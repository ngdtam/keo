"""Plain records passed between Kéo modules."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SongRow:
    video_id: str
    title: str
    artists: tuple[str, ...]
    album: str | None

    @property
    def primary_artist(self) -> str:
        return self.artists[0] if self.artists else ""


@dataclass(frozen=True)
class PlaylistRef:
    playlist_id: str
    title: str


@dataclass(frozen=True)
class MoodRef:
    params: str
    title: str


@dataclass(frozen=True)
class HistoryItem:
    video_id: str
    artist: str
    title: str
    played_label: str
