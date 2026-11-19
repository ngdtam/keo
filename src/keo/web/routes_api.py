"""JSON API endpoints for the SPA views."""

from __future__ import annotations

from dataclasses import asdict, is_dataclass
from datetime import date, datetime
import json
import os
from pathlib import Path
import tempfile
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field
import ytmusicapi
from ytmusicapi.exceptions import YTMusicError

from keo.catalog import CatalogError, open_ytmusic
from keo.history import sync_followed_artists, sync_watched_playlists
from keo.library import SetupAlreadyCompleteError
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
_YTM_AUTH_MAX_BYTES = 1_048_576


class LoginRequest(BaseModel):
    username: str = "admin"
    password: str = ""
    token: str = ""
    remember: bool = True


class CredentialsRequest(BaseModel):
    username: str
    password: str
    password_confirmation: str


class SetupRequest(CredentialsRequest):
    remember: bool = True


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


def _validate_credentials(body: CredentialsRequest) -> tuple[str, str]:
    username = body.username.strip()
    if not username:
        raise HTTPException(status_code=400, detail="Username is required")
    if not body.password.strip():
        raise HTTPException(status_code=400, detail="Password is required")
    if body.password != body.password_confirmation:
        raise HTTPException(status_code=400, detail="Passwords do not match")
    return username, body.password


@router.get("/api/auth/status", tags=["Auth"])
def api_auth_status(request: Request):
    services = get_services(request)
    return {
        "needs_setup": services.library.needs_setup(),
        "ytm_configured": services.catalog.is_configured(),
    }


@router.post("/api/auth/setup", tags=["Auth"])
def api_setup(request: Request, body: SetupRequest, response: Response):
    services = get_services(request)
    if not services.library.needs_setup():
        raise HTTPException(status_code=409, detail="Setup is already complete")
    username, password = _validate_credentials(body)
    try:
        user = services.library.create_initial_user(username, password)
    except SetupAlreadyCompleteError as exc:
        raise HTTPException(status_code=409, detail="Setup is already complete") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    _set_session_cookie(response, services.token, user, remember=body.remember)
    return {"ok": True, "username": user.username, "api_key": user.api_key}


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
    username, password = _validate_credentials(body)
    services = get_services(request)
    updated = services.library.update_credentials(username, password)
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
def api_library(
    request: Request,
    filter: str = "all",
    q: str = "",
    sort: str = "artist",
    artist: str = "",
):
    data = _library_page_data(
        get_services(request), filter=filter, q=q, sort=sort, artist=artist
    )
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


async def _parse_ytm_auth_json(request: Request) -> str:
    content_type = request.headers.get("content-type", "").lower()
    headers_raw = ""
    auth_file: Any = None

    if "application/json" in content_type:
        try:
            payload = await request.json()
        except Exception as exc:
            raise HTTPException(status_code=400, detail="Invalid JSON body") from exc
        if not isinstance(payload, dict):
            raise HTTPException(status_code=400, detail="JSON body must be an object")
        raw_val = payload.get("headers_raw")
        if raw_val is not None and not isinstance(raw_val, str):
            raise HTTPException(status_code=400, detail="headers_raw must be a string")
        headers_raw = raw_val or ""
    elif "multipart/form-data" in content_type or "application/x-www-form-urlencoded" in content_type:
        form = await request.form()
        raw_val = form.get("headers_raw")
        headers_raw = raw_val if isinstance(raw_val, str) else ""
        file_val = form.get("auth_file")
        auth_file = file_val if hasattr(file_val, "read") else None
    else:
        raise HTTPException(status_code=400, detail="Provide headers_raw or auth_file")

    if headers_raw.strip():
        try:
            return ytmusicapi.setup(headers_raw=headers_raw)
        except (YTMusicError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=f"Invalid YouTube Music headers: {exc}") from exc

    if auth_file is not None:
        raw = await auth_file.read(_YTM_AUTH_MAX_BYTES + 1)
        if len(raw) > _YTM_AUTH_MAX_BYTES:
            raise HTTPException(status_code=400, detail="Auth file is too large")
        if not raw.strip():
            raise HTTPException(status_code=400, detail="Auth file is empty")
        try:
            parsed = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=f"Invalid JSON auth file: {exc}") from exc
        if not isinstance(parsed, dict) or not parsed:
            raise HTTPException(status_code=400, detail="Auth file must be a non-empty JSON object")
        return json.dumps(parsed, indent=2)

    raise HTTPException(status_code=400, detail="Provide headers_raw or auth_file")


@router.post("/api/settings/ytm", **API, tags=["Settings"])
async def api_settings_ytm(request: Request):
    services = get_services(request)
    if services.data_dir is None:
        raise HTTPException(status_code=400, detail="Kéo has no data directory.")
    auth_json = await _parse_ytm_auth_json(request)
    parsed = json.loads(auth_json)
    if "refresh_token" in parsed and not (services.ytm_client_id and services.ytm_client_secret):
        raise HTTPException(
            status_code=400,
            detail="OAuth auth requires KEO_YTM_CLIENT_ID and KEO_YTM_CLIENT_SECRET.",
        )
    dest = services.data_dir / "browser.json"
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=dest.parent,
            prefix=".browser-",
            suffix=".tmp",
            delete=False,
        ) as tmp_file:
            tmp_file.write(auth_json)
            tmp_path = Path(tmp_file.name)
        ytm = open_ytmusic(tmp_path, services.ytm_client_id, services.ytm_client_secret)
        os.replace(tmp_path, dest)
        tmp_path = None
        if "refresh_token" in parsed:
            ytm = open_ytmusic(dest, services.ytm_client_id, services.ytm_client_secret)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Invalid YouTube Music auth: {exc}") from exc
    finally:
        if tmp_path is not None:
            tmp_path.unlink(missing_ok=True)
    services.catalog.use(ytm)
    services.ytm_auth = dest
    return {"ok": True, "ytm_configured": True}


@router.post("/api/settings/tasks/{name}/run", **API, tags=["Settings & Tasks"])
def api_run_task(request: Request, name: str):
    _run_scheduled_task(get_services(request), name)
    return {"ok": True, "task": name}
