"""JSON API endpoints for the SPA views."""

from __future__ import annotations

from dataclasses import asdict, is_dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field

from keo.catalog import CatalogError
from keo.history import sync_followed_artists, sync_watched_playlists
from keo.recycle import list_recycle
from keo.system import _pkg_version, run_health_checks
from keo.web.auth import (
    COOKIE_MAX_AGE,
    COOKIE_NAME,
    ensure_services_auth_user,
    session_value,
)
from keo.web.deps import _sidebar_status, get_services, require_api, song_json
from keo.web.routes_library import (
    _bulk_library_items,
    _collect_rows,
    _delete_song_item,
    _delete_trash_item,
    _empty_trash,
    _format_bytes,
    _library_page_data,
    _organize_library,
    _plan_organize,
    _rescan_library,
    _restore_trash_item,
    _song_cover_response,
    _song_view,
)
from keo.web.routes_queue import (
    _activity_events,
    _follow_artist,
    _review_candidates,
    _watch_playlist,
)
from keo.web.routes_settings import _context, _run_scheduled_task

router = APIRouter()
API = {"dependencies": [Depends(require_api)]}


class LoginRequest(BaseModel):
    username: str = "admin"
    password: str = ""
    token: str = ""
    remember: bool = True


class CredentialsRequest(BaseModel):
    username: str
    password: str
    password_confirmation: str


class BulkLibraryRequest(BaseModel):
    action: str
    items: list[str] = Field(default_factory=list)


class TrashItemRequest(BaseModel):
    name: str


class WatchPlaylistRequest(BaseModel):
    playlist_id: str
    auto_queue: bool = False


class FollowArtistRequest(BaseModel):
    artist: str


def _json_safe(value: Any) -> Any:
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if is_dataclass(value) and not isinstance(value, type):
        return _json_safe(asdict(value))
    if isinstance(value, dict):
        return {k: _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(v) for v in value]
    return value


def _set_session_cookie(response: Response, token: str, user, *, remember: bool = True) -> None:
    response.set_cookie(
        COOKIE_NAME,
        session_value(token, user),
        max_age=COOKIE_MAX_AGE if remember else None,
        httponly=True,
        samesite="lax",
    )


@router.post("/api/auth/login", tags=["Auth"])
def api_login(request: Request, body: LoginRequest, response: Response):
    services = get_services(request)
    ensure_services_auth_user(services)
    secret = body.password or body.token
    matched = services.library.verify_user(body.username, secret)
    if matched is None:
        detail = (
            "Invalid token"
            if (body.token and not body.password)
            else "Invalid username or password"
        )
        raise HTTPException(status_code=401, detail=detail)
    _set_session_cookie(response, services.token, matched, remember=body.remember)
    return {"ok": True, "username": matched.username}


@router.post("/api/auth/logout", tags=["Auth"])
def api_logout(response: Response):
    response.delete_cookie(COOKIE_NAME)
    return {"ok": True}


@router.post("/api/auth/credentials", **API, tags=["Auth"])
def api_update_credentials(request: Request, body: CredentialsRequest, response: Response):
    username = body.username.strip()
    if not username:
        raise HTTPException(status_code=400, detail="Username is required")
    if not body.password.strip():
        raise HTTPException(status_code=400, detail="Password is required")
    if body.password != body.password_confirmation:
        raise HTTPException(status_code=400, detail="Passwords do not match")

    services = get_services(request)
    updated = services.library.update_credentials(username, body.password)
    _set_session_cookie(response, services.token, updated, remember=True)
    return {"ok": True, "username": updated.username}


@router.post("/api/auth/api-key/reset", **API, tags=["Auth"])
def api_reset_api_key(request: Request):
    services = get_services(request)
    new_key = services.library.reset_api_key()
    services.library.record_event("API key", "reset")
    return {"api_key": new_key}


@router.get("/api/system/status", **API, tags=["System"])
def api_system_status(request: Request):
    services = get_services(request)
    checks = run_health_checks(services)
    return {
        "app": "Kéo",
        "version": _pkg_version("keo"),
        "library_count": len(_collect_rows(services)),
        "active_jobs": services.library.count_active_jobs(),
        "review_count": len(services.library.list_review()),
        "health": "warn" if any(lvl != "ok" for lvl, _ in checks) else "ok",
    }


def _dashboard_stats(services) -> dict:
    jobs = services.library.list_jobs()
    seen: set[str] = set()
    attention = []
    for job in jobs:
        if job.status not in {"queued", "running", "failed"}:
            continue
        href = (
            f"/library/spotify/{job.spotify_id}" if job.spotify_id
            else f"/library/video/{job.video_id}"
        )
        if href in seen:
            continue
        seen.add(href)
        attention.append({"href": href, "artist": job.artist, "title": job.title})
    all_rows = _collect_rows(services)
    library_size = _format_bytes(sum(r.size for r in all_rows if r.size))
    missing_count = sum(1 for r in all_rows if not r.path and r.label not in {"Queued", "Downloading"})
    return {
        "review_count": len(services.library.list_review()),
        "library_count": len(all_rows),
        "library_size": library_size,
        "missing_count": missing_count,
        "active_count": sum(job.status in {"queued", "running"} for job in jobs),
        "failed_count": sum(job.status == "failed" for job in jobs),
        "attention": attention,
    }


@router.get("/api/dashboard", **API, tags=["System"])
def api_dashboard(request: Request):
    services = get_services(request)
    stats = _dashboard_stats(services)
    ctx = _context(request)
    sidebar = _sidebar_status(request)
    return {
        **stats,
        "auth_error": ctx["auth_error"],
        "gaps": list(ctx["gaps"]),
        "warnings": ctx["warnings"],
        "nav_review_count": sidebar["nav_review_count"],
        "nav_active_jobs_count": sidebar["nav_active_jobs_count"],
        "nav_settings_warn": sidebar["nav_settings_warn"],
    }


@router.get("/api/library", **API, tags=["Library"])
def api_library(request: Request, filter: str = "all", q: str = "", sort: str = "artist"):
    data = _library_page_data(get_services(request), filter=filter, q=q, sort=sort)
    data["rows"] = [vars(r) for r in data["rows"]]
    return data


@router.get("/api/library/organize/preview", **API, tags=["Library"])
def api_library_organize_preview(request: Request):
    services = get_services(request)
    return {
        "template": services.spotdl.settings.output_template,
        "moves": _plan_organize(services),
    }


@router.post("/api/library/rescan", **API, tags=["Library"])
def api_library_rescan(request: Request):
    total_count = _rescan_library(get_services(request))
    return {"ok": True, "total_count": total_count}


@router.post("/api/library/organize", **API, tags=["Library"])
def api_library_organize(request: Request):
    moved = _organize_library(get_services(request))
    return {"ok": True, "moved": moved}


@router.post("/api/library/bulk", **API, tags=["Library"])
def api_library_bulk(request: Request, body: BulkLibraryRequest):
    _bulk_library_items(get_services(request), body.action, body.items)
    return {"ok": True, "action": body.action}


@router.get("/api/library/trash", **API, tags=["Library"])
def api_library_trash(request: Request):
    services = get_services(request)
    items = list_recycle(services.music_dir) if services.music_dir is not None else []
    return {
        "recycle_days": services.recycle_days,
        "items": [
            {
                "name": i.name,
                "size": i.size,
                "size_label": _format_bytes(i.size),
                "deleted_at": i.deleted_at,
            }
            for i in items
        ],
    }


@router.post("/api/library/trash/restore", **API, tags=["Library"])
def api_library_trash_restore(request: Request, body: TrashItemRequest):
    _restore_trash_item(get_services(request), body.name)
    return {"ok": True}


@router.delete("/api/library/trash", **API, tags=["Library"])
def api_library_trash_empty(request: Request):
    _empty_trash(get_services(request))
    return {"ok": True}


@router.delete("/api/library/trash/{name:path}", **API, tags=["Library"])
def api_library_trash_delete(request: Request, name: str):
    _delete_trash_item(get_services(request), name)
    return {"ok": True}


@router.get("/api/library/{kind}/{ident}/cover", **API, tags=["Library"])
def api_library_cover(request: Request, kind: str, ident: str):
    return _song_cover_response(get_services(request), kind, ident)


@router.get("/api/library/{kind}/{ident}", **API, tags=["Library"])
def api_library_detail(request: Request, kind: str, ident: str, q: str = ""):
    services = get_services(request)
    row, job_lines = _song_view(services, kind, ident)
    candidates = []
    if q.strip():
        try:
            candidates = [song_json(c) for c in services.catalog.search(q.strip())[:5]]
        except CatalogError:
            candidates = []
    return {
        "song": vars(row),
        "jobs": [vars(j) for j in job_lines],
        "candidates": candidates,
    }


@router.delete("/api/library/{kind}/{ident}", **API, tags=["Library"])
def api_library_delete(request: Request, kind: str, ident: str, scope: str = "file"):
    if scope not in ("", "file", "library"):
        raise HTTPException(status_code=400, detail="Invalid scope")
    _delete_song_item(get_services(request), kind, ident, scope=scope)
    return {"ok": True, "deleted": ident, "scope": scope or "file"}


@router.get("/api/review/full", **API, tags=["Queue"])
def api_review_full(request: Request):
    services = get_services(request)
    lib = services.library
    return {
        "review": [asdict(r) for r in lib.list_review()],
        "watched_playlists": [asdict(p) for p in lib.list_watched_playlists()],
        "followed_artists": [asdict(a) for a in lib.list_followed_artists()],
        "history_progress": services.history_progress.snapshot(),
    }


@router.get("/api/review/{video_id}/candidates", **API, tags=["Queue"])
def api_review_candidates(request: Request, video_id: str, q: str = ""):
    row, query, candidates, error = _review_candidates(get_services(request), video_id, q=q)
    return {
        "item": asdict(row),
        "query": query,
        "candidates": [song_json(c) for c in candidates],
        "error": error,
    }


@router.get("/api/activity", **API, tags=["Queue"])
def api_activity(request: Request):
    services = get_services(request)
    return {
        "jobs": [asdict(job) for job in services.library.list_jobs()],
        "history": _activity_events(services),
    }


@router.post("/api/review/playlists", **API, tags=["Review & Discovery"])
def api_review_watch_playlist(request: Request, body: WatchPlaylistRequest):
    pid, title = _watch_playlist(
        get_services(request), body.playlist_id, auto_queue=body.auto_queue
    )
    return {"ok": True, "playlist_id": pid, "title": title}


@router.post("/api/review/playlists/sync", **API, tags=["Review & Discovery"])
def api_review_sync_playlists(request: Request):
    services = get_services(request)
    sync_watched_playlists(
        catalog=services.catalog, library=services.library, worker=services.worker
    )
    return {"ok": True}


@router.delete("/api/review/playlists/{playlist_id}", **API, tags=["Review & Discovery"])
def api_review_unwatch_playlist(request: Request, playlist_id: str):
    get_services(request).library.unwatch_playlist(playlist_id)
    return {"ok": True}


@router.post("/api/review/artists", **API, tags=["Review & Discovery"])
def api_review_follow_artist(request: Request, body: FollowArtistRequest):
    cid, name = _follow_artist(get_services(request), body.artist)
    return {"ok": True, "channel_id": cid, "name": name}


@router.post("/api/review/artists/sync", **API, tags=["Review & Discovery"])
def api_review_sync_artists(request: Request):
    services = get_services(request)
    sync_followed_artists(catalog=services.catalog, library=services.library)
    return {"ok": True}


@router.delete("/api/review/artists/{channel_id}", **API, tags=["Review & Discovery"])
def api_review_unfollow_artist(request: Request, channel_id: str):
    get_services(request).library.unfollow_artist(channel_id)
    return {"ok": True}


@router.get("/api/settings", **API, tags=["Settings"])
def api_settings(request: Request):
    services = get_services(request)
    user = ensure_services_auth_user(services)
    ctx = _json_safe(_context(request))
    ctx["recycle_days"] = services.recycle_days
    ctx["config_writable"] = services.config_path is not None
    ctx["username"] = user.username
    ctx["api_key"] = user.api_key
    return ctx


@router.post("/api/settings/tasks/{name}/run", **API, tags=["Settings & Tasks"])
def api_run_task(request: Request, name: str):
    _run_scheduled_task(get_services(request), name)
    return {"ok": True, "task": name}
