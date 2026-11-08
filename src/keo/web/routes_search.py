"""Search YouTube Music songs."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse

from keo.web.deps import TEMPLATES, get_services, require_api, require_page, song_json

router = APIRouter()


@router.get("/search", response_class=HTMLResponse, dependencies=[Depends(require_page)])
def search_page(request: Request, q: str = ""):
    songs = get_services(request).catalog.search(q) if q.strip() else []
    return TEMPLATES.TemplateResponse(request, "search.html", {"q": q, "songs": songs})


@router.get("/api/search", dependencies=[Depends(require_api)])
def api_search(request: Request, q: str):
    return [song_json(song) for song in get_services(request).catalog.search(q)]


@router.get("/api/suggestions", dependencies=[Depends(require_api)])
def api_suggestions(request: Request, q: str):
    return get_services(request).catalog.suggestions(q)
