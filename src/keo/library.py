"""SQLite store for owned songs, download jobs, the review list, and history pulls.

This is the only module that writes the database. One lock serializes access
because the web threads, the worker, and the scheduler share one connection.
"""

from __future__ import annotations

import re
import sqlite3
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from keo.archive import archive_ids, drop_track
from keo.match import normalize
from keo.records import HistoryItem

AUDIO_EXT = {".mp3", ".m4a", ".opus", ".ogg", ".flac", ".wav"}


def list_audio(folder: Path) -> set[Path]:
    if not folder.is_dir():
        return set()
    return {path for path in folder.iterdir() if path.is_file() and path.suffix.lower() in AUDIO_EXT}


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
    updated_at TEXT NOT NULL
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
"""


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


class Library:
    def __init__(self, db_path: Path, archive_path: Path) -> None:
        self._archive_path = archive_path
        self._lock = threading.Lock()
        self._db = sqlite3.connect(db_path, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._db.execute("PRAGMA journal_mode = WAL")
        self._db.executescript(SCHEMA)
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

    def dismiss(self, video_id: str) -> None:
        with self._lock, self._db:
            self._db.execute("DELETE FROM review WHERE video_id = ?", (video_id,))

    def is_dismissed(self, video_id: str) -> bool:
        with self._lock:
            row = self._db.execute(
                "SELECT 1 FROM dismissed WHERE video_id = ?", (video_id,)
            ).fetchone()
        return row is not None

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
