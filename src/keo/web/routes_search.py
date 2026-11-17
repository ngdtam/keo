"""Search YouTube Music songs."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from keo.web.deps import get_services, require_api, song_json

router = APIRouter(tags=["Search"])


@router.get("/api/search", dependencies=[Depends(require_api)])
def api_search(request: Request, q: str):
    return [song_json(song) for song in get_services(request).catalog.search(q)]


@router.get("/api/suggestions", dependencies=[Depends(require_api)])
def api_suggestions(request: Request, q: str):
    return get_services(request).catalog.suggestions(q)
