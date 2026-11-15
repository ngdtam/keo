"""JSON API endpoints for the SPA views."""

from __future__ import annotations

import hmac
from dataclasses import asdict, is_dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel

from keo.catalog import CatalogError
from keo.recycle import list_recycle
from keo.web.auth import COOKIE_MAX_AGE, COOKIE_NAME, session_value
from keo.web.deps import _sidebar_status, get_services, require_api, song_json
from keo.web.routes_home import _dashboard_stats
from keo.web.routes_library import (
    _format_bytes,
    _library_page_data,
    _plan_organize,
    _song_view,
)
from keo.web.routes_queue import _activity_events, _review_candidates
from keo.web.routes_settings import _context

router = APIRouter()
API = {"dependencies": [Depends(require_api)]}


class LoginRequest(BaseModel):
    token: str


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


@router.post("/api/auth/login")
def api_login(request: Request, body: LoginRequest, response: Response):
    expected = get_services(request).token
    if not hmac.compare_digest(body.token.encode(), expected.encode()):
        raise HTTPException(status_code=401, detail="Invalid token")
    response.set_cookie(
        COOKIE_NAME,
        session_value(expected),
        max_age=COOKIE_MAX_AGE,
        httponly=True,
        samesite="lax",
    )
    return {"ok": True}


@router.post("/api/auth/logout")
def api_logout(response: Response):
    response.delete_cookie(COOKIE_NAME)
    return {"ok": True}


@router.get("/api/dashboard", **API)
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
        "nav_settings_warn": sidebar["nav_settings_warn"],
    }


@router.get("/api/library", **API)
def api_library(request: Request, filter: str = "all", q: str = "", sort: str = "artist"):
    data = _library_page_data(get_services(request), filter=filter, q=q, sort=sort)
    data["rows"] = [vars(r) for r in data["rows"]]
    return data


@router.get("/api/library/organize/preview", **API)
def api_library_organize_preview(request: Request):
    services = get_services(request)
    return {
        "template": services.spotdl.settings.output_template,
        "moves": _plan_organize(services),
    }


@router.get("/api/library/trash", **API)
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


@router.get("/api/library/{kind}/{ident}", **API)
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


@router.get("/api/review/full", **API)
def api_review_full(request: Request):
    services = get_services(request)
    lib = services.library
    return {
        "review": [asdict(r) for r in lib.list_review()],
        "watched_playlists": [asdict(p) for p in lib.list_watched_playlists()],
        "followed_artists": [asdict(a) for a in lib.list_followed_artists()],
        "history_progress": services.history_progress.snapshot(),
    }


@router.get("/api/review/{video_id}/candidates", **API)
def api_review_candidates(request: Request, video_id: str, q: str = ""):
    row, query, candidates, error = _review_candidates(get_services(request), video_id, q=q)
    return {
        "item": asdict(row),
        "query": query,
        "candidates": [song_json(c) for c in candidates],
        "error": error,
    }


@router.get("/api/activity", **API)
def api_activity(request: Request):
    services = get_services(request)
    return {
        "jobs": [asdict(job) for job in services.library.list_jobs()],
        "history": _activity_events(services),
    }


@router.get("/api/settings", **API)
def api_settings(request: Request):
    services = get_services(request)
    ctx = _json_safe(_context(request))
    ctx["recycle_days"] = services.recycle_days
    ctx["config_writable"] = services.config_path is not None
    return ctx
