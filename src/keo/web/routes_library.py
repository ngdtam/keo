"""Library: one row per Spotify track, with its file and its jobs."""

from __future__ import annotations

import re
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
from keo.scan import organize_library, plan_organize, scan
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


def _target_kbps(bitrate: str) -> int:
    match = re.match(r"^(\d+)k$", (bitrate or "").strip().lower())
    return int(match.group(1)) if match else 0


def _is_upgradable(row: SimpleNamespace, settings) -> bool:
    if not row.path or not row.quality:
        return False
    quality = row.quality.lower()
    target_fmt = (settings.format or "").strip().lower()
    if target_fmt:
        actual_fmt = quality.split("·", 1)[0].strip()
        if actual_fmt == "aac":
            actual_fmt = "m4a"
        if actual_fmt and actual_fmt != target_fmt:
            return True
    target_kbps = _target_kbps(settings.bitrate)
    if target_kbps > 0:
        match = re.search(r"(\d+)\s*kbps", quality)
        if match and int(match.group(1)) < target_kbps - 16:
            return True
    return False


def _collect_rows(services) -> list[SimpleNamespace]:
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
    return rows


def _format_bytes(total: int) -> str:
    if total >= 1024 ** 3:
        return f"{total / (1024 ** 3):.2f} GB"
    if total >= 1024 ** 2:
        return f"{total / (1024 ** 2):.1f} MB"
    return f"{total // 1024} KB"


def _library_stats(rows: list[SimpleNamespace]) -> str:
    total_size = sum(r.size for r in rows if r.size)
    formats: dict[str, int] = {}
    for r in rows:
        if r.quality:
            fmt = r.quality.split("·", 1)[0].strip()
            if fmt:
                formats[fmt] = formats.get(fmt, 0) + 1
    parts = [f"{len(rows)} songs", _format_bytes(total_size)]
    if formats:
        parts.append(" · ".join(f"{k}: {v}" for k, v in sorted(formats.items())))
    return " · ".join(parts)


@router.get("/library", **PAGE)
def library_page(request: Request, filter: str = "all", q: str = "", sort: str = "artist"):
    services = get_services(request)
    settings = services.spotdl.settings
    all_rows = _collect_rows(services)
    missing_rows = [r for r in all_rows if not r.path and r.label not in {"Queued", "Downloading"}]
    upgradable_rows = [r for r in all_rows if _is_upgradable(r, settings)]
    unmatched_rows = [r for r in all_rows if r.kind == "song"]
    if filter == "missing":
        rows = missing_rows
    elif filter == "upgradable":
        rows = upgradable_rows
    elif filter == "unmatched":
        rows = unmatched_rows
    else:
        filter = "all"
        rows = all_rows
    query = q.strip().casefold()
    if query:
        rows = [
            r for r in rows
            if query in (r.artist or "").casefold() or query in (r.title or "").casefold()
        ]
    if sort == "title":
        rows = sorted(rows, key=lambda r: (r.title or "").casefold())
    elif sort == "size":
        rows = sorted(rows, key=lambda r: r.size or 0, reverse=True)
    else:
        sort = "artist"
        rows = sorted(rows, key=lambda r: ((r.artist or "").casefold(), (r.title or "").casefold()))
    return TEMPLATES.TemplateResponse(
        request,
        "library.html",
        {
            "rows": rows,
            "filter": filter,
            "q": q,
            "sort": sort,
            "stats": _library_stats(all_rows),
            "total_count": len(all_rows),
            "missing_count": len(missing_rows),
            "upgradable_count": len(upgradable_rows),
            "unmatched_count": len(unmatched_rows),
        },
    )


@router.post("/library/rescan", **PAGE)
def rescan_library(request: Request):
    services = get_services(request)
    if services.music_dir is not None:
        scan(services.music_dir, services.library)
    return RedirectResponse("/library", status_code=303)


@router.get("/library/organize/preview", **PAGE)
def organize_preview_route(request: Request):
    services = get_services(request)
    moves = []
    if services.music_dir is not None:
        settings = services.spotdl.settings
        for song, src, target in plan_organize(
            services.music_dir,
            services.library,
            settings.output_template,
            max_length=settings.max_filename_length,
        ):
            moves.append(
                {
                    "artist": song.artist,
                    "title": song.title,
                    "src": str(src.relative_to(services.music_dir)),
                    "dst": str(target.relative_to(services.music_dir)),
                }
            )
    return TEMPLATES.TemplateResponse(request, "organize_preview.html", {"moves": moves})


@router.post("/library/organize", **PAGE)
def organize_library_route(request: Request):
    services = get_services(request)
    if services.music_dir is not None:
        settings = services.spotdl.settings
        organize_library(
            services.music_dir,
            services.library,
            settings.output_template,
            max_length=settings.max_filename_length,
        )
    return RedirectResponse("/library", status_code=303)


@router.post("/library/bulk", **PAGE)
def bulk_library_route(
    request: Request,
    action: Annotated[str, Form()],
    items: Annotated[list[str], Form()] = (),
):
    services = get_services(request)
    for raw in items:
        if ":" not in raw:
            continue
        kind, ident = raw.split(":", 1)
        if kind not in ("spotify", "video", "song"):
            continue
        song = services.library.get_song_by_identifier(kind, ident)
        jobs = _matching_jobs(services, kind, ident)
        if not jobs and song is None:
            continue
        if action == "redownload":
            if kind in ("spotify", "video"):
                if jobs:
                    try:
                        _redownload(services, kind, ident, jobs, allow_upgrade=True)
                    except HTTPException:
                        continue
                elif song is not None and song.video_id:
                    path = _known_file(services, ident, jobs, song=song)
                    if path is not None and services.music_dir is not None and inside_music(services.music_dir, path):
                        move_to_recycle(services.music_dir, path)
                    if kind == "spotify":
                        services.library.forget_spotify(ident)
                    else:
                        services.library.forget_video(ident)
                    services.worker.submit(
                        song.video_id, song.artist, song.title,
                        source="library", spotify_id=song.spotify_id,
                    )
        elif action == "delete":
            if any(job.status in {"queued", "running"} for job in jobs):
                continue
            path = _known_file(services, ident, jobs, song=song)
            if path is not None and services.music_dir is not None and inside_music(services.music_dir, path):
                move_to_recycle(services.music_dir, path)
            if kind == "spotify":
                services.library.forget_spotify(ident)
                services.library.drop_spotify_jobs(ident)
            elif kind == "video":
                services.library.forget_video(ident)
                services.library.drop_video_jobs(ident)
            elif song is not None:
                services.library.forget_song(song.id)
            title = song.title if song is not None else jobs[0].title
            services.library.record_event(title, "deleted", "bulk")
    return RedirectResponse("/library", status_code=303)


@router.post("/library/song/{ident}/link", **PAGE)
def link_manual_song(
    request: Request,
    ident: str,
    video_id: Annotated[str, Form()],
    artist: Annotated[str, Form()],
    title: Annotated[str, Form()],
):
    services = get_services(request)
    try:
        song_id = int(ident)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail="Unknown song") from exc
    linked = services.library.link_song(song_id, video_id.strip(), artist.strip(), title.strip())
    if linked is None:
        raise HTTPException(status_code=404, detail="Unknown song")
    return RedirectResponse(f"/library/video/{linked.video_id}", status_code=303)


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
            services.library.record_event(name, "restored")
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
    label = f"{song.artist} - {song.title}" if song is not None else (f"{jobs[0].artist} - {jobs[0].title}" if jobs else ident)
    services.library.record_event(label, "deleted", scope)
    return RedirectResponse("/library", status_code=303)


def _redownload(services, kind: str, ident: str, jobs: list[Job], *, allow_upgrade: bool = False):
    if not jobs:
        raise HTTPException(status_code=404, detail="Unknown song")
    live = next((job for job in jobs if job.status in {"queued", "running"}), None)
    if live is not None:
        return queued(live)
    path = _known_file(services, ident, jobs)
    if path is not None:
        if not allow_upgrade:
            raise HTTPException(status_code=409, detail="The file is already on disk.")
        if services.music_dir is not None and inside_music(services.music_dir, path):
            move_to_recycle(services.music_dir, path)
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


@router.post("/library/redownload-missing", **PAGE)
def redownload_missing_route(request: Request):
    services = get_services(request)
    require_download(services)
    jobs_by_key = dict(_groups(services.library.list_jobs(limit=1000)))
    for row in _collect_rows(services):
        if row.path or row.label in {"Queued", "Downloading"}:
            continue
        jobs = jobs_by_key.get((row.kind, row.ident), [])
        if jobs:
            try:
                _redownload(services, row.kind, row.ident, jobs)
            except HTTPException:
                continue
    return RedirectResponse("/library?filter=missing", status_code=303)


@router.post("/library/upgrade-all", **PAGE)
def upgrade_all_route(request: Request):
    services = get_services(request)
    require_download(services)
    settings = services.spotdl.settings
    jobs_by_key = dict(_groups(services.library.list_jobs(limit=1000)))
    for row in _collect_rows(services):
        if not _is_upgradable(row, settings):
            continue
        jobs = jobs_by_key.get((row.kind, row.ident), [])
        if jobs:
            try:
                _redownload(services, row.kind, row.ident, jobs, allow_upgrade=True)
            except HTTPException:
                continue
    return RedirectResponse("/library?filter=upgradable", status_code=303)


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
