"""SQLite store for owned songs, download jobs, the review list, and history pulls.

This is the only module that writes the database. One lock serializes access
because the web threads, the worker, and the scheduler share one connection.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import re
import secrets
import sqlite3
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from keo.archive import archive_ids, drop_track
from keo.match import normalize
from keo.records import HistoryItem

AUDIO_EXT = {".mp3", ".m4a", ".opus", ".ogg", ".flac", ".wav"}


def list_audio(folder: Path) -> set[Path]:
    if not folder.is_dir():
        return set()
    return {
        path
        for path in folder.rglob("*")
        if path.is_file()
        and path.suffix.lower() in AUDIO_EXT
        and ".recycle" not in path.relative_to(folder).parts
    }


def unique_title_file(folder: Path, titles: set[str]) -> Path | None:
    found: list[Path] = []
    for path in list_audio(folder):
        for title in titles:
            if title and path.stem.endswith(f" - {title}"):
                found.append(path)
                break
    if len(found) == 1:
        return found[0]
    return None


def inside_music(music: Path, candidate: Path) -> bool:
    root = music.resolve()
    try:
        resolved = candidate.resolve()
    except OSError:
        return False
    return resolved == root or root in resolved.parents


SCHEMA = """
CREATE TABLE IF NOT EXISTS history_pulls (
    id INTEGER PRIMARY KEY,
    started_at TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('ok', 'failed')),
    error TEXT
);
CREATE TABLE IF NOT EXISTS history_items (
    id INTEGER PRIMARY KEY,
    pull_id INTEGER NOT NULL REFERENCES history_pulls (id),
    video_id TEXT NOT NULL,
    artist TEXT NOT NULL,
    title TEXT NOT NULL,
    played_label TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS songs (
    id INTEGER PRIMARY KEY,
    video_id TEXT UNIQUE,
    artist_key TEXT NOT NULL,
    title_key TEXT NOT NULL,
    spotify_id TEXT UNIQUE,
    artist TEXT NOT NULL,
    title TEXT NOT NULL,
    audio_path TEXT,
    audio_mtime REAL,
    audio_size INTEGER,
    audio_card TEXT,
    audio_details TEXT,
    has_cover INTEGER,
    UNIQUE (artist_key, title_key)
);
CREATE TABLE IF NOT EXISTS jobs (
    id INTEGER PRIMARY KEY,
    video_id TEXT NOT NULL,
    artist TEXT NOT NULL,
    title TEXT NOT NULL,
    spotify_url TEXT,
    spotify_id TEXT,
    status TEXT NOT NULL CHECK (status IN ('queued', 'running', 'succeeded', 'failed')),
    source TEXT NOT NULL CHECK (source IN ('manual', 'history', 'review', 'library', 'youtube')),
    error TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    format TEXT,
    premium INTEGER
);
CREATE UNIQUE INDEX IF NOT EXISTS jobs_one_active
    ON jobs (video_id) WHERE status IN ('queued', 'running');
CREATE TABLE IF NOT EXISTS review (
    video_id TEXT PRIMARY KEY,
    heard_artist TEXT NOT NULL,
    heard_title TEXT NOT NULL,
    candidate_artist TEXT,
    candidate_title TEXT,
    spotify_url TEXT,
    spotify_id TEXT,
    reason TEXT NOT NULL CHECK (reason IN ('pending', 'close_match', 'weak_match', 'no_metadata')),
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS dismissed (
    video_id TEXT PRIMARY KEY,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS watched_playlists (
    playlist_id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    auto_queue INTEGER NOT NULL DEFAULT 0,
    last_synced_at TEXT,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS followed_artists (
    channel_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    last_synced_at TEXT,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY,
    created_at TEXT NOT NULL,
    song TEXT NOT NULL,
    kind TEXT NOT NULL,
    detail TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS scheduled_tasks (
    name TEXT PRIMARY KEY,
    interval_hours INTEGER NOT NULL,
    last_run TEXT
);
CREATE TABLE IF NOT EXISTS auth_user (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    username TEXT NOT NULL,
    password_hash TEXT NOT NULL,
    salt TEXT NOT NULL,
    api_key TEXT NOT NULL,
    api_key_rotated INTEGER NOT NULL DEFAULT 0,
    session_secret TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL
);
"""


@dataclass(frozen=True)
class Song:
    id: int
    video_id: str | None
    artist_key: str
    title_key: str
    spotify_id: str | None
    artist: str
    title: str
    audio_path: str | None = None
    audio_mtime: float | None = None
    audio_size: int | None = None
    audio_card: str | None = None
    audio_details: tuple[tuple[str, str], ...] = ()
    has_cover: bool | None = None


@dataclass(frozen=True)
class Job:
    id: int
    video_id: str
    artist: str
    title: str
    spotify_url: str | None
    spotify_id: str | None
    status: str
    source: str
    error: str | None
    created_at: str
    updated_at: str
    format: str | None = None
    premium: int | None = None


@dataclass(frozen=True)
class ReviewRow:
    video_id: str
    heard_artist: str
    heard_title: str
    candidate_artist: str | None
    candidate_title: str | None
    spotify_url: str | None
    spotify_id: str | None
    reason: str
    created_at: str


@dataclass(frozen=True)
class HeardCount:
    video_id: str
    artist: str
    title: str
    plays: int


@dataclass(frozen=True)
class WatchedPlaylist:
    playlist_id: str
    title: str
    auto_queue: bool
    last_synced_at: str | None
    created_at: str


@dataclass(frozen=True)
class FollowedArtist:
    channel_id: str
    name: str
    last_synced_at: str | None
    created_at: str


@dataclass(frozen=True)
class Event:
    id: int
    created_at: str
    song: str
    kind: str
    detail: str


@dataclass(frozen=True)
class HistoryPull:
    id: int
    started_at: str
    status: str
    error: str | None


@dataclass(frozen=True)
class ScheduledTask:
    name: str
    interval_hours: int
    last_run: str | None


@dataclass(frozen=True)
class AuthUser:
    username: str
    password_hash: str
    salt: str
    api_key: str
    api_key_rotated: bool
    updated_at: str
    session_secret: str = ""


class SetupAlreadyCompleteError(Exception):
    """Raised when initial user setup is attempted after credentials are already set."""


def _validate_credentials(username: str, password: str) -> str:
    norm_username = username.strip().lower()
    if not norm_username:
        raise ValueError("Username must not be empty")
    if not password or not password.strip():
        raise ValueError("Password must not be empty")
    return norm_username


def hash_password(password: str, salt_hex: str) -> str:
    return hashlib.pbkdf2_hmac(
        "sha512",
        password.encode("utf-8"),
        bytes.fromhex(salt_hex),
        100_000,
        dklen=32,
    ).hex()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


RECENT_PLAYED_LABELS: tuple[str, ...] = ("Today", "Yesterday", "This week")


def _artists_overlap(left: str, right: str) -> bool:
    if not left or not right:
        return False
    norm_left = f" {normalize(left)} "
    norm_right = f" {normalize(right)} "
    if norm_left.strip() in norm_right or norm_right.strip() in norm_left:
        return True
    tokens_left = {t.strip() for t in re.split(r"[,&x+/]|\b(?:feat|ft)\b", left.casefold()) if len(t.strip()) > 1}
    tokens_right = {t.strip() for t in re.split(r"[,&x+/]|\b(?:feat|ft)\b", right.casefold()) if len(t.strip()) > 1}
    for tl in tokens_left:
        for tr in tokens_right:
            norm_tl = normalize(tl)
            norm_tr = normalize(tr)
            if norm_tl and (norm_tl == norm_tr or norm_tl in norm_tr or norm_tr in norm_tl):
                return True
    return False


def _artist_in_filename(artist: str, audio_path: str) -> bool:
    if not artist or not audio_path:
        return False
    filename = Path(audio_path).stem
    artist_norm = normalize(artist)
    filename_norm = normalize(filename)
    return bool(artist_norm) and (artist_norm in filename_norm or f" {artist_norm} " in f" {filename_norm} ")


LATEST_VERSION = 8


def _row_to_song(row: sqlite3.Row) -> Song:
    raw_details = row["audio_details"]
    details: tuple[tuple[str, str], ...] = ()
    if raw_details:
        try:
            details = tuple((str(k), str(v)) for k, v in json.loads(raw_details))
        except (ValueError, TypeError):
            details = ()
    raw_cover = row["has_cover"]
    return Song(
        id=row["id"],
        video_id=row["video_id"],
        artist_key=row["artist_key"],
        title_key=row["title_key"],
        spotify_id=row["spotify_id"],
        artist=row["artist"],
        title=row["title"],
        audio_path=row["audio_path"],
        audio_mtime=row["audio_mtime"],
        audio_size=row["audio_size"],
        audio_card=row["audio_card"],
        audio_details=details,
        has_cover=None if raw_cover is None else bool(raw_cover),
    )


def _row_to_auth_user(row: sqlite3.Row) -> AuthUser:
    return AuthUser(
        username=row["username"],
        password_hash=row["password_hash"],
        salt=row["salt"],
        api_key=row["api_key"],
        api_key_rotated=bool(row["api_key_rotated"]),
        updated_at=row["updated_at"],
        session_secret=row["session_secret"],
    )


class Library:
    def __init__(self, db_path: Path, archive_path: Path) -> None:
        self._archive_path = archive_path
        self._lock = threading.Lock()
        self._db = sqlite3.connect(db_path, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._db.execute("PRAGMA journal_mode = WAL")
        is_fresh = (
            self._db.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
            ).fetchone()
            is None
        )
        self._db.executescript(SCHEMA)
        self._migrate(is_fresh)

    def _migrate(self, is_fresh: bool) -> None:
        version = self._db.execute("PRAGMA user_version").fetchone()[0]
        if is_fresh and version == 0:
            with self._db:
                self._db.execute(f"PRAGMA user_version = {LATEST_VERSION}")
            return
        if version < 1:
            columns = {row[1] for row in self._db.execute("PRAGMA table_info(jobs)")}
            song_columns = {row[1] for row in self._db.execute("PRAGMA table_info(songs)")}
            with self._db:
                if "format" not in columns:
                    self._db.execute("ALTER TABLE jobs ADD COLUMN format TEXT")
                if "premium" not in columns:
                    self._db.execute("ALTER TABLE jobs ADD COLUMN premium INTEGER")
                if "audio_path" not in song_columns:
                    self._db.execute("ALTER TABLE songs ADD COLUMN audio_path TEXT")
            self._accept_library_source()
            self._accept_close_match_review()
            with self._db:
                self._db.execute("PRAGMA user_version = 1")
        if version < 2:
            song_columns = {row[1] for row in self._db.execute("PRAGMA table_info(songs)")}
            with self._db:
                for col, col_type in (
                    ("audio_mtime", "REAL"),
                    ("audio_size", "INTEGER"),
                    ("audio_card", "TEXT"),
                    ("audio_details", "TEXT"),
                    ("has_cover", "INTEGER"),
                ):
                    if col not in song_columns:
                        self._db.execute(f"ALTER TABLE songs ADD COLUMN {col} {col_type}")
                self._db.execute("PRAGMA user_version = 2")
        if version < 3:
            with self._db:
                self._db.execute("PRAGMA user_version = 3")
        if version < 4:
            with self._db:
                self._db.execute("PRAGMA user_version = 4")
        if version < 5:
            with self._db:
                self._db.execute("PRAGMA user_version = 5")
        if version < 6:
            with self._db:
                self._db.execute("PRAGMA user_version = 6")
        if version < 7:
            with self._db:
                self._db.execute(
                    """
                    CREATE TABLE IF NOT EXISTS auth_user (
                        id INTEGER PRIMARY KEY CHECK (id = 1),
                        username TEXT NOT NULL,
                        password_hash TEXT NOT NULL,
                        salt TEXT NOT NULL,
                        api_key TEXT NOT NULL,
                        api_key_rotated INTEGER NOT NULL DEFAULT 0,
                        updated_at TEXT NOT NULL
                    )
                    """
                )
                self._db.execute("PRAGMA user_version = 7")
        if version < 8:
            auth_columns = {row[1] for row in self._db.execute("PRAGMA table_info(auth_user)")}
            with self._db:
                if "session_secret" not in auth_columns:
                    self._db.execute(
                        "ALTER TABLE auth_user ADD COLUMN session_secret TEXT NOT NULL DEFAULT ''"
                    )
                self._db.execute("PRAGMA user_version = 8")

    def _accept_close_match_review(self) -> None:
        row = self._db.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'review'"
        ).fetchone()
        if row is None or ("'close_match'" in (row[0] or "") and "'pending'" in (row[0] or "")):
            return
        with self._db:
            self._db.execute("DROP TABLE IF EXISTS review_next")
            self._db.execute(
                """
                CREATE TABLE review_next (
                    video_id TEXT PRIMARY KEY,
                    heard_artist TEXT NOT NULL,
                    heard_title TEXT NOT NULL,
                    candidate_artist TEXT,
                    candidate_title TEXT,
                    spotify_url TEXT,
                    spotify_id TEXT,
                    reason TEXT NOT NULL CHECK (reason IN ('pending', 'close_match', 'weak_match', 'no_metadata')),
                    created_at TEXT NOT NULL
                )
                """
            )
            self._db.execute(
                """
                INSERT INTO review_next
                SELECT video_id, heard_artist, heard_title, candidate_artist, candidate_title,
                       spotify_url, spotify_id, reason, created_at
                FROM review
                """
            )
            self._db.execute("DROP TABLE review")
            self._db.execute("ALTER TABLE review_next RENAME TO review")

    def _accept_library_source(self) -> None:
        # SQLite cannot change a CHECK. Rebuild jobs so source may be 'library' and 'youtube'.
        row = self._db.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'jobs'"
        ).fetchone()
        if row is None or ("'library'" in (row[0] or "") and "'youtube'" in (row[0] or "")):
            return
        with self._db:
            self._db.execute("DROP TABLE IF EXISTS jobs_next")
            self._db.execute(
                """
                CREATE TABLE jobs_next (
                    id INTEGER PRIMARY KEY,
                    video_id TEXT NOT NULL,
                    artist TEXT NOT NULL,
                    title TEXT NOT NULL,
                    spotify_url TEXT,
                    spotify_id TEXT,
                    status TEXT NOT NULL CHECK (status IN ('queued', 'running', 'succeeded', 'failed')),
                    source TEXT NOT NULL CHECK (source IN ('manual', 'history', 'review', 'library', 'youtube')),
                    error TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    format TEXT,
                    premium INTEGER
                )
                """
            )
            self._db.execute(
                """
                INSERT INTO jobs_next (
                    id, video_id, artist, title, spotify_url, spotify_id, status, source,
                    error, created_at, updated_at, format, premium
                )
                SELECT
                    id, video_id, artist, title, spotify_url, spotify_id, status, source,
                    error, created_at, updated_at, format, premium
                FROM jobs
                """
            )
            self._db.execute("DROP TABLE jobs")
            self._db.execute("ALTER TABLE jobs_next RENAME TO jobs")
            self._db.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS jobs_one_active"
                " ON jobs (video_id) WHERE status IN ('queued', 'running')"
            )

    # Owned songs

    def is_owned(self, video_id: str, artist: str, title: str) -> bool:
        norm_title = normalize(title)
        norm_artist = normalize(artist)
        with self._lock:
            # 1. Match video_id in songs or jobs linked to an existing song
            if self._db.execute("SELECT 1 FROM songs WHERE video_id = ?", (video_id,)).fetchone() is not None:
                return True
            if self._db.execute(
                "SELECT 1 FROM songs s JOIN jobs j ON (s.spotify_id = j.spotify_id)"
                " WHERE j.video_id = ? AND j.spotify_id IS NOT NULL",
                (video_id,),
            ).fetchone() is not None:
                return True

            # 2. Exact match on (artist_key, title_key)
            if self._db.execute("SELECT 1 FROM songs WHERE artist_key = ? AND title_key = ?", (norm_artist, norm_title)).fetchone() is not None:
                return True

            # 3. Match by title with artist overlap or audio_path filename match
            cursor = self._db.execute("SELECT artist, audio_path FROM songs WHERE title_key = ?", (norm_title,))
            for s_row in cursor.fetchall():
                db_artist = str(s_row["artist"] or "")
                raw_path = str(s_row["audio_path"] or "")
                if _artists_overlap(artist, db_artist):
                    return True
                if raw_path and _artist_in_filename(artist, raw_path):
                    return True

        return False

    def is_owned_spotify(self, spotify_id: str) -> bool:
        with self._lock:
            row = self._db.execute(
                "SELECT 1 FROM songs WHERE spotify_id = ?", (spotify_id,)
            ).fetchone()
        return row is not None or spotify_id in archive_ids(self._archive_path)

    def has_spotify(self, spotify_id: str) -> bool:
        with self._lock:
            row = self._db.execute(
                "SELECT 1 FROM songs WHERE spotify_id = ?", (spotify_id,)
            ).fetchone()
        return row is not None

    def has_song(self, identifier: str) -> bool:
        with self._lock:
            row = self._db.execute(
                "SELECT 1 FROM songs WHERE spotify_id = ? OR video_id = ?",
                (identifier, identifier),
            ).fetchone()
        return row is not None

    def audio_path(self, identifier: str) -> str | None:
        with self._lock:
            row = self._db.execute(
                "SELECT audio_path FROM songs WHERE spotify_id = ? OR video_id = ?",
                (identifier, identifier),
            ).fetchone()
        if row is None or row["audio_path"] is None:
            return None
        return str(row["audio_path"])

    def song_title(self, identifier: str) -> str | None:
        with self._lock:
            row = self._db.execute(
                "SELECT title FROM songs WHERE spotify_id = ? OR video_id = ?", (identifier, identifier)
            ).fetchone()
        return str(row["title"]) if row else None

    def set_audio_path(self, identifier: str, path: str) -> None:
        with self._lock, self._db:
            self._db.execute(
                "UPDATE songs SET audio_path = ? WHERE spotify_id = ? OR video_id = ?",
                (path, identifier, identifier),
            )

    def forget_spotify(self, spotify_id: str) -> None:
        drop_track(self._archive_path, spotify_id)
        with self._lock, self._db:
            self._db.execute("DELETE FROM songs WHERE spotify_id = ?", (spotify_id,))

    def forget_video(self, video_id: str) -> None:
        with self._lock, self._db:
            self._db.execute("DELETE FROM songs WHERE video_id = ?", (video_id,))

    def forget_song(self, song_id: int) -> None:
        with self._lock, self._db:
            self._db.execute("DELETE FROM songs WHERE id = ?", (song_id,))

    def list_songs(self) -> list[Song]:
        with self._lock:
            rows = self._db.execute("SELECT * FROM songs ORDER BY id DESC").fetchall()
        return [_row_to_song(row) for row in rows]

    def get_song(self, song_id: int) -> Song | None:
        with self._lock:
            row = self._db.execute("SELECT * FROM songs WHERE id = ?", (song_id,)).fetchone()
        return _row_to_song(row) if row else None

    def get_song_by_identifier(self, kind: str, ident: str) -> Song | None:
        with self._lock:
            if kind == "spotify":
                row = self._db.execute("SELECT * FROM songs WHERE spotify_id = ?", (ident,)).fetchone()
            elif kind == "video":
                row = self._db.execute("SELECT * FROM songs WHERE video_id = ?", (ident,)).fetchone()
            elif kind == "song":
                try:
                    song_id = int(ident)
                except ValueError:
                    return None
                row = self._db.execute("SELECT * FROM songs WHERE id = ?", (song_id,)).fetchone()
            else:
                row = None
        return _row_to_song(row) if row else None

    def upsert_scanned_song(self, artist: str, title: str, audio_path: str) -> Song:
        norm_artist = normalize(artist)
        norm_title = normalize(title)
        with self._lock, self._db:
            row = self._db.execute("SELECT * FROM songs WHERE audio_path = ?", (audio_path,)).fetchone()
            if row is None and (norm_artist or norm_title):
                row = self._db.execute(
                    "SELECT * FROM songs WHERE artist_key = ? AND title_key = ?",
                    (norm_artist, norm_title),
                ).fetchone()
            if row is None:
                self._db.execute(
                    "INSERT OR IGNORE INTO songs (video_id, artist_key, title_key, spotify_id, artist, title, audio_path)"
                    " VALUES (NULL, ?, ?, NULL, ?, ?, ?)",
                    (norm_artist, norm_title, artist, title, audio_path),
                )
                row = self._db.execute(
                    "SELECT * FROM songs WHERE artist_key = ? AND title_key = ?",
                    (norm_artist, norm_title),
                ).fetchone()
        assert row is not None
        return _row_to_song(row)

    def link_song(self, song_id: int, video_id: str, artist: str, title: str) -> Song | None:
        norm_artist = normalize(artist)
        norm_title = normalize(title)
        with self._lock, self._db:
            src = self._db.execute("SELECT * FROM songs WHERE id = ?", (song_id,)).fetchone()
            if src is None:
                return None
            existing = self._db.execute(
                "SELECT * FROM songs WHERE video_id = ? AND id != ?", (video_id, song_id)
            ).fetchone()
            if existing is not None:
                self._db.execute(
                    "UPDATE songs SET audio_path = COALESCE(audio_path, ?),"
                    " audio_mtime = COALESCE(audio_mtime, ?),"
                    " audio_size = COALESCE(audio_size, ?),"
                    " audio_card = COALESCE(audio_card, ?),"
                    " audio_details = COALESCE(audio_details, ?),"
                    " has_cover = COALESCE(has_cover, ?)"
                    " WHERE id = ?",
                    (
                        src["audio_path"],
                        src["audio_mtime"],
                        src["audio_size"],
                        src["audio_card"],
                        src["audio_details"],
                        src["has_cover"],
                        existing["id"],
                    ),
                )
                self._db.execute("DELETE FROM songs WHERE id = ?", (song_id,))
                row = self._db.execute("SELECT * FROM songs WHERE id = ?", (existing["id"],)).fetchone()
            else:
                self._db.execute(
                    "UPDATE songs SET video_id = ?, artist = ?, title = ?, artist_key = ?, title_key = ?"
                    " WHERE id = ?",
                    (video_id, artist, title, norm_artist, norm_title, song_id),
                )
                row = self._db.execute("SELECT * FROM songs WHERE id = ?", (song_id,)).fetchone()
        return _row_to_song(row) if row is not None else None

    def set_audio_cache(
        self,
        song_id: int,
        *,
        audio_path: str,
        mtime: float,
        size: int,
        card: str | None,
        details: tuple[tuple[str, str], ...],
        has_cover: bool,
    ) -> None:
        with self._lock, self._db:
            self._db.execute(
                "UPDATE songs SET audio_path = ?, audio_mtime = ?, audio_size = ?,"
                " audio_card = ?, audio_details = ?, has_cover = ? WHERE id = ?",
                (audio_path, mtime, size, card, json.dumps(details), int(has_cover), song_id),
            )

    def set_song_audio_path(self, song_id: int, audio_path: str) -> None:
        with self._lock, self._db:
            self._db.execute(
                "UPDATE songs SET audio_path = ? WHERE id = ?",
                (audio_path, song_id),
            )

    def clear_missing_audio(self, existing_paths: set[str]) -> int:
        with self._lock, self._db:
            rows = self._db.execute(
                "SELECT id, audio_path FROM songs WHERE audio_path IS NOT NULL"
            ).fetchall()
            missing_ids = [row["id"] for row in rows if row["audio_path"] not in existing_paths]
            if missing_ids:
                placeholders = ", ".join("?" for _ in missing_ids)
                self._db.execute(
                    f"UPDATE songs SET audio_path = NULL, audio_mtime = NULL, audio_size = NULL,"
                    f" audio_card = NULL, audio_details = NULL, has_cover = NULL"
                    f" WHERE id IN ({placeholders})",
                    missing_ids,
                )
        return len(missing_ids)

    def drop_spotify_jobs(self, spotify_id: str) -> None:
        with self._lock, self._db:
            self._db.execute("DELETE FROM jobs WHERE spotify_id = ?", (spotify_id,))

    def drop_video_jobs(self, video_id: str) -> None:
        with self._lock, self._db:
            self._db.execute("DELETE FROM jobs WHERE video_id = ? AND spotify_id IS NULL", (video_id,))

    def mark_owned(self, video_id: str, artist: str, title: str, spotify_id: str | None) -> None:
        norm_title = normalize(title)
        norm_artist = normalize(artist)
        with self._lock, self._db:
            if spotify_id:
                self._db.execute(
                    "UPDATE songs SET video_id = coalesce(video_id, ?) WHERE spotify_id = ?",
                    (video_id, spotify_id),
                )
            self._db.execute(
                "INSERT OR IGNORE INTO songs"
                " (video_id, artist_key, title_key, spotify_id, artist, title)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                (video_id, norm_artist, norm_title, spotify_id, artist, title),
            )
            self._db.execute("DELETE FROM review WHERE video_id = ?", (video_id,))

    # Jobs

    def enqueue(
        self,
        video_id: str,
        artist: str,
        title: str,
        *,
        source: str,
        spotify_url: str | None = None,
        spotify_id: str | None = None,
        format: str | None = None,
        premium: int | None = None,
    ) -> Job:
        now = _now()
        with self._lock, self._db:
            active = self._active(video_id)
            if active is not None:
                return active
            cursor = self._db.execute(
                "INSERT INTO jobs (video_id, artist, title, spotify_url, spotify_id,"
                " status, source, format, premium, created_at, updated_at)"
                " VALUES (?, ?, ?, ?, ?, 'queued', ?, ?, ?, ?, ?)",
                (video_id, artist, title, spotify_url, spotify_id, source, format, premium, now, now),
            )
            job = self._job(cursor.lastrowid)
        assert job is not None
        return job

    def claim_next(self) -> Job | None:
        with self._lock, self._db:
            row = self._db.execute(
                "SELECT id FROM jobs WHERE status = 'queued' ORDER BY id LIMIT 1"
            ).fetchone()
            if row is None:
                return None
            self._db.execute(
                "UPDATE jobs SET status = 'running', updated_at = ? WHERE id = ?",
                (_now(), row["id"]),
            )
            return self._job(row["id"])

    def set_candidate(self, job_id: int, spotify_url: str, spotify_id: str) -> None:
        with self._lock, self._db:
            self._db.execute(
                "UPDATE jobs SET spotify_url = ?, spotify_id = ?, updated_at = ? WHERE id = ?",
                (spotify_url, spotify_id, _now(), job_id),
            )

    def finish(self, job_id: int, *, ok: bool, error: str | None = None) -> None:
        with self._lock, self._db:
            self._db.execute(
                "UPDATE jobs SET status = ?, error = ?, updated_at = ? WHERE id = ?",
                ("succeeded" if ok else "failed", error, _now(), job_id),
            )

    def get_job(self, job_id: int) -> Job | None:
        with self._lock:
            return self._job(job_id)

    def list_jobs(self, limit: int = 100) -> list[Job]:
        with self._lock:
            rows = self._db.execute(
                "SELECT * FROM jobs ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
        return [Job(**dict(row)) for row in rows]

    def active_job(self, video_id: str) -> Job | None:
        with self._lock:
            return self._active(video_id)

    def count_active_jobs(self) -> int:
        with self._lock:
            row = self._db.execute(
                "SELECT COUNT(*) FROM jobs WHERE status IN ('queued', 'running')"
            ).fetchone()
        return int(row[0]) if row is not None else 0

    def retry(self, job_id: int) -> Job | None:
        with self._lock, self._db:
            job = self._job(job_id)
            if job is None or job.status != "failed":
                return job
            active = self._active(job.video_id)
            if active is not None:
                return active
            self._db.execute(
                "UPDATE jobs SET status = 'queued', error = NULL, updated_at = ? WHERE id = ?",
                (_now(), job_id),
            )
            return self._job(job_id)

    def requeue_running(self) -> None:
        with self._lock, self._db:
            self._db.execute(
                "UPDATE jobs SET status = 'queued', updated_at = ? WHERE status = 'running'",
                (_now(),),
            )

    # Review list

    def add_review(
        self,
        video_id: str,
        heard_artist: str,
        heard_title: str,
        *,
        reason: str,
        candidate_artist: str | None = None,
        candidate_title: str | None = None,
        spotify_url: str | None = None,
        spotify_id: str | None = None,
        replace: bool = False,
    ) -> None:
        with self._lock, self._db:
            if not replace:
                existing = self._db.execute("SELECT reason FROM review WHERE video_id = ?", (video_id,)).fetchone()
                if existing and existing[0] != "pending":
                    return
            self._db.execute(
                "INSERT OR REPLACE INTO review (video_id, heard_artist, heard_title, candidate_artist,"
                " candidate_title, spotify_url, spotify_id, reason, created_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    video_id, heard_artist, heard_title, candidate_artist, candidate_title,
                    spotify_url, spotify_id, reason, _now(),
                ),
            )

    def get_review(self, video_id: str) -> ReviewRow | None:
        with self._lock:
            row = self._db.execute(
                "SELECT * FROM review WHERE video_id = ?", (video_id,)
            ).fetchone()
        return ReviewRow(**dict(row)) if row else None

    def list_review(self) -> list[ReviewRow]:
        with self._lock:
            rows = self._db.execute("SELECT * FROM review ORDER BY created_at").fetchall()
        return [ReviewRow(**dict(row)) for row in rows]

    def dismiss(self, video_id: str, *, permanent: bool = False) -> None:
        with self._lock, self._db:
            self._db.execute("DELETE FROM review WHERE video_id = ?", (video_id,))
            if permanent:
                self._db.execute(
                    "INSERT OR IGNORE INTO dismissed (video_id, created_at) VALUES (?, ?)",
                    (video_id, _now()),
                )

    def is_dismissed(self, video_id: str) -> bool:
        with self._lock:
            row = self._db.execute(
                "SELECT 1 FROM dismissed WHERE video_id = ?", (video_id,)
            ).fetchone()
        return row is not None

    # Watched playlists

    def watch_playlist(self, playlist_id: str, title: str, *, auto_queue: bool = False) -> None:
        with self._lock, self._db:
            self._db.execute(
                "INSERT INTO watched_playlists (playlist_id, title, auto_queue, created_at)"
                " VALUES (?, ?, ?, ?)"
                " ON CONFLICT(playlist_id) DO UPDATE SET title = excluded.title, auto_queue = excluded.auto_queue",
                (playlist_id, title, int(auto_queue), _now()),
            )

    def unwatch_playlist(self, playlist_id: str) -> None:
        with self._lock, self._db:
            self._db.execute("DELETE FROM watched_playlists WHERE playlist_id = ?", (playlist_id,))

    def list_watched_playlists(self) -> list[WatchedPlaylist]:
        with self._lock:
            rows = self._db.execute(
                "SELECT playlist_id, title, auto_queue, last_synced_at, created_at"
                " FROM watched_playlists ORDER BY created_at"
            ).fetchall()
        return [
            WatchedPlaylist(
                playlist_id=row["playlist_id"],
                title=row["title"],
                auto_queue=bool(row["auto_queue"]),
                last_synced_at=row["last_synced_at"],
                created_at=row["created_at"],
            )
            for row in rows
        ]

    def set_watched_playlist_synced(
        self, playlist_id: str, synced_at: str, *, title: str | None = None
    ) -> None:
        with self._lock, self._db:
            if title:
                self._db.execute(
                    "UPDATE watched_playlists SET last_synced_at = ?, title = ? WHERE playlist_id = ?",
                    (synced_at, title, playlist_id),
                )
            else:
                self._db.execute(
                    "UPDATE watched_playlists SET last_synced_at = ? WHERE playlist_id = ?",
                    (synced_at, playlist_id),
                )

    # Followed artists

    def follow_artist(self, channel_id: str, name: str) -> None:
        with self._lock, self._db:
            self._db.execute(
                "INSERT INTO followed_artists (channel_id, name, created_at) VALUES (?, ?, ?)"
                " ON CONFLICT(channel_id) DO UPDATE SET name = excluded.name",
                (channel_id, name, _now()),
            )

    def unfollow_artist(self, channel_id: str) -> None:
        with self._lock, self._db:
            self._db.execute("DELETE FROM followed_artists WHERE channel_id = ?", (channel_id,))

    def list_followed_artists(self) -> list[FollowedArtist]:
        with self._lock:
            rows = self._db.execute(
                "SELECT channel_id, name, last_synced_at, created_at"
                " FROM followed_artists ORDER BY created_at"
            ).fetchall()
        return [
            FollowedArtist(
                channel_id=row["channel_id"],
                name=row["name"],
                last_synced_at=row["last_synced_at"],
                created_at=row["created_at"],
            )
            for row in rows
        ]

    def set_followed_artist_synced(
        self, channel_id: str, synced_at: str, *, name: str | None = None
    ) -> None:
        with self._lock, self._db:
            if name:
                self._db.execute(
                    "UPDATE followed_artists SET last_synced_at = ?, name = ? WHERE channel_id = ?",
                    (synced_at, name, channel_id),
                )
            else:
                self._db.execute(
                    "UPDATE followed_artists SET last_synced_at = ? WHERE channel_id = ?",
                    (synced_at, channel_id),
                )

    # Activity events

    def record_event(self, song: str, kind: str, detail: str = "") -> None:
        with self._lock, self._db:
            self._db.execute(
                "INSERT INTO events (created_at, song, kind, detail) VALUES (?, ?, ?, ?)",
                (_now(), song, kind, detail),
            )

    def list_events(self, limit: int = 200) -> list[Event]:
        with self._lock:
            rows = self._db.execute(
                "SELECT id, created_at, song, kind, detail FROM events ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [Event(**dict(row)) for row in rows]

    def housekeep(
        self,
        *,
        keep_days: int = 30,
        keep_jobs: int = 500,
        now: datetime | None = None,
    ) -> tuple[int, int]:
        """Trim history pulls/items older than keep_days and finished jobs beyond keep_jobs."""
        cutoff = ((now or datetime.now(timezone.utc)) - timedelta(days=keep_days)).isoformat(timespec="seconds")
        with self._lock, self._db:
            self._db.execute(
                "DELETE FROM history_items WHERE pull_id IN "
                "(SELECT id FROM history_pulls WHERE started_at < ?)",
                (cutoff,),
            )
            pulls_deleted = self._db.execute(
                "DELETE FROM history_pulls WHERE started_at < ?",
                (cutoff,),
            ).rowcount
            self._db.execute("DELETE FROM events WHERE created_at < ?", (cutoff,))
            jobs_deleted = 0
            if keep_jobs >= 0:
                jobs_deleted = self._db.execute(
                    "DELETE FROM jobs WHERE status IN ('succeeded', 'failed') "
                    "AND id NOT IN ("
                    "SELECT id FROM jobs WHERE status IN ('succeeded', 'failed') "
                    "ORDER BY id DESC LIMIT ?"
                    ")",
                    (keep_jobs,),
                ).rowcount
        return pulls_deleted, jobs_deleted

    # History

    def record_pull(
        self, started_at: str, status: str, error: str | None, items: list[HistoryItem]
    ) -> int:
        with self._lock, self._db:
            cursor = self._db.execute(
                "INSERT INTO history_pulls (started_at, status, error) VALUES (?, ?, ?)",
                (started_at, status, error),
            )
            pull_id = cursor.lastrowid
            self._db.executemany(
                "INSERT INTO history_items (pull_id, video_id, artist, title, played_label)"
                " VALUES (?, ?, ?, ?, ?)",
                [
                    (pull_id, item.video_id, item.artist, item.title, item.played_label)
                    for item in items
                ],
            )
        assert pull_id is not None
        return pull_id

    def has_ok_pull_on(self, local_date: str) -> bool:
        with self._lock:
            row = self._db.execute(
                "SELECT 1 FROM history_pulls"
                " WHERE status = 'ok' AND substr(started_at, 1, 10) = ?",
                (local_date,),
            ).fetchone()
        return row is not None

    def last_pull(self) -> HistoryPull | None:
        with self._lock:
            row = self._db.execute(
                "SELECT id, started_at, status, error FROM history_pulls"
                " ORDER BY id DESC LIMIT 1"
            ).fetchone()
        if row is None:
            return None
        return HistoryPull(row[0], row[1], row[2], row[3])

    def count_recent(
        self, pull_id: int, labels: tuple[str, ...] = RECENT_PLAYED_LABELS
    ) -> list[HeardCount]:
        if not labels:
            return []
        placeholders = ", ".join("?" for _ in labels)
        with self._lock:
            rows = self._db.execute(
                f"SELECT video_id, artist, title, COUNT(*) AS plays FROM history_items"
                f" WHERE pull_id = ? AND played_label IN ({placeholders})"
                f" GROUP BY video_id ORDER BY MIN(id)",
                (pull_id, *labels),
            ).fetchall()
        return [HeardCount(**dict(row)) for row in rows]

    def count_today(self, pull_id: int) -> list[HeardCount]:
        return self.count_recent(pull_id, labels=("Today",))

    def record_task_run(self, name: str, interval_hours: int) -> None:
        with self._lock, self._db:
            self._db.execute(
                "INSERT INTO scheduled_tasks (name, interval_hours, last_run)"
                " VALUES (?, ?, ?)"
                " ON CONFLICT(name) DO UPDATE SET"
                " interval_hours = excluded.interval_hours,"
                " last_run = excluded.last_run",
                (name, interval_hours, _now()),
            )

    def list_tasks(self) -> list[ScheduledTask]:
        defaults = [
            ("history_pull", 24),
            ("library_scan", 24),
            ("housekeeping", 24),
            ("recycle_sweep", 24),
            ("backup", 168),
        ]
        with self._lock:
            rows = {
                row["name"]: ScheduledTask(row["name"], row["interval_hours"], row["last_run"])
                for row in self._db.execute("SELECT name, interval_hours, last_run FROM scheduled_tasks")
            }
        return [
            rows.get(name, ScheduledTask(name, hours, None))
            for name, hours in defaults
        ]

    # Auth user

    def get_auth_user(self) -> AuthUser | None:
        with self._lock:
            row = self._db.execute(
                "SELECT username, password_hash, salt, api_key, api_key_rotated, session_secret, updated_at"
                " FROM auth_user WHERE id = 1"
            ).fetchone()
        return _row_to_auth_user(row) if row is not None else None

    def needs_setup(self) -> bool:
        """Return True when no user or no password has been configured yet."""
        user = self.get_auth_user()
        return user is None or not user.password_hash

    def ensure_auth_user(
        self,
        default_password: str,
        default_username: str = "admin",
        default_api_key: str = "",
        reset_password: bool = False,
    ) -> AuthUser:
        if not reset_password:
            existing = self.get_auth_user()
            if existing is not None:
                return existing
        norm_username = default_username.strip().lower() or "admin"
        with self._lock, self._db:
            row = self._db.execute(
                "SELECT username, password_hash, salt, api_key, api_key_rotated, session_secret, updated_at"
                " FROM auth_user WHERE id = 1"
            ).fetchone()
            if row is None:
                salt = secrets.token_hex(16)
                pw_hash = hash_password(default_password, salt)
                api_key = default_api_key.strip() or secrets.token_hex(16)
                session_secret = secrets.token_hex(32)
                now = _now()
                self._db.execute(
                    "INSERT INTO auth_user"
                    " (id, username, password_hash, salt, api_key, api_key_rotated, session_secret, updated_at)"
                    " VALUES (1, ?, ?, ?, ?, 0, ?, ?)",
                    (norm_username, pw_hash, salt, api_key, session_secret, now),
                )
                return AuthUser(
                    username=norm_username,
                    password_hash=pw_hash,
                    salt=salt,
                    api_key=api_key,
                    api_key_rotated=False,
                    updated_at=now,
                    session_secret=session_secret,
                )
            if reset_password:
                salt = secrets.token_hex(16)
                pw_hash = hash_password(default_password, salt)
                session_secret = secrets.token_hex(32)
                now = _now()
                self._db.execute(
                    "UPDATE auth_user SET username = ?, password_hash = ?, salt = ?, session_secret = ?, updated_at = ?"
                    " WHERE id = 1",
                    (norm_username, pw_hash, salt, session_secret, now),
                )
                return AuthUser(
                    username=norm_username,
                    password_hash=pw_hash,
                    salt=salt,
                    api_key=row["api_key"],
                    api_key_rotated=bool(row["api_key_rotated"]),
                    updated_at=now,
                    session_secret=session_secret,
                )
            return _row_to_auth_user(row)

    def verify_user(self, username: str, password: str) -> AuthUser | None:
        norm_username = username.strip().lower()
        if not norm_username or not password:
            return None
        user = self.get_auth_user()
        if user is None or not user.password_hash or not user.salt:
            return None
        if not hmac.compare_digest(norm_username.encode("utf-8"), user.username.encode("utf-8")):
            return None
        candidate = hash_password(password, user.salt)
        if not hmac.compare_digest(candidate.encode("utf-8"), user.password_hash.encode("utf-8")):
            return None
        return user

    def create_initial_user(self, username: str, password: str) -> AuthUser:
        """Create the initial auth user once, preserving any existing pre-seeded API key."""
        norm_username = _validate_credentials(username, password)
        with self._lock, self._db:
            row = self._db.execute(
                "SELECT api_key, api_key_rotated, password_hash FROM auth_user WHERE id = 1"
            ).fetchone()
            if row is not None and row["password_hash"]:
                raise SetupAlreadyCompleteError("Setup is already complete")
            salt = secrets.token_hex(16)
            pw_hash = hash_password(password, salt)
            session_secret = secrets.token_hex(32)
            now = _now()
            return self._upsert_auth_user_locked(
                row, norm_username, pw_hash, salt, session_secret, now
            )

    def ensure_session_secret(self) -> AuthUser | None:
        """Backfill session_secret for an existing user (e.g. migrated from schema v7)."""
        with self._lock, self._db:
            row = self._db.execute(
                "SELECT username, password_hash, salt, api_key, api_key_rotated, session_secret, updated_at"
                " FROM auth_user WHERE id = 1"
            ).fetchone()
            if row is None:
                return None
            user = _row_to_auth_user(row)
            if user.session_secret:
                return user
            session_secret = secrets.token_hex(32)
            now = _now()
            self._db.execute(
                "UPDATE auth_user SET session_secret = ?, updated_at = ? WHERE id = 1",
                (session_secret, now),
            )
        return AuthUser(
            username=user.username,
            password_hash=user.password_hash,
            salt=user.salt,
            api_key=user.api_key,
            api_key_rotated=user.api_key_rotated,
            updated_at=now,
            session_secret=session_secret,
        )

    def reset_auth_password(self) -> None:
        """Clear password credentials to re-enter setup while preserving API key and rotation state."""
        session_secret = secrets.token_hex(32)
        now = _now()
        with self._lock, self._db:
            self._db.execute(
                "UPDATE auth_user SET password_hash = '', salt = '', session_secret = ?, updated_at = ?"
                " WHERE id = 1",
                (session_secret, now),
            )

    def update_credentials(self, username: str, password: str) -> AuthUser:
        norm_username = _validate_credentials(username, password)
        salt = secrets.token_hex(16)
        pw_hash = hash_password(password, salt)
        session_secret = secrets.token_hex(32)
        now = _now()
        with self._lock, self._db:
            row = self._db.execute(
                "SELECT api_key, api_key_rotated FROM auth_user WHERE id = 1"
            ).fetchone()
            return self._upsert_auth_user_locked(
                row, norm_username, pw_hash, salt, session_secret, now
            )

    def _upsert_auth_user_locked(
        self,
        row: sqlite3.Row | None,
        norm_username: str,
        pw_hash: str,
        salt: str,
        session_secret: str,
        now: str,
    ) -> AuthUser:
        if row is None:
            api_key = secrets.token_hex(16)
            rotated = False
            self._db.execute(
                "INSERT INTO auth_user"
                " (id, username, password_hash, salt, api_key, api_key_rotated, session_secret, updated_at)"
                " VALUES (1, ?, ?, ?, ?, 0, ?, ?)",
                (norm_username, pw_hash, salt, api_key, session_secret, now),
            )
        else:
            api_key = row["api_key"]
            rotated = bool(row["api_key_rotated"])
            self._db.execute(
                "UPDATE auth_user SET username = ?, password_hash = ?, salt = ?, session_secret = ?, updated_at = ?"
                " WHERE id = 1",
                (norm_username, pw_hash, salt, session_secret, now),
            )
        return AuthUser(
            username=norm_username,
            password_hash=pw_hash,
            salt=salt,
            api_key=api_key,
            api_key_rotated=rotated,
            updated_at=now,
            session_secret=session_secret,
        )

    def reset_api_key(self) -> str:
        new_key = secrets.token_hex(16)
        now = _now()
        with self._lock, self._db:
            cur = self._db.execute(
                "UPDATE auth_user SET api_key = ?, api_key_rotated = 1, updated_at = ? WHERE id = 1",
                (new_key, now),
            )
            if cur.rowcount == 0:
                raise RuntimeError("auth_user is not initialized")
        return new_key

    # Helpers that expect the lock to be held

    def _job(self, job_id: int | None) -> Job | None:
        row = self._db.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        return Job(**dict(row)) if row else None

    def _active(self, video_id: str) -> Job | None:
        row = self._db.execute(
            "SELECT * FROM jobs WHERE video_id = ? AND status IN ('queued', 'running')",
            (video_id,),
        ).fetchone()
        return Job(**dict(row)) if row else None
