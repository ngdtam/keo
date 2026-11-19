"""Scan the music folder to sync SQLite songs and cached audio metadata."""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from keo.audio_info import AudioView, cover_bytes, load as load_audio_info
from keo.library import Library, inside_music, list_audio
from keo.youtube_dl import format_output_template


@dataclass(frozen=True)
class ScanResult:
    added: int
    updated: int
    missing: int


def _read_embedded_tags(path: Path) -> tuple[str, str]:
    """Read (artist, title) from embedded audio tags, returning empty strings if absent."""
    try:
        from mutagen import File
    except ImportError:
        return "", ""
    try:
        audio = File(path, easy=True)
        if audio and getattr(audio, "tags", None):
            artists = audio.tags.get("artist") or []
            titles = audio.tags.get("title") or []
            artist = ", ".join(a for a in (str(x).strip() for x in artists) if a)
            title = str(titles[0]).strip() if titles else ""
            return artist, title
    except Exception:
        pass
    return "", ""


def read_album_tag(path: Path) -> str | None:
    """Read the embedded album tag from an audio file, if present."""
    try:
        from mutagen import File
    except ImportError:
        return None
    try:
        audio = File(path, easy=True)
        if audio and getattr(audio, "tags", None):
            albums = audio.tags.get("album") or []
            album = str(albums[0]).strip() if albums else ""
            return album or None
    except Exception:
        pass
    return None


_SPOTIFY_URL_RE = re.compile(r"open\.spotify\.com/track/([A-Za-z0-9]+)")


def read_spotify_id_tag(path: Path) -> str | None:
    """Recover a Spotify track ID from embedded WOAS / URL tags if present."""
    try:
        from mutagen import File
    except ImportError:
        return None
    try:
        audio = File(path)
        tags = getattr(audio, "tags", None)
        if not tags:
            return None
        for key in ("WOAS", "woas", "purl", "url", "website"):
            val = tags.get(key)
            if val is None:
                continue
            text = str(getattr(val, "url", val))
            match = _SPOTIFY_URL_RE.search(text)
            if match:
                return match.group(1)
    except Exception:
        pass
    return None


def read_tags(path: Path) -> tuple[str, str]:
    """Read (artist, title) from audio tags, falling back to 'Artist - Title' filename."""
    artist, title = _read_embedded_tags(path)
    if title:
        return artist, title
    stem = path.stem
    if " - " in stem:
        artist, title = stem.split(" - ", 1)
        return artist.strip(), title.strip()
    return "", stem.strip()


def _has_cover(path: Path) -> bool:
    return cover_bytes(path) is not None


def _sync_spotify_tags(library: Library, song: object, path: Path) -> None:
    if not getattr(song, "spotify_id", None):
        return
    tag_artist, tag_title = _read_embedded_tags(path)
    if tag_artist or tag_title:
        library.sync_song_metadata(
            song.id,
            artist=tag_artist or None,
            title=tag_title or None,
        )


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
        album=read_album_tag(path),
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
            if current.album is None:
                tag_album = read_album_tag(path)
                if tag_album:
                    library.set_song_album(current.id, tag_album)
            _sync_spotify_tags(library, current, path)
            continue
        artist, title = read_tags(path)
        song = library.upsert_scanned_song(artist, title, path_str)
        if song.spotify_id is None:
            recovered_id = read_spotify_id_tag(path)
            if recovered_id:
                library.set_song_spotify_id(song.id, recovered_id)
                song = library.get_song(song.id) or song
        is_new = song.id not in existing_ids
        existing_ids.add(song.id)
        _cache_song(library, song.id, path, st, audio_loader, cover_checker)
        _sync_spotify_tags(library, song, path)
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
    _sync_spotify_tags(library, song, path)


def plan_organize(
    music_dir: Path,
    library: Library,
    output_template: str,
    *,
    max_length: int = 0,
) -> list[tuple[object, Path, Path]]:
    """Return planned (song, src, target) renames without touching disk."""
    if not music_dir.is_dir():
        return []
    planned: list[tuple[object, Path, Path]] = []
    for song in library.list_songs():
        if not song.audio_path or not song.artist or not song.title:
            continue
        src = Path(song.audio_path)
        if not src.is_file() or not inside_music(music_dir, src):
            continue
        tag_artist, tag_title = _read_embedded_tags(src)
        rel = format_output_template(
            output_template, tag_artist or song.artist, tag_title or song.title, max_length=max_length
        ).removesuffix(".%(ext)s")
        target = music_dir / f"{rel}{src.suffix}"
        if target == src or not inside_music(music_dir, target) or target.exists():
            continue
        planned.append((song, src, target))
    return planned


def organize_library(
    music_dir: Path,
    library: Library,
    output_template: str,
    *,
    max_length: int = 0,
) -> int:
    """Rename and move library audio files (plus .lrc sidecars) to match output_template."""
    moved = 0
    for song, src, target in plan_organize(
        music_dir, library, output_template, max_length=max_length
    ):
        if target.exists():
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        src.replace(target)
        lrc = src.with_suffix(".lrc")
        lrc_target = target.with_suffix(".lrc")
        if lrc.is_file() and not lrc_target.exists():
            lrc.replace(lrc_target)
        library.set_song_audio_path(song.id, str(target))
        parent = src.parent
        while parent != music_dir and parent.is_dir():
            try:
                parent.rmdir()
            except OSError:
                break
            parent = parent.parent
        moved += 1
    return moved

