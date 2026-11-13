"""Start Kéo: read settings, open the library, and serve the app.

Run with: uvicorn --factory keo.main:build_app
"""

from __future__ import annotations

import logging
import os
import sys
from contextlib import asynccontextmanager

from fastapi import FastAPI

from keo.catalog import Catalog, open_ytmusic
from keo.config import ConfigError, load_settings
from keo.cookies import CookieGate
from keo.history import Scheduler
from keo.jobs import Worker
from keo.library import Library
from keo.notify import send_notification, trigger_media_refresh
from keo.spotdl_cmd import Paths, Spotdl
from keo.web import Services, create_app


def build_app() -> FastAPI:
    logging.basicConfig(level=logging.INFO)
    try:
        settings = load_settings(os.environ)
    except ConfigError as exc:
        sys.exit(f"Kéo cannot start: {exc}")
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    paths = Paths(settings.music_dir, settings.data_dir)
    library = Library(settings.data_dir / "keo.db", paths.archive)
    library.requeue_running()
    spotdl = Spotdl(settings.spotdl, paths)
    cookies = CookieGate()
    services_ref: list[Services] = []

    def _on_success(_job) -> None:
        if services_ref and services_ref[0].media_refresh_url:
            trigger_media_refresh(services_ref[0].media_refresh_url)

    def _on_failure(job, error: str) -> None:
        if services_ref and services_ref[0].notify_url:
            send_notification(
                services_ref[0].notify_url,
                "Kéo: download failed",
                f"{job.artist} - {job.title}: {error[:200]}",
            )

    worker = Worker(
        library, spotdl, settings.music_dir, cookies=cookies,
        on_success=_on_success, on_failure=_on_failure,
    )
    try:
        ytm = open_ytmusic(settings.ytm_auth, settings.ytm_client_id, settings.ytm_client_secret)
    except Exception as exc:
        sys.exit(f"Kéo cannot start: KEO_YTM_AUTH could not be loaded: {exc}")
    services = Services(
        token=settings.token,
        catalog=Catalog(ytm),
        library=library,
        worker=worker,
        spotdl=spotdl,
        cookies=cookies,
        config_path=settings.config_path,
        ytm_auth=settings.ytm_auth,
        ytm_client_id=settings.ytm_client_id,
        ytm_client_secret=settings.ytm_client_secret,
        timezone=settings.history_timezone,
        data_dir=settings.data_dir,
        music_dir=settings.music_dir,
        recycle_days=settings.recycle_days,
        notify_url=settings.notify_url,
        media_refresh_url=settings.media_refresh_url,
    )
    services_ref.append(services)

    scheduler = Scheduler(
        catalog=services.catalog,
        library=library,
        worker=worker,
        spotdl=spotdl,
        cookies=cookies,
        timezone=settings.history_timezone,
        hour=settings.history_hour,
        progress=services.history_progress,
        music_dir=settings.music_dir,
        recycle_days=settings.recycle_days,
        data_dir=settings.data_dir,
        config_path=settings.config_path,
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        worker.start()
        scheduler.start()
        yield
        scheduler.stop()
        worker.stop()

    return create_app(services, lifespan=lifespan)
