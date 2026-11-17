"""Browse Vietnam charts, moods, playlists, and radio."""

from __future__ import annotations

from dataclasses import asdict

from fastapi import APIRouter, Depends, Request

from keo.web.deps import get_services, require_api, song_json

router = APIRouter(tags=["Browse"])
API = {"dependencies": [Depends(require_api)]}


@router.get("/api/charts", **API)
def api_charts(request: Request):
    return [asdict(playlist) for playlist in get_services(request).catalog.charts()]


@router.get("/api/moods", **API)
def api_moods(request: Request):
    return {
        section: [asdict(mood) for mood in moods]
        for section, moods in get_services(request).catalog.moods().items()
    }


@router.get("/api/moods/{params}", **API)
def api_mood(request: Request, params: str):
    return [asdict(p) for p in get_services(request).catalog.mood_playlists(params)]


@router.get("/api/playlists/{playlist_id}", **API)
def api_playlist(request: Request, playlist_id: str):
    title, songs = get_services(request).catalog.playlist(playlist_id)
    return {"title": title, "songs": [song_json(song) for song in songs]}


@router.get("/api/songs/{video_id}/radio", **API)
def api_radio(request: Request, video_id: str):
    return [song_json(song) for song in get_services(request).catalog.radio(video_id)]
