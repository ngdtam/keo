"""The download queue. One worker thread runs one job at a time."""

from __future__ import annotations

import logging
import threading
from pathlib import Path

from keo.config import cookie_ready
from keo.cookies import CookieGate, settings_are_premium
from keo.library import Job, Library, list_audio
from keo.match import Name, is_close
from keo.spotdl_cmd import Spotdl
from keo.youtube_dl import Runner, download_youtube_audio

log = logging.getLogger(__name__)
IDLE_WAIT_SECONDS = 5


class Worker:
    def __init__(
        self,
        library: Library,
        spotdl: Spotdl,
        music_dir: Path | None = None,
        *,
        cookies: CookieGate,
        youtube_runner: Runner | None = None,
    ) -> None:
        self._library = library
        self._spotdl = spotdl
        self._music = music_dir
        self.cookies = cookies
        self._youtube_runner = youtube_runner
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
        premium = int(settings_are_premium(settings) and cookie_ready(settings))
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
        if job.source == "youtube":
            self._process_youtube(job)
            return

        reason = self.cookies.allow(self._spotdl.settings)
        if reason is not None:
            self._library.finish(job.id, ok=False, error=reason)
            return

        url, spotify_id = job.spotify_url, job.spotify_id
        if url is None:
            lookup = self._spotdl.lookup(f"{job.artist} - {job.title}")
            cand = lookup.candidate
            if cand is None:
                log.info("No Spotify metadata for '%s - %s' -> falling back to YouTube audio download", job.artist, job.title)
                self._process_youtube(job)
                return
            if not is_close(Name(job.artist, job.title), Name(cand.primary_artist, cand.name)):
                log.info(
                    "Weak Spotify match for '%s - %s' -> candidate '%s - %s' -> falling back to YouTube audio download",
                    job.artist, job.title, cand.primary_artist, cand.name,
                )
                self._process_youtube(job)
                return
            url, spotify_id = cand.url, cand.song_id
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

    def _process_youtube(self, job: Job) -> None:
        before = list_audio(self._music) if self._music else set()
        kwargs = {}
        if self._youtube_runner is not None:
            kwargs["runner"] = self._youtube_runner
        result = download_youtube_audio(
            video_id=job.video_id,
            artist=job.artist,
            title=job.title,
            music_dir=self._music or Path("music"),
            audio_format=job.format or self._spotdl.settings.format or "mp3",
            cookie_file=self._spotdl.settings.cookie_file if self._spotdl.settings.cookie_file else None,
            **kwargs,
        )
        if result.ok:
            self._library.mark_owned(job.video_id, job.artist, job.title, None)
            appeared = list_audio(self._music) - before if self._music else set()
            if len(appeared) == 1:
                self._library.set_audio_path(job.video_id, str(appeared.pop()))
            self._library.finish(job.id, ok=True)
        else:
            self._library.finish(job.id, ok=False, error=result.output)
