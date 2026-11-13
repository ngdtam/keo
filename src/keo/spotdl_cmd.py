"""Build and run spotDL commands. Only one spotDL process runs at a time.

spotDL exits 0 even when the one song fails, so a download counts as finished
only when the Spotify URL also landed in the archive file.
"""

from __future__ import annotations

import json
import os
import subprocess
import threading
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from keo.archive import archive_ids, track_id
from keo.config import FORMATS, SpotdlSettings
from keo.cookies import settings_are_premium

Operation = Literal["save", "download"]
Runner = Callable[..., subprocess.CompletedProcess]

SAVE_TIMEOUT_SECONDS = 300
DOWNLOAD_TIMEOUT_SECONDS = 1800
TAIL_LINES = 40


@dataclass(frozen=True)
class Paths:
    music_dir: Path
    data_dir: Path

    @property
    def archive(self) -> Path:
        return self.data_dir / "archive.txt"

    @property
    def home(self) -> Path:
        return self.data_dir / "spotdl-home"


@dataclass(frozen=True)
class Candidate:
    name: str
    artist: str
    artists: tuple[str, ...]
    url: str
    song_id: str

    @property
    def primary_artist(self) -> str:
        return self.artists[0] if self.artists else self.artist


@dataclass(frozen=True)
class Lookup:
    candidate: Candidate | None
    output: str


@dataclass(frozen=True)
class DownloadResult:
    ok: bool
    output: str


def build_args(
    settings: SpotdlSettings, paths: Paths, operation: Operation, query: str
) -> list[str]:
    s = settings
    args = [operation, query]
    if operation == "save":
        args += ["--save-file", "-"]
        _flag(args, "--preload", s.preload)
    else:
        args += [
            "--output", str(paths.music_dir / s.output_template),
            "--archive", str(paths.archive),
            "--overwrite", "skip",
            "--detect-formats", *FORMATS,
            "--format", s.format,
            "--bitrate", s.bitrate,
            "--id3-separator", s.id3_separator,
        ]
        _flag(args, "--sponsor-block", s.sponsor_block)
        _value(args, "--ffmpeg-args", s.ffmpeg_args)
        _flag(args, "--generate-lrc", s.generate_lrc)
        _flag(args, "--skip-album-art", s.skip_album_art)
        _flag(args, "--skip-explicit", s.skip_explicit)
        _value(args, "--restrict", s.restrict)
        if s.max_filename_length:
            args += ["--max-filename-length", str(s.max_filename_length)]
        _value(args, "--yt-dlp-args", s.yt_dlp_args)
    args += [
        "--threads", "1",
        "--max-retries", str(s.max_retries),
        "--audio", *s.audio,
        "--lyrics", *s.lyrics,
    ]
    _value(args, "--genius-access-token", s.genius_access_token)
    _value(args, "--search-query", s.search_query)
    _value(args, "--album-type", s.album_type)
    _flag(args, "--only-verified-results", s.only_verified_results)
    _flag(args, "--dont-filter-results", s.dont_filter_results)
    _value(args, "--proxy", s.proxy)
    _value(args, "--client-id", s.client_id)
    _value(args, "--client-secret", s.client_secret)
    if settings_are_premium(s):
        _value(args, "--cookie-file", s.cookie_file)
    return args


def parse_save_output(stdout: str) -> Candidate | None:
    decoder = json.JSONDecoder()
    start = stdout.find("[")
    while start != -1:
        try:
            value, _ = decoder.raw_decode(stdout, start)
        except json.JSONDecodeError:
            value = None
        if isinstance(value, list) and (not value or isinstance(value[0], dict)):
            return _candidate(value[0]) if value else None
        start = stdout.find("[", start + 1)
    return None


def tail(text: str, lines: int = TAIL_LINES) -> str:
    return "\n".join(text.strip().splitlines()[-lines:])


class Spotdl:
    def __init__(
        self,
        settings: SpotdlSettings,
        paths: Paths,
        binary: str = "spotdl",
        runner: Runner = subprocess.run,
    ) -> None:
        self._settings = settings
        self._paths = paths
        self._binary = binary
        self._runner = runner
        self._lock = threading.Lock()

    @property
    def settings(self) -> SpotdlSettings:
        return self._settings

    def use(self, settings: SpotdlSettings) -> None:
        self._settings = settings

    def lookup(self, query: str) -> Lookup:
        proc = self._run("save", query, SAVE_TIMEOUT_SECONDS)
        if proc is None:
            return Lookup(None, "spotDL lookup timed out")
        candidate = parse_save_output(proc.stdout) if proc.returncode == 0 else None
        return Lookup(candidate, tail(f"{proc.stdout}\n{proc.stderr}"))

    def download(
        self, url: str, spotify_id: str | None, *, video_id: str | None = None
    ) -> DownloadResult:
        query = f"https://music.youtube.com/watch?v={video_id}|{url}" if video_id else url
        proc = self._run("download", query, DOWNLOAD_TIMEOUT_SECONDS)
        if proc is None:
            return DownloadResult(False, "spotDL download timed out")
        song_id = spotify_id or track_id(url)
        finished = proc.returncode == 0 and song_id in archive_ids(self._paths.archive)
        return DownloadResult(finished, tail(f"{proc.stdout}\n{proc.stderr}"))

    def _run(
        self, operation: Operation, query: str, timeout: int
    ) -> subprocess.CompletedProcess | None:
        args = [self._binary, *build_args(self._settings, self._paths, operation, query)]
        with self._lock:
            _prepare_home(self._paths.home)
            try:
                return self._runner(
                    args,
                    capture_output=True,
                    text=True,
                    timeout=timeout,
                    env={**os.environ, "HOME": str(self._paths.home)},
                    check=False,
                )
            except subprocess.TimeoutExpired:
                return None


def _prepare_home(home: Path) -> None:
    for folder in (home / ".config" / "spotdl", home / ".spotdl"):
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "config.json").write_text('{"load_config": false}\n', encoding="utf-8")


def _candidate(data: dict[str, Any]) -> Candidate | None:
    url, song_id = data.get("url"), data.get("song_id")
    if not url or not song_id:
        return None
    artists = tuple(a for a in data.get("artists") or [] if isinstance(a, str))
    return Candidate(
        name=data.get("name") or "",
        artist=data.get("artist") or "",
        artists=artists,
        url=url,
        song_id=song_id,
    )


def _flag(args: list[str], flag: str, enabled: bool) -> None:
    if enabled:
        args.append(flag)


def _value(args: list[str], flag: str, value: str) -> None:
    if value:
        args += [flag, value]
