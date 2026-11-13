"""Library: one row per Spotify track, with its file and its jobs."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Annotated

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from keo.audio_info import cover_bytes, load
from keo.library import Job, Song, inside_music, unique_title_file
from keo.recycle import (
    delete_from_recycle,
    empty_recycle,
    list_recycle,
    move_to_recycle,
    restore_from_recycle,
)
from keo.scan import scan
from keo.web.deps import TEMPLATES, get_services, require_api, require_download, require_page
from keo.web.routes_queue import _detail, format_job_time, queued

router = APIRouter()
PAGE = {"response_class": HTMLResponse, "dependencies": [Depends(require_page)]}
API = {"dependencies": [Depends(require_api)]}


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


def _titles(services, ident: str, jobs: list[Job]) -> set[str]:
    titles = {job.title for job in jobs if job.title}
    stored = services.library.song_title(ident)
    if stored:
        titles.add(stored)
    return titles


def _song_key(song: Song) -> tuple[str, str]:
    if song.spotify_id:
        return ("spotify", song.spotify_id)
    if song.video_id:
        return ("video", song.video_id)
    return ("song", str(song.id))


def _known_file(services, ident: str, jobs: list[Job], song: Song | None = None) -> Path | None:
    music = services.music_dir
    raw = song.audio_path if song is not None else services.library.audio_path(ident)
    if raw:
        path = Path(raw)
        if music is not None and inside_music(music, path) and path.is_file():
            return path
        return None
    if music is None or not (services.library.has_spotify(ident) or services.library.has_song(ident)):
        return None
    match = unique_title_file(music, _titles(services, ident, jobs))
    if match is None:
        return None
    services.library.set_audio_path(ident, str(match))
    return match


def _row(services, key: tuple[str, str], jobs: list[Job], song: Song | None = None) -> SimpleNamespace:
    kind, ident = key
    path = _known_file(services, ident, jobs, song=song)
    color, label = _indicator(_live(jobs), path)
    href = f"/library/{kind}/{ident}"
    artist = song.artist if song is not None else jobs[0].artist
    title = song.title if song is not None else jobs[0].title

    if song is not None and song.has_cover is not None and path is not None:
        cover = f"/library/{kind}/{ident}/cover" if song.has_cover else ""
        quality = song.audio_card or ""
        details = song.audio_details
        size = song.audio_size if song.audio_size is not None else path.stat().st_size
    else:
        cover = f"/library/{kind}/{ident}/cover" if (path and cover_bytes(path)) else ""
        heard = load(path) if path is not None and path.is_file() else None
        quality = heard.card if heard and heard.card else ""
        details = heard.rows if heard else ()
        size = path.stat().st_size if path else None

    return SimpleNamespace(
        href=href, color=color, label=label, artist=artist, title=title,
        path=str(path) if path else "", size=size,
        can_delete=True, ident=ident, cover=cover, kind=kind,
        quality=quality, details=details,
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


def _matching_jobs(services, kind: str, ident: str) -> list[Job]:
    return [
        job for job in services.library.list_jobs(limit=1000)
        if (
            job.spotify_id == ident if kind == "spotify"
            else (job.spotify_id is None and job.video_id == ident) if kind == "video"
            else False
        )
    ]


@router.get("/library/{kind}/{ident}/cover", **PAGE)
def song_cover(request: Request, kind: str, ident: str):
    if kind not in ("spotify", "video", "song"):
        raise HTTPException(status_code=404, detail="Not found")
    services = get_services(request)
    song = services.library.get_song_by_identifier(kind, ident)
    jobs = _matching_jobs(services, kind, ident)
    if not jobs and song is None:
        raise HTTPException(status_code=404, detail="Unknown song")
    path = _known_file(services, ident, jobs, song=song)
    found = cover_bytes(path) if path else None
    if found is None:
        raise HTTPException(status_code=404, detail="No cover")
    data, mime = found
    return Response(content=data, media_type=mime)


@router.get("/library", **PAGE)
def library_page(request: Request):
    services = get_services(request)
    jobs_by_key = dict(_groups(services.library.list_jobs(limit=1000)))
    seen: set[tuple[str, str]] = set()
    rows = []
    for song in services.library.list_songs():
        key = _song_key(song)
        seen.add(key)
        rows.append(_row(services, key, jobs_by_key.get(key, []), song=song))
    for key, jobs in jobs_by_key.items():
        if key not in seen:
            rows.append(_row(services, key, jobs))
    return TEMPLATES.TemplateResponse(request, "library.html", {"rows": rows})


@router.post("/library/rescan", **PAGE)
def rescan_library(request: Request):
    services = get_services(request)
    if services.music_dir is not None:
        scan(services.music_dir, services.library)
    return RedirectResponse("/library", status_code=303)


@router.get("/library/trash", **PAGE)
def trash_page(request: Request):
    services = get_services(request)
    items = list_recycle(services.music_dir) if services.music_dir is not None else []
    return TEMPLATES.TemplateResponse(
        request, "trash.html", {"items": items, "recycle_days": services.recycle_days}
    )


@router.post("/library/trash/restore", **PAGE)
def trash_restore(request: Request, name: Annotated[str, Form()]):
    services = get_services(request)
    if services.music_dir is not None:
        try:
            restore_from_recycle(services.music_dir, name, services.library)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail="Not found") from exc
    return RedirectResponse("/library/trash", status_code=303)


@router.post("/library/trash/delete", **PAGE)
def trash_delete(request: Request, name: Annotated[str, Form()]):
    services = get_services(request)
    if services.music_dir is not None:
        try:
            delete_from_recycle(services.music_dir, name)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
    return RedirectResponse("/library/trash", status_code=303)


@router.post("/library/trash/empty", **PAGE)
def trash_empty(request: Request):
    services = get_services(request)
    if services.music_dir is not None:
        empty_recycle(services.music_dir)
    return RedirectResponse("/library/trash", status_code=303)


@router.get("/library/spotify/{spotify_id}", **PAGE)
def spotify_song(request: Request, spotify_id: str):
    services = get_services(request)
    song = services.library.get_song_by_identifier("spotify", spotify_id)
    jobs = _matching_jobs(services, "spotify", spotify_id)
    if not jobs and song is None:
        raise HTTPException(status_code=404, detail="Unknown song")
    row = _row(services, ("spotify", spotify_id), jobs, song=song)
    return TEMPLATES.TemplateResponse(
        request, "song.html", {"song": row, "jobs": _job_lines(services, jobs)}
    )


@router.get("/library/video/{video_id}", **PAGE)
def video_song(request: Request, video_id: str):
    services = get_services(request)
    song = services.library.get_song_by_identifier("video", video_id)
    jobs = _matching_jobs(services, "video", video_id)
    if not jobs and song is None:
        raise HTTPException(status_code=404, detail="Unknown song")
    row = _row(services, ("video", video_id), jobs, song=song)
    return TEMPLATES.TemplateResponse(
        request, "song.html", {"song": row, "jobs": _job_lines(services, jobs)}
    )


@router.get("/library/song/{song_id:int}", **PAGE)
def local_song(request: Request, song_id: int):
    services = get_services(request)
    song = services.library.get_song(song_id)
    if song is None:
        raise HTTPException(status_code=404, detail="Unknown song")
    row = _row(services, ("song", str(song_id)), [], song=song)
    return TEMPLATES.TemplateResponse(
        request, "song.html", {"song": row, "jobs": []}
    )


@router.post("/library/{kind}/{ident}/delete", **PAGE)
def delete_song(request: Request, kind: str, ident: str, scope: Annotated[str, Form()] = ""):
    if kind not in ("spotify", "video", "song"):
        raise HTTPException(status_code=404, detail="Not found")
    services = get_services(request)
    song = services.library.get_song_by_identifier(kind, ident)
    jobs = _matching_jobs(services, kind, ident)
    if not jobs and song is None:
        raise HTTPException(status_code=404, detail="Unknown song")
    if scope == "library" and any(job.status in {"queued", "running"} for job in jobs):
        row = _row(services, (kind, ident), jobs, song=song)
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
    raw = song.audio_path if song is not None else services.library.audio_path(ident)
    path = Path(raw) if raw else None
    if path is None and music is not None and (song is not None or services.library.has_spotify(ident) or services.library.has_song(ident)):
        path = unique_title_file(music, _titles(services, ident, jobs))
    if path is not None and (music is None or not inside_music(music, path)):
        raise HTTPException(status_code=400, detail="Path is outside the music folder")
    if path is not None and path.is_file():
        move_to_recycle(music, path)
    if kind == "spotify":
        services.library.forget_spotify(ident)
        if scope == "library":
            services.library.drop_spotify_jobs(ident)
    elif kind == "video":
        services.library.forget_video(ident)
        if scope == "library":
            services.library.drop_video_jobs(ident)
    elif scope == "library" and song is not None:
        services.library.forget_song(song.id)
    return RedirectResponse("/library", status_code=303)


def _redownload(services, kind: str, ident: str, jobs: list[Job]):
    if not jobs:
        raise HTTPException(status_code=404, detail="Unknown song")
    live = next((job for job in jobs if job.status in {"queued", "running"}), None)
    if live is not None:
        return queued(live)
    path = _known_file(services, ident, jobs)
    if path is not None:
        raise HTTPException(status_code=409, detail="The file is already on disk.")
    require_download(services)
    if kind == "spotify":
        services.library.forget_spotify(ident)
    else:
        services.library.forget_video(ident)
    failed = next((job for job in jobs if job.status == "failed"), None)
    if failed is not None:
        return queued(services.worker.retry(failed.id))
    newest = jobs[0]
    job = services.worker.submit(
        newest.video_id, newest.artist, newest.title,
        source="library", spotify_url=newest.spotify_url, spotify_id=newest.spotify_id,
    )
    if job is None:
        raise HTTPException(status_code=409, detail="Already in the library.")
    return queued(job)


@router.post("/api/library/spotify/{spotify_id}/download", **API)
def download_spotify(request: Request, spotify_id: str):
    services = get_services(request)
    jobs = [job for job in services.library.list_jobs() if job.spotify_id == spotify_id]
    return _redownload(services, "spotify", spotify_id, jobs)


@router.post("/api/library/video/{video_id}/download", **API)
def download_video(request: Request, video_id: str):
    services = get_services(request)
    jobs = [
        job for job in services.library.list_jobs()
        if job.spotify_id is None and job.video_id == video_id
    ]
    return _redownload(services, "video", video_id, jobs)
