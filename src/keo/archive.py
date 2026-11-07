"""Read spotDL's archive file: one Spotify track URL per line."""

from __future__ import annotations

from pathlib import Path


def track_id(url: str) -> str | None:
    if "/track/" not in url:
        return None
    return url.rsplit("/track/", 1)[1].split("?", 1)[0].strip("/") or None


def archive_ids(path: Path) -> set[str]:
    if not path.is_file():
        return set()
    lines = path.read_text(encoding="utf-8").splitlines()
    return {song_id for line in lines if (song_id := track_id(line.strip()))}
