"""The daily YouTube Music history check.

A song played today downloads on its own only when the Spotify match is close.
Anything else waits on the review list for a tap.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from datetime import date, datetime
from zoneinfo import ZoneInfo

from keo.catalog import Catalog, CatalogError
from keo.cookies import CookieGate
from keo.jobs import Worker
from keo.library import Library
from keo.match import Name, is_close
from keo.spotdl_cmd import Spotdl

log = logging.getLogger(__name__)

PLAYS_TO_QUALIFY = 1
CHECK_EVERY_SECONDS = 60


def decide(
    video_id: str,
    artist: str,
    title: str,
    *,
    library: Library,
    worker: Worker,
    spotdl: Spotdl,
    cookies: CookieGate,
    source: str = "history",
    replace_review: bool = False,
) -> str:
    candidate = spotdl.lookup(f"{artist} - {title}").candidate
    if candidate is None:
        library.add_review(video_id, artist, title, reason="no_metadata", replace=replace_review)
        return "review"
    if library.is_owned_spotify(candidate.song_id):
        library.mark_owned(video_id, artist, title, candidate.song_id)
        return "owned"
    if is_close(Name(artist, title), Name(candidate.primary_artist, candidate.name)):
        reason = cookies.allow(spotdl.settings)
        if reason is not None:
            log.warning("%s", reason)
            return "cookie"
        worker.submit(
            video_id, artist, title,
            source=source, spotify_url=candidate.url, spotify_id=candidate.song_id,
        )
        return "queued"
    library.add_review(
        video_id, artist, title,
        reason="weak_match",
        candidate_artist=candidate.primary_artist,
        candidate_title=candidate.name,
        spotify_url=candidate.url,
        spotify_id=candidate.song_id,
        replace=replace_review,
    )
    return "review"


def run_pull(
    *,
    catalog: Catalog,
    library: Library,
    worker: Worker,
    spotdl: Spotdl,
    cookies: CookieGate,
    now: datetime,
) -> int | None:
    started_at = now.isoformat(timespec="seconds")
    try:
        items = catalog.history()
    except CatalogError as exc:
        log.warning("History pull failed: %s", exc)
        library.record_pull(started_at, "failed", str(exc), [])
        return None
    pull_id = library.record_pull(started_at, "ok", None, items)
    for heard in library.count_today(pull_id):
        if heard.plays < PLAYS_TO_QUALIFY:
            continue
        if library.is_owned(heard.video_id, heard.artist, heard.title):
            continue
        if library.active_job(heard.video_id) is not None:
            continue
        try:
            decide(
                heard.video_id, heard.artist, heard.title,
                library=library, worker=worker, spotdl=spotdl, cookies=cookies,
            )
        except Exception:
            log.exception("History decision failed for %s", heard.video_id)
    return pull_id


def is_due(now: datetime, hour: int, library: Library, last_attempt: date | None) -> bool:
    today = now.date()
    return (
        now.hour >= hour
        and last_attempt != today
        and not library.has_ok_pull_on(today.isoformat())
    )


class Scheduler:
    def __init__(
        self,
        *,
        catalog: Catalog,
        library: Library,
        worker: Worker,
        spotdl: Spotdl,
        cookies: CookieGate,
        timezone: str,
        hour: int,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        zone = ZoneInfo(timezone)
        self._catalog = catalog
        self._library = library
        self._worker = worker
        self._spotdl = spotdl
        self._cookies = cookies
        self._hour = hour
        self._clock = clock or (lambda: datetime.now(zone))
        self._last_attempt: date | None = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def tick(self) -> bool:
        now = self._clock()
        if not is_due(now, self._hour, self._library, self._last_attempt):
            return False
        self._last_attempt = now.date()
        run_pull(
            catalog=self._catalog, library=self._library,
            worker=self._worker, spotdl=self._spotdl, cookies=self._cookies, now=now,
        )
        return True

    def start(self) -> None:
        self._thread = threading.Thread(target=self._loop, name="keo-history", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5)

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self.tick()
            except Exception:
                log.exception("History check crashed")
            self._stop.wait(CHECK_EVERY_SECONDS)
