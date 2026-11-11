"""Download audio natively from YouTube via yt-dlp."""

from __future__ import annotations

import logging
import re
import shutil
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)

Runner = Callable[..., subprocess.CompletedProcess]
DOWNLOAD_TIMEOUT_SECONDS = 600


@dataclass(frozen=True)
class DownloadResult:
    ok: bool
    output: str


def sanitize_filename(name: str) -> str:
    """Strip or replace characters unsafe across Linux/macOS/Windows filesystems."""
    return re.sub(r'[/\\:*?"<>|]', "_", name).strip()


def build_yt_dlp_args(
    video_id: str,
    artist: str,
    title: str,
    music_dir: Path,
    *,
    audio_format: str = "mp3",
    cookie_file: str | None = None,
) -> list[str]:
    safe_artist = sanitize_filename(artist)
    safe_title = sanitize_filename(title)
    if safe_artist and safe_title:
        output_template = str(music_dir / f"{safe_artist} - {safe_title}.%(ext)s")
    else:
        output_template = str(music_dir / "%(artist,uploader)s - %(title)s.%(ext)s")

    binary = shutil.which("yt-dlp") or "yt-dlp"
    args = [
        binary,
        "--no-progress",
        "--extract-audio",
        "--audio-format", audio_format,
        "--audio-quality", "0",
        "--embed-metadata",
        "--embed-thumbnail",
        "-o", output_template,
    ]
    if cookie_file and Path(cookie_file).is_file():
        args.extend(["--cookies", str(cookie_file)])
    args.append(f"https://www.youtube.com/watch?v={video_id}")
    return args


def download_youtube_audio(
    video_id: str,
    artist: str,
    title: str,
    music_dir: Path,
    *,
    audio_format: str = "mp3",
    cookie_file: str | None = None,
    runner: Runner = subprocess.run,
) -> DownloadResult:
    args = build_yt_dlp_args(
        video_id,
        artist,
        title,
        music_dir,
        audio_format=audio_format,
        cookie_file=cookie_file,
    )
    music_dir.mkdir(parents=True, exist_ok=True)
    try:
        proc = runner(
            args,
            capture_output=True,
            text=True,
            timeout=DOWNLOAD_TIMEOUT_SECONDS,
            check=False,
        )
        output = (proc.stdout or "") + (proc.stderr or "")
        return DownloadResult(ok=proc.returncode == 0, output=output)
    except subprocess.TimeoutExpired:
        return DownloadResult(ok=False, output=f"YouTube download timed out after {DOWNLOAD_TIMEOUT_SECONDS}s")
    except Exception as exc:
        return DownloadResult(ok=False, output=f"Failed to run yt-dlp: {exc}")
