"""YouTube Music lookups through ytmusicapi, returned as plain records."""

from __future__ import annotations

import json
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any, TypeVar

from ytmusicapi import OAuthCredentials, YTMusic

from keo.records import HistoryItem, MoodRef, PlaylistRef, SongRow

T = TypeVar("T")
CHART_CAROUSELS = ("daily", "weekly", "videos")


class CatalogError(Exception):
    """YouTube Music failed or rejected the request."""


def open_ytmusic(auth_path: Path, client_id: str, client_secret: str) -> YTMusic:
    auth = json.loads(auth_path.read_text(encoding="utf-8"))
    if "refresh_token" in auth:
        credentials = OAuthCredentials(client_id=client_id, client_secret=client_secret)
        return YTMusic(str(auth_path), language="en", oauth_credentials=credentials)
    return YTMusic(str(auth_path), language="en")


class Catalog:
    def __init__(self, ytm: Any) -> None:
        self._ytm = ytm

    def search(self, query: str) -> list[SongRow]:
        return _songs(self._call(self._ytm.search, query, filter="songs", limit=20))

    def suggestions(self, query: str) -> list[str]:
        return list(self._call(self._ytm.get_search_suggestions, query))

    def charts(self) -> list[PlaylistRef]:
        data = self._call(self._ytm.get_charts, "VN")
        return [
            PlaylistRef(item["playlistId"], item.get("title") or "")
            for key in CHART_CAROUSELS
            for item in data.get(key) or []
            if item.get("playlistId")
        ]

    def moods(self) -> dict[str, list[MoodRef]]:
        data = self._call(self._ytm.get_mood_categories)
        return {
            section: [MoodRef(item["params"], item["title"]) for item in items]
            for section, items in data.items()
        }

    def mood_playlists(self, params: str) -> list[PlaylistRef]:
        return [
            PlaylistRef(item["playlistId"], item.get("title") or "")
            for item in self._call(self._ytm.get_mood_playlists, params)
            if item.get("playlistId")
        ]

    def playlist(self, playlist_id: str) -> tuple[str, list[SongRow]]:
        if playlist_id in {"LM", "liked"}:
            data = self._call(self._ytm.get_liked_songs, limit=100)
            return data.get("title") or "Liked Music", _songs(data.get("tracks") or [])
        data = self._call(self._ytm.get_playlist, playlist_id, limit=100)
        return data.get("title") or "", _songs(data.get("tracks") or [])

    def find_artist(self, query: str) -> tuple[str, str] | None:
        results = self._call(self._ytm.search, query, filter="artists", limit=1)
        for item in results:
            cid = item.get("browseId")
            name = item.get("artist") or ""
            if cid:
                return cid, name or cid
        return None

    def artist_releases(self, channel_id: str) -> tuple[str, list[SongRow]]:
        data = self._call(self._ytm.get_artist, channel_id)
        name = data.get("name") or ""
        seen: set[str] = set()
        songs: list[SongRow] = []

        def _add(rows: list[SongRow]) -> None:
            for row in rows:
                if row.video_id in seen:
                    continue
                seen.add(row.video_id)
                if not row.artists and name:
                    row = SongRow(row.video_id, row.title, (name,), row.album)
                songs.append(row)

        _add(_songs((data.get("songs") or {}).get("results") or []))
        releases = [
            *((data.get("singles") or {}).get("results") or [])[:3],
            *((data.get("albums") or {}).get("results") or [])[:2],
        ]
        for rel in releases:
            bid = rel.get("browseId")
            if not bid:
                continue
            album = self._call(self._ytm.get_album, bid)
            _add(_songs(album.get("tracks") or []))
        return name, songs

    def radio(self, video_id: str) -> list[SongRow]:
        data = self._call(self._ytm.get_watch_playlist, videoId=video_id, limit=25)
        return _songs(data.get("tracks") or [])

    def history(self) -> list[HistoryItem]:
        items = []
        for item in self._call(self._ytm.get_history):
            song = _song(item)
            if song is not None:
                items.append(
                    HistoryItem(song.video_id, song.primary_artist, song.title, item.get("played") or "")
                )
        return items

    def _call(self, fn: Callable[..., T], *args: Any, **kwargs: Any) -> T:
        try:
            return fn(*args, **kwargs)
        except Exception as exc:
            raise CatalogError(f"YouTube Music request failed: {exc}") from exc


def _song(item: dict[str, Any]) -> SongRow | None:
    video_id = item.get("videoId")
    if not video_id:
        return None
    artists = tuple(a["name"] for a in item.get("artists") or [] if a.get("name"))
    album = item.get("album")
    return SongRow(
        video_id=video_id,
        title=item.get("title") or "",
        artists=artists,
        album=album.get("name") if isinstance(album, dict) else None,
    )


def _songs(items: Iterable[dict[str, Any]]) -> list[SongRow]:
    return [song for song in map(_song, items) if song is not None]
