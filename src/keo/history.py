"""The daily YouTube Music history check.

Songs played today are matched on Spotify and added to the review list for a
tap.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from datetime import date, datetime
from zoneinfo import ZoneInfo

from keo.catalog import Catalog, CatalogError
from keo.cookies import CookieGate
from keo.jobs import Worker
from keo.library import Library
from keo.match import Name, is_close
from keo.spotdl_cmd import Spotdl

log = logging.getLogger(__name__)

PLAYS_TO_QUALIFY = 1
CHECK_EVERY_SECONDS = 60


class HistoryProgress:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.active: bool = False
        self.stage: str = "idle"
        self.total: int = 0
        self.current: int = 0

    @property
    def percent(self) -> int:
        with self._lock:
            if not self.active or self.total <= 0:
                return 0
            return min(100, int((self.current / self.total) * 100))

    def start_fetching(self) -> None:
        with self._lock:
            self.active = True
            self.stage = "fetching"
            self.total = 0
            self.current = 0

    def start_processing(self, total: int) -> None:
        with self._lock:
            self.active = True
            self.stage = "processing"
            self.total = total
            self.current = 0

    def step(self) -> None:
        with self._lock:
            self.current += 1

    def finish(self) -> None:
        with self._lock:
            self.active = False
            self.stage = "idle"

    def snapshot(self) -> dict[str, object]:
        with self._lock:
            pct = 0 if not self.active or self.total <= 0 else min(100, int((self.current / self.total) * 100))
            return {
                "active": self.active,
                "stage": self.stage,
                "current": self.current,
                "total": self.total,
                "percent": pct,
            }


def decide(
    video_id: str,
    artist: str,
    title: str,
    *,
    library: Library,
    worker: Worker,
    spotdl: Spotdl,
    cookies: CookieGate,
    source: str = "history",
    replace_review: bool = False,
) -> str:
    candidate = spotdl.lookup(f"{artist} - {title}").candidate
    if candidate is None:
        log.info("No Spotify metadata found for '%s - %s' (%s) -> review (no_metadata)", artist, title, video_id)
        library.add_review(video_id, artist, title, reason="no_metadata", replace=replace_review)
        return "review"
    if library.is_owned_spotify(candidate.song_id):
        log.info("Spotify candidate '%s - %s' (%s) already owned in library -> mark_owned", candidate.primary_artist, candidate.name, candidate.song_id)
        library.mark_owned(video_id, artist, title, candidate.song_id)
        return "owned"
    reason = (
        "close_match"
        if is_close(Name(artist, title), Name(candidate.primary_artist, candidate.name))
        else "weak_match"
    )
    log.info(
        "Matched '%s - %s' (%s) -> '%s - %s' (%s) [%s] -> review",
        artist, title, video_id, candidate.primary_artist, candidate.name, candidate.song_id, reason,
    )
    library.add_review(
        video_id, artist, title,
        reason=reason,
        candidate_artist=candidate.primary_artist,
        candidate_title=candidate.name,
        spotify_url=candidate.url,
        spotify_id=candidate.song_id,
        replace=replace_review,
    )
    return "review"


def run_pull(
    *,
    catalog: Catalog,
    library: Library,
    worker: Worker,
    spotdl: Spotdl,
    cookies: CookieGate,
    now: datetime,
    progress: HistoryProgress | None = None,
) -> int | None:
    if progress:
        progress.start_fetching()
    started_at = now.isoformat(timespec="seconds")
    log.info("Starting history pull at %s", started_at)
    try:
        try:
            items = catalog.history()
        except CatalogError as exc:
            log.warning("History pull failed: %s", exc)
            library.record_pull(started_at, "failed", str(exc), [])
            return None
        pull_id = library.record_pull(started_at, "ok", None, items)
        recent = library.count_recent(pull_id)
        if progress:
            progress.start_processing(len(recent))
        log.info(
            "Fetched %d history items from YouTube Music (%d in recent shelves: Today, Yesterday, This week)",
            len(items), len(recent),
        )
        added = 0
        for heard in recent:
            try:
                if heard.plays < PLAYS_TO_QUALIFY:
                    continue
                if library.is_owned(heard.video_id, heard.artist, heard.title):
                    log.info("Skipping '%s - %s' (%s): already in library", heard.artist, heard.title, heard.video_id)
                    continue
                if library.active_job(heard.video_id) is not None:
                    log.info("Skipping '%s - %s' (%s): download already active", heard.artist, heard.title, heard.video_id)
                    continue
                try:
                    log.info("Looking up '%s - %s' (%s) on Spotify...", heard.artist, heard.title, heard.video_id)
                    outcome = decide(
                        heard.video_id, heard.artist, heard.title,
                        library=library, worker=worker, spotdl=spotdl, cookies=cookies,
                    )
                    log.info("Decision for '%s - %s' (%s): %s", heard.artist, heard.title, heard.video_id, outcome)
                    if outcome == "review":
                        added += 1
                except Exception:
                    log.exception("History decision failed for %s", heard.video_id)
            finally:
                if progress:
                    progress.step()
        log.info("History pull completed: %d new item(s) added to review", added)
        return pull_id
    finally:
        if progress:
            progress.finish()


def is_due(now: datetime, hour: int, library: Library, last_attempt: date | None) -> bool:
    today = now.date()
    return (
        now.hour >= hour
        and last_attempt != today
        and not library.has_ok_pull_on(today.isoformat())
    )


class Scheduler:
    def __init__(
        self,
        *,
        catalog: Catalog,
        library: Library,
        worker: Worker,
        spotdl: Spotdl,
        cookies: CookieGate,
        timezone: str,
        hour: int,
        clock: Callable[[], datetime] | None = None,
        progress: HistoryProgress | None = None,
    ) -> None:
        zone = ZoneInfo(timezone)
        self._catalog = catalog
        self._library = library
        self._worker = worker
        self._spotdl = spotdl
        self._cookies = cookies
        self._hour = hour
        self._clock = clock or (lambda: datetime.now(zone))
        self._progress = progress
        self._last_attempt: date | None = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def tick(self) -> bool:
        now = self._clock()
        if not is_due(now, self._hour, self._library, self._last_attempt):
            return False
        self._last_attempt = now.date()
        run_pull(
            catalog=self._catalog, library=self._library,
            worker=self._worker, spotdl=self._spotdl, cookies=self._cookies, now=now,
            progress=self._progress,
        )
        return True

    def start(self) -> None:
        self._thread = threading.Thread(target=self._loop, name="keo-history", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5)

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self.tick()
            except Exception:
                log.exception("History check crashed")
            self._stop.wait(CHECK_EVERY_SECONDS)
