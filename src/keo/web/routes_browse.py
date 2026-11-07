"""Browse Vietnam charts, moods, playlists, and radio."""

from __future__ import annotations

from dataclasses import asdict

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse

from keo.web.deps import TEMPLATES, get_services, require_api, require_page, song_json

router = APIRouter()
PAGE = {"response_class": HTMLResponse, "dependencies": [Depends(require_page)]}
API = {"dependencies": [Depends(require_api)]}


@router.get("/charts", **PAGE)
def charts_page(request: Request):
    playlists = get_services(request).catalog.charts()
    return TEMPLATES.TemplateResponse(
        request, "playlists.html", {"heading": "Vietnam charts", "playlists": playlists}
    )


@router.get("/api/charts", **API)
def api_charts(request: Request):
    return [asdict(playlist) for playlist in get_services(request).catalog.charts()]


@router.get("/moods", **PAGE)
def moods_page(request: Request):
    sections = get_services(request).catalog.moods()
    return TEMPLATES.TemplateResponse(request, "moods.html", {"sections": sections})


@router.get("/api/moods", **API)
def api_moods(request: Request):
    return {
        section: [asdict(mood) for mood in moods]
        for section, moods in get_services(request).catalog.moods().items()
    }


@router.get("/moods/{params}", **PAGE)
def mood_page(request: Request, params: str):
    playlists = get_services(request).catalog.mood_playlists(params)
    return TEMPLATES.TemplateResponse(
        request, "playlists.html", {"heading": "Mood playlists", "playlists": playlists}
    )


@router.get("/api/moods/{params}", **API)
def api_mood(request: Request, params: str):
    return [asdict(p) for p in get_services(request).catalog.mood_playlists(params)]


@router.get("/playlists/{playlist_id}", **PAGE)
def playlist_page(request: Request, playlist_id: str):
    title, songs = get_services(request).catalog.playlist(playlist_id)
    return TEMPLATES.TemplateResponse(
        request, "songs.html", {"heading": title or "Playlist", "songs": songs}
    )


@router.get("/api/playlists/{playlist_id}", **API)
def api_playlist(request: Request, playlist_id: str):
    title, songs = get_services(request).catalog.playlist(playlist_id)
    return {"title": title, "songs": [song_json(song) for song in songs]}


@router.get("/songs/{video_id}/radio", **PAGE)
def radio_page(request: Request, video_id: str):
    songs = get_services(request).catalog.radio(video_id)
    return TEMPLATES.TemplateResponse(request, "songs.html", {"heading": "Radio", "songs": songs})


@router.get("/api/songs/{video_id}/radio", **API)
def api_radio(request: Request, video_id: str):
    return [song_json(song) for song in get_services(request).catalog.radio(video_id)]
