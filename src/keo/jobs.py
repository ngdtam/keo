"""The download queue. One worker thread runs one job at a time."""

from __future__ import annotations

import logging
import threading
from pathlib import Path

from keo.config import cookie_ready
from keo.library import Job, Library, list_audio
from keo.spotdl_cmd import Spotdl

log = logging.getLogger(__name__)
IDLE_WAIT_SECONDS = 5


class Worker:
    def __init__(self, library: Library, spotdl: Spotdl, music_dir: Path | None = None) -> None:
        self._library = library
        self._spotdl = spotdl
        self._music = music_dir
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def submit(
        self,
        video_id: str,
        artist: str,
        title: str,
        *,
        source: str = "manual",
        spotify_url: str | None = None,
        spotify_id: str | None = None,
    ) -> Job | None:
        """Queue one download. Returns None when the song is already owned."""
        if self._library.is_owned(video_id, artist, title):
            return None
        settings = self._spotdl.settings
        premium = int(cookie_ready(settings) and settings.format in {"opus", "m4a"})
        job = self._library.enqueue(
            video_id, artist, title,
            source=source, spotify_url=spotify_url, spotify_id=spotify_id,
            format=settings.format, premium=premium,
        )
        self._wake.set()
        return job

    def retry(self, job_id: int) -> Job | None:
        job = self._library.retry(job_id)
        self._wake.set()
        return job

    def run_once(self) -> bool:
        job = self._library.claim_next()
        if job is None:
            return False
        try:
            self._process(job)
        except Exception as exc:
            log.exception("Job %s crashed", job.id)
            self._library.finish(job.id, ok=False, error=f"Kéo error: {exc}")
        return True

    def start(self) -> None:
        self._thread = threading.Thread(target=self._loop, name="keo-worker", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()
        if self._thread is not None:
            self._thread.join(timeout=5)

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                if self.run_once():
                    continue
            except Exception:
                log.exception("Download worker crashed")
                if self._stop.is_set():
                    break
            self._wake.wait(timeout=IDLE_WAIT_SECONDS)
            self._wake.clear()

    def _process(self, job: Job) -> None:
        url, spotify_id = job.spotify_url, job.spotify_id
        if url is None:
            lookup = self._spotdl.lookup(f"{job.artist} - {job.title}")
            if lookup.candidate is None:
                self._library.finish(
                    job.id,
                    ok=False,
                    error=f"No Spotify metadata for {job.artist} - {job.title}\n{lookup.output}",
                )
                return
            url, spotify_id = lookup.candidate.url, lookup.candidate.song_id
            self._library.set_candidate(job.id, url, spotify_id)

        if spotify_id and self._library.is_owned_spotify(spotify_id):
            self._library.mark_owned(job.video_id, job.artist, job.title, spotify_id)
            self._library.finish(job.id, ok=True)
            return

        before = list_audio(self._music) if self._music else set()
        result = self._spotdl.download(url, spotify_id)
        if result.ok:
            self._library.mark_owned(job.video_id, job.artist, job.title, spotify_id)
            appeared = list_audio(self._music) - before if self._music else set()
            if spotify_id and len(appeared) == 1:
                self._library.set_audio_path(spotify_id, str(appeared.pop()))
            self._library.finish(job.id, ok=True)
        else:
            self._library.finish(job.id, ok=False, error=result.output)
