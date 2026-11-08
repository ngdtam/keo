"""Library: one row per Spotify track, with its file and its jobs."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Annotated

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from keo.audio_info import load
from keo.library import Job, inside_music, unique_title_file
from keo.web.deps import TEMPLATES, get_services, require_page
from keo.web.routes_queue import _detail, format_job_time

router = APIRouter()
PAGE = {"response_class": HTMLResponse, "dependencies": [Depends(require_page)]}


def _live(jobs: list[Job]) -> str | None:
    statuses = {job.status for job in jobs}
    if "running" in statuses:
        return "running"
    if "queued" in statuses:
        return "queued"
    return None


def _indicator(live: str | None, path: Path | None) -> tuple[str, str]:
    if live:
        return "yellow", live
    if path is not None and path.is_file():
        return "green", "On disk"
    return "red", "Missing"


def _groups(jobs: list[Job]) -> list[tuple[tuple[str, str], list[Job]]]:
    grouped: dict[tuple[str, str], list[Job]] = {}
    order: list[tuple[str, str]] = []
    for job in jobs:
        key = ("spotify", job.spotify_id) if job.spotify_id else ("video", job.video_id)
        if key not in grouped:
            grouped[key] = []
            order.append(key)
        grouped[key].append(job)
    return [(key, grouped[key]) for key in order]


def _titles(services, spotify_id: str, jobs: list[Job]) -> set[str]:
    titles = {job.title for job in jobs if job.title}
    stored = services.library.song_title(spotify_id)
    if stored:
        titles.add(stored)
    return titles


def _known_file(services, spotify_id: str, jobs: list[Job]) -> Path | None:
    music = services.music_dir
    raw = services.library.audio_path(spotify_id)
    if raw:
        path = Path(raw)
        if music is not None and inside_music(music, path) and path.is_file():
            return path
        return None
    if music is None or not services.library.has_spotify(spotify_id):
        return None
    match = unique_title_file(music, _titles(services, spotify_id, jobs))
    if match is None:
        return None
    services.library.set_audio_path(spotify_id, str(match))
    return match


def _row(services, key: tuple[str, str], jobs: list[Job]) -> SimpleNamespace:
    kind, ident = key
    newest = jobs[0]
    path = _known_file(services, ident, jobs) if kind == "spotify" else None
    color, label = _indicator(_live(jobs), path)
    href = f"/library/spotify/{ident}" if kind == "spotify" else f"/library/video/{ident}"
    cover = ""
    if kind == "spotify" and path and cover_bytes(path):
        cover = f"/library/spotify/{ident}/cover"
    heard = load(path) if path is not None and path.is_file() else None
    return SimpleNamespace(
        href=href, color=color, label=label, artist=newest.artist, title=newest.title,
        path=str(path) if path else "", size=path.stat().st_size if path else None,
        can_delete=kind == "spotify", ident=ident, cover=cover,
        quality=heard.card if heard and heard.card else "",
        details=heard.rows if heard else (),
    )


def _job_lines(services, jobs: list[Job]) -> list[SimpleNamespace]:
    return [
        SimpleNamespace(
            id=job.id, status=job.status, error=job.error,
            line=(
                f"{job.artist} · {job.status} · {job.source} · "
                f"{format_job_time(job.updated_at, services.timezone)}{_detail(job)}"
            ),
        )
        for job in jobs
    ]


def cover_bytes(path: Path) -> tuple[bytes, str] | None:
    try:
        from mutagen import File
    except ImportError:
        return None
    try:
        audio = File(path)
    except Exception:
        return None
    if audio is None:
        return None
    pictures = getattr(audio, "pictures", None) or []
    if pictures:
        return pictures[0].data, pictures[0].mime or "image/jpeg"
    tags = getattr(audio, "tags", None)
    if not tags:
        return None
    block = tags.get("metadata_block_picture") if hasattr(tags, "get") else None
    if block:
        import base64
        from mutagen.flac import Picture
        picture = Picture(base64.b64decode(block[0]))
        return picture.data, picture.mime or "image/jpeg"
    covr = tags.get("covr") if hasattr(tags, "get") else None
    if covr:
        return bytes(covr[0]), "image/jpeg"
    for key in tags.keys():
        if str(key).startswith("APIC"):
            frame = tags[key]
            return frame.data, getattr(frame, "mime", None) or "image/jpeg"
    return None


@router.get("/library/spotify/{spotify_id}/cover", **PAGE)
def song_cover(request: Request, spotify_id: str):
    services = get_services(request)
    jobs = [job for job in services.library.list_jobs() if job.spotify_id == spotify_id]
    if not jobs:
        raise HTTPException(status_code=404, detail="Unknown song")
    path = _known_file(services, spotify_id, jobs)
    found = cover_bytes(path) if path else None
    if found is None:
        raise HTTPException(status_code=404, detail="No cover")
    data, mime = found
    return Response(content=data, media_type=mime)


@router.get("/library", **PAGE)
def library_page(request: Request):
    services = get_services(request)
    rows = [_row(services, key, jobs) for key, jobs in _groups(services.library.list_jobs())]
    return TEMPLATES.TemplateResponse(request, "library.html", {"rows": rows})


@router.get("/library/spotify/{spotify_id}", **PAGE)
def spotify_song(request: Request, spotify_id: str):
    services = get_services(request)
    jobs = [job for job in services.library.list_jobs() if job.spotify_id == spotify_id]
    if not jobs:
        raise HTTPException(status_code=404, detail="Unknown song")
    row = _row(services, ("spotify", spotify_id), jobs)
    return TEMPLATES.TemplateResponse(
        request, "song.html", {"song": row, "jobs": _job_lines(services, jobs)}
    )


@router.get("/library/video/{video_id}", **PAGE)
def video_song(request: Request, video_id: str):
    services = get_services(request)
    jobs = [
        job for job in services.library.list_jobs()
        if job.spotify_id is None and job.video_id == video_id
    ]
    if not jobs:
        raise HTTPException(status_code=404, detail="Unknown song")
    row = _row(services, ("video", video_id), jobs)
    return TEMPLATES.TemplateResponse(
        request, "song.html", {"song": row, "jobs": _job_lines(services, jobs)}
    )


@router.post("/library/spotify/{spotify_id}/delete", **PAGE)
def delete_song(request: Request, spotify_id: str, scope: Annotated[str, Form()] = ""):
    services = get_services(request)
    jobs = [job for job in services.library.list_jobs() if job.spotify_id == spotify_id]
    if not jobs:
        raise HTTPException(status_code=404, detail="Unknown song")
    if scope == "library" and any(job.status in {"queued", "running"} for job in jobs):
        row = _row(services, ("spotify", spotify_id), jobs)
        return TEMPLATES.TemplateResponse(
            request,
            "song.html",
            {
                "song": row,
                "jobs": _job_lines(services, jobs),
                "notice": "This song is still downloading.",
            },
            status_code=409,
        )
    music = services.music_dir
    raw = services.library.audio_path(spotify_id)
    path = Path(raw) if raw else None
    if path is None and music is not None and services.library.has_spotify(spotify_id):
        path = unique_title_file(music, _titles(services, spotify_id, jobs))
    if path is not None and (music is None or not inside_music(music, path)):
        raise HTTPException(status_code=400, detail="Path is outside the music folder")
    if path is not None and path.is_file():
        path.unlink()
        lyrics = path.with_suffix(".lrc")
        if lyrics.is_file() and music is not None and inside_music(music, lyrics):
            lyrics.unlink()
    services.library.forget_spotify(spotify_id)
    if scope == "library":
        services.library.drop_spotify_jobs(spotify_id)
    return RedirectResponse("/library", status_code=303)
