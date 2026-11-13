"""Start downloads, work the review list, and watch jobs."""

from __future__ import annotations

import logging
from dataclasses import asdict
from datetime import datetime, timezone
from typing import Annotated
from urllib.parse import parse_qs, urlparse
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from pydantic import BaseModel

from keo.catalog import CatalogError
from keo.history import decide, run_pull, sync_followed_artists, sync_watched_playlists
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
    lib = get_services(request).library
    return TEMPLATES.TemplateResponse(
        request,
        "review.html",
        {
            "rows": lib.list_review(),
            "watched": lib.list_watched_playlists(),
            "artists": lib.list_followed_artists(),
        },
    )


@router.get("/review/{video_id}/pick", **PAGE)
def review_pick_page(request: Request, video_id: str, q: str = ""):
    services = get_services(request)
    row = services.library.get_review(video_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Unknown review song")
    query = q.strip() or f"{row.heard_artist} {row.heard_title}".strip()
    candidates = []
    error = ""
    if query:
        try:
            candidates = services.catalog.search(query)[:5]
        except CatalogError as exc:
            error = str(exc)
    return TEMPLATES.TemplateResponse(
        request,
        "review_pick.html",
        {"row": row, "query": query, "candidates": candidates, "error": error},
    )


@router.post("/review/{video_id}/pick", **PAGE)
def review_pick_submit(
    request: Request,
    video_id: str,
    chosen_video_id: Annotated[str, Form()],
    artist: Annotated[str, Form()],
    title: Annotated[str, Form()],
):
    services = get_services(request)
    require_download(services)
    services.worker.submit(chosen_video_id.strip(), artist.strip(), title.strip(), source="review")
    services.library.dismiss(video_id)
    return RedirectResponse("/review", status_code=303)


def _extract_playlist_id(raw: str) -> str:
    value = raw.strip()
    if "://" in value or "list=" in value:
        query = parse_qs(urlparse(value).query)
        if query.get("list"):
            return query["list"][0].strip()
    return value


@router.post("/review/playlists/watch", **PAGE)
def watch_playlist_route(
    request: Request,
    playlist_id: Annotated[str, Form()],
    auto_queue: Annotated[bool, Form()] = False,
):
    services = get_services(request)
    pid = _extract_playlist_id(playlist_id)
    if not pid:
        return RedirectResponse("/review", status_code=303)
    title = "Liked Music" if pid in {"LM", "liked"} else pid
    try:
        fetched_title, _ = services.catalog.playlist(pid)
        if fetched_title:
            title = fetched_title
    except CatalogError:
        pass
    services.library.watch_playlist(pid, title, auto_queue=auto_queue)
    return RedirectResponse("/review", status_code=303)


@router.post("/review/playlists/sync", **PAGE)
def sync_watched_playlists_route(request: Request):
    services = get_services(request)
    sync_watched_playlists(
        catalog=services.catalog, library=services.library, worker=services.worker
    )
    return RedirectResponse("/review", status_code=303)


@router.post("/review/playlists/{playlist_id}/unwatch", **PAGE)
def unwatch_playlist_route(request: Request, playlist_id: str):
    get_services(request).library.unwatch_playlist(playlist_id)
    return RedirectResponse("/review", status_code=303)


def _extract_channel_id(raw: str) -> str | None:
    value = raw.strip()
    if "/channel/" in value:
        part = value.split("/channel/", 1)[1].split("/", 1)[0].split("?", 1)[0].strip()
        if part:
            return part
    if value.startswith("UC") and " " not in value and len(value) >= 16:
        return value
    return None


@router.post("/review/artists/follow", **PAGE)
def follow_artist_route(request: Request, artist: Annotated[str, Form()]):
    services = get_services(request)
    query = artist.strip()
    if not query:
        return RedirectResponse("/review", status_code=303)
    cid = _extract_channel_id(query)
    name = query
    try:
        if cid is None:
            found = services.catalog.find_artist(query)
            if found is None:
                return RedirectResponse("/review", status_code=303)
            cid, name = found
        else:
            fetched_name, _ = services.catalog.artist_releases(cid)
            if fetched_name:
                name = fetched_name
    except CatalogError:
        if cid is None:
            return RedirectResponse("/review", status_code=303)
    services.library.follow_artist(cid, name)
    return RedirectResponse("/review", status_code=303)


@router.post("/review/artists/sync", **PAGE)
def sync_followed_artists_route(request: Request):
    services = get_services(request)
    sync_followed_artists(catalog=services.catalog, library=services.library)
    return RedirectResponse("/review", status_code=303)


@router.post("/review/artists/{channel_id}/unfollow", **PAGE)
def unfollow_artist_route(request: Request, channel_id: str):
    get_services(request).library.unfollow_artist(channel_id)
    return RedirectResponse("/review", status_code=303)


@router.get("/activity", **PAGE)
def activity_page(request: Request):
    services = get_services(request)
    events = [
        {
            "time": format_job_time(ev.created_at, services.timezone),
            "song": ev.song,
            "kind": ev.kind,
            "detail": ev.detail,
        }
        for ev in services.library.list_events()
    ]
    return TEMPLATES.TemplateResponse(request, "activity.html", {"events": events})


@router.get("/api/review", **API)
def api_review(request: Request):
    return [asdict(row) for row in get_services(request).library.list_review()]


@router.post("/api/review/{video_id}/dismiss", **API)
def api_review_dismiss(request: Request, video_id: str, permanent: bool = False):
    services = get_services(request)
    services.library.dismiss(video_id, permanent=permanent)
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
    job = services.worker.submit(
        row.video_id,
        row.heard_artist,
        row.heard_title,
        source="review",
        spotify_url=row.spotify_url,
        spotify_id=row.spotify_id,
    )
    return queued(job)


@router.post("/api/review/{video_id}/download-youtube", **API)
def api_review_download_youtube(request: Request, video_id: str):
    services = get_services(request)
    row = services.library.get_review(video_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Not on the review list")
    job = services.worker.submit(
        row.video_id,
        row.heard_artist,
        row.heard_title,
        source="youtube",
    )
    return queued(job)



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
