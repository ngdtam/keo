"""Scan the music folder to sync SQLite songs and cached audio metadata."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from keo.audio_info import AudioView, cover_bytes, load as load_audio_info
from keo.library import Library, list_audio


@dataclass(frozen=True)
class ScanResult:
    added: int
    updated: int
    missing: int


def read_tags(path: Path) -> tuple[str, str]:
    """Read (artist, title) from audio tags, falling back to 'Artist - Title' filename."""
    try:
        from mutagen import File
    except ImportError:
        File = None
    if File is not None:
        try:
            audio = File(path, easy=True)
            if audio and getattr(audio, "tags", None):
                artists = audio.tags.get("artist") or []
                titles = audio.tags.get("title") or []
                artist = str(artists[0]).strip() if artists else ""
                title = str(titles[0]).strip() if titles else ""
                if title:
                    return artist, title
        except Exception:
            pass
    stem = path.stem
    if " - " in stem:
        artist, title = stem.split(" - ", 1)
        return artist.strip(), title.strip()
    return "", stem.strip()


def _has_cover(path: Path) -> bool:
    return cover_bytes(path) is not None


def _cache_song(
    library: Library,
    song_id: int,
    path: Path,
    stat_result,
    audio_loader: Callable[[Path], AudioView | None],
    cover_checker: Callable[[Path], bool],
) -> None:
    view = audio_loader(path)
    library.set_audio_cache(
        song_id,
        audio_path=str(path),
        mtime=stat_result.st_mtime,
        size=stat_result.st_size,
        card=view.card if view else None,
        details=view.rows if view else (),
        has_cover=bool(cover_checker(path)),
    )


def scan(
    music_dir: Path,
    library: Library,
    *,
    audio_loader: Callable[[Path], AudioView | None] = load_audio_info,
    cover_checker: Callable[[Path], bool] = _has_cover,
) -> ScanResult:
    if not music_dir.is_dir():
        return ScanResult(0, 0, 0)

    files: dict[str, tuple[Path, object]] = {}
    for path in list_audio(music_dir):
        try:
            files[str(path)] = (path, path.stat())
        except OSError:
            continue

    missing = library.clear_missing_audio(set(files))
    by_path = {s.audio_path: s for s in library.list_songs() if s.audio_path}
    existing_ids = {s.id for s in library.list_songs()}

    added = 0
    updated = 0
    for path_str, (path, st) in files.items():
        current = by_path.get(path_str)
        if current is not None and current.audio_mtime == st.st_mtime and current.has_cover is not None:
            continue
        artist, title = read_tags(path)
        song = library.upsert_scanned_song(artist, title, path_str)
        is_new = song.id not in existing_ids
        existing_ids.add(song.id)
        _cache_song(library, song.id, path, st, audio_loader, cover_checker)
        if is_new:
            added += 1
        else:
            updated += 1
    return ScanResult(added=added, updated=updated, missing=missing)


def refresh_song_cache(
    library: Library,
    kind: str,
    identifier: str,
    path: Path,
    *,
    audio_loader: Callable[[Path], AudioView | None] = load_audio_info,
    cover_checker: Callable[[Path], bool] = _has_cover,
) -> None:
    song = library.get_song_by_identifier(kind, identifier)
    if song is None:
        return
    try:
        st = path.stat()
    except OSError:
        return
    _cache_song(library, song.id, path, st, audio_loader, cover_checker)

