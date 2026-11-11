"""Start downloads, work the review list, and watch jobs."""

from __future__ import annotations

import logging
from dataclasses import asdict
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from pydantic import BaseModel

from keo.history import decide, run_pull
from keo.library import Job
from keo.web.deps import TEMPLATES, get_services, require_api, require_download, require_page

log = logging.getLogger(__name__)
router = APIRouter()
PAGE = {"response_class": HTMLResponse, "dependencies": [Depends(require_page)]}
API = {"dependencies": [Depends(require_api)]}


class DownloadRequest(BaseModel):
    video_id: str
    title: str
    artists: list[str]


def format_job_time(updated_at: str, zone: str) -> str:
    instant = datetime.fromisoformat(updated_at)
    if instant.tzinfo is None:
        instant = instant.replace(tzinfo=timezone.utc)
    return instant.astimezone(ZoneInfo(zone)).strftime("%Y-%m-%d, %H:%M")


def _detail(job: Job) -> str:
    if job.format is None:
        return ""
    premium = "Premium" if job.premium else "not Premium"
    return f" · {job.format} · .{job.format} · {premium}"


def queued(job: Job | None) -> dict:
    return {"owned": job is None, "job": asdict(job) if job else None}


@router.post("/api/downloads", **API)
def api_download(request: Request, body: DownloadRequest):
    services = get_services(request)
    require_download(services)
    artist = body.artists[0] if body.artists else ""
    return queued(services.worker.submit(body.video_id, artist, body.title))


@router.get("/review", **PAGE)
def review_page(request: Request):
    rows = get_services(request).library.list_review()
    return TEMPLATES.TemplateResponse(request, "review.html", {"rows": rows})


@router.get("/api/review", **API)
def api_review(request: Request):
    return [asdict(row) for row in get_services(request).library.list_review()]


@router.post("/api/review/{video_id}/dismiss", **API)
def api_review_dismiss(request: Request, video_id: str):
    services = get_services(request)
    services.library.dismiss(video_id)
    return JSONResponse({"status": "dismissed"})


@router.post("/api/history/pull", **API)
def api_history_pull(request: Request):
    log.info("Manual history pull requested via API")
    services = get_services(request)
    now = datetime.now(ZoneInfo(services.timezone))
    services.run_later(
        lambda: run_pull(
            catalog=services.catalog,
            library=services.library,
            worker=services.worker,
            spotdl=services.spotdl,
            cookies=services.cookies,
            now=now,
            progress=services.history_progress,
        )
    )
    return JSONResponse({"status": "checking"}, status_code=202)


@router.get("/api/history/status", **API)
def api_history_status(request: Request):
    return JSONResponse(get_services(request).history_progress.snapshot())


@router.post("/api/review/{video_id}/download", **API)
def api_review_download(request: Request, video_id: str):
    services = get_services(request)
    row = services.library.get_review(video_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Not on the review list")
    require_download(services)
    if row.spotify_url:
        job = services.worker.submit(
            row.video_id, row.heard_artist, row.heard_title,
            source="review", spotify_url=row.spotify_url, spotify_id=row.spotify_id,
        )
        return queued(job)
    services.run_later(
        lambda: decide(
            row.video_id, row.heard_artist, row.heard_title,
            library=services.library, worker=services.worker, spotdl=services.spotdl,
            cookies=services.cookies,
            source="review", replace_review=True,
        )
    )
    return JSONResponse({"status": "checking"}, status_code=202)


@router.get("/jobs", **PAGE)
def jobs_page(request: Request):
    return RedirectResponse("/library", status_code=303)


@router.get("/api/jobs", **API)
def api_jobs(request: Request):
    return [asdict(job) for job in get_services(request).library.list_jobs()]


@router.post("/api/jobs/{job_id}/retry", **API)
def api_retry(request: Request, job_id: int):
    services = get_services(request)
    if services.library.get_job(job_id) is None:
        raise HTTPException(status_code=404, detail="Unknown job")
    require_download(services)
    job = services.worker.retry(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Unknown job")
    return queued(job)
