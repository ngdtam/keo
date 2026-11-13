"""Recycle bin operations under music/.recycle/."""

from __future__ import annotations

import shutil
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from keo.library import AUDIO_EXT, Library
from keo.scan import scan

RECYCLE_DIR = ".recycle"


@dataclass(frozen=True)
class RecycleItem:
    name: str
    size: int
    deleted_at: str


def recycle_root(music_dir: Path) -> Path:
    return music_dir / RECYCLE_DIR


def _safe_entry(music_dir: Path, filename: str) -> Path:
    if not filename or Path(filename).name != filename or filename in (".", ".."):
        raise ValueError("Invalid trash filename")
    root = recycle_root(music_dir).resolve()
    candidate = (root / filename).resolve()
    if root not in candidate.parents:
        raise ValueError("Path is outside the recycle bin")
    return candidate


def _unique_dest(directory: Path, name: str) -> Path:
    dest = directory / name
    if not dest.exists():
        return dest
    stem = dest.stem
    suffix = dest.suffix
    stamp = int(time.time())
    candidate = directory / f"{stem}.{stamp}{suffix}"
    counter = 1
    while candidate.exists():
        candidate = directory / f"{stem}.{stamp}_{counter}{suffix}"
        counter += 1
    return candidate


def move_to_recycle(music_dir: Path, audio_path: Path) -> Path:
    root = recycle_root(music_dir)
    root.mkdir(parents=True, exist_ok=True)
    dest = _unique_dest(root, audio_path.name)
    shutil.move(str(audio_path), str(dest))
    lyrics = audio_path.with_suffix(".lrc")
    if lyrics.is_file():
        shutil.move(str(lyrics), str(dest.with_suffix(".lrc")))
    return dest


def list_recycle(music_dir: Path) -> list[RecycleItem]:
    root = recycle_root(music_dir)
    if not root.is_dir():
        return []
    entries: list[tuple[float, RecycleItem]] = []
    for path in root.iterdir():
        if not path.is_file() or path.suffix.lower() not in AUDIO_EXT:
            continue
        try:
            st = path.stat()
        except OSError:
            continue
        deleted_at = datetime.fromtimestamp(st.st_mtime, tz=timezone.utc).strftime("%Y-%m-%d %H:%M")
        entries.append((st.st_mtime, RecycleItem(name=path.name, size=st.st_size, deleted_at=deleted_at)))
    entries.sort(key=lambda item: item[0], reverse=True)
    return [item for _, item in entries]


def restore_from_recycle(music_dir: Path, filename: str, library: Library) -> Path:
    src = _safe_entry(music_dir, filename)
    if not src.is_file():
        raise FileNotFoundError(filename)
    dest = _unique_dest(music_dir, src.name)
    shutil.move(str(src), str(dest))
    lyrics = src.with_suffix(".lrc")
    if lyrics.is_file():
        shutil.move(str(lyrics), str(dest.with_suffix(".lrc")))
    scan(music_dir, library)
    return dest


def delete_from_recycle(music_dir: Path, filename: str) -> None:
    src = _safe_entry(music_dir, filename)
    if src.is_file():
        src.unlink()
    lyrics = src.with_suffix(".lrc")
    if lyrics.is_file():
        lyrics.unlink()


def empty_recycle(music_dir: Path) -> int:
    root = recycle_root(music_dir)
    if not root.is_dir():
        return 0
    removed = 0
    for path in list(root.iterdir()):
        if path.is_file():
            if path.suffix.lower() in AUDIO_EXT:
                removed += 1
            path.unlink()
    return removed


def cleanup_recycle(music_dir: Path, days: int, *, now: float | None = None) -> int:
    if days <= 0:
        return 0
    root = recycle_root(music_dir)
    if not root.is_dir():
        return 0
    cutoff = (time.time() if now is None else now) - days * 86400
    removed = 0
    for path in list(root.iterdir()):
        if not path.is_file():
            continue
        try:
            if path.stat().st_mtime < cutoff:
                if path.suffix.lower() in AUDIO_EXT:
                    removed += 1
                path.unlink()
        except OSError:
            continue
    return removed

