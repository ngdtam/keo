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


def format_output_template(
    template: str,
    artist: str,
    title: str,
    *,
    max_length: int = 0,
) -> str:
    safe_artist = sanitize_filename(artist)
    safe_title = sanitize_filename(title)
    if not (safe_artist and safe_title):
        return "%(artist,uploader)s - %(title)s.%(ext)s"
    rendered = (
        template.replace("{artists}", safe_artist)
        .replace("{artist}", safe_artist)
        .replace("{title}", safe_title)
        .replace(".{output-ext}", "")
        .replace("{output-ext}", "")
    )
    parts = [
        part.strip(" .")
        for part in rendered.replace("\\", "/").split("/")
        if part.strip(" .") and part.strip() not in {".", ".."}
    ]
    if not parts:
        parts = [f"{safe_artist} - {safe_title}"]
    if max_length > 0:
        parts[-1] = parts[-1][:max_length].rstrip(" .") or safe_title
    return f"{'/'.join(parts)}.%(ext)s"


def write_tags(path: Path, *, artist: str, title: str) -> None:
    try:
        import mutagen
        audio = mutagen.File(path, easy=True)
        if audio is None or getattr(audio, "tags", None) is None:
            return
        if artist:
            audio.tags["artist"] = [artist]
        if title:
            audio.tags["title"] = [title]
        audio.save()
    except Exception:
        log.debug("Could not write tags on %s", path, exc_info=True)


def build_yt_dlp_args(
    video_id: str,
    artist: str,
    title: str,
    music_dir: Path,
    *,
    audio_format: str = "mp3",
    cookie_file: str | None = None,
    output_template: str = "{artists} - {title}.{output-ext}",
    max_filename_length: int = 0,
) -> list[str]:
    filename = format_output_template(
        output_template, artist, title, max_length=max_filename_length
    )
    template_path = str(music_dir / filename)

    binary = shutil.which("yt-dlp") or "yt-dlp"
    args = [
        binary,
        "--no-progress",
        "--extract-audio",
        "--audio-format", audio_format,
        "--audio-quality", "0",
        "--embed-metadata",
        "--embed-thumbnail",
        "-o", template_path,
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
    output_template: str = "{artists} - {title}.{output-ext}",
    max_filename_length: int = 0,
    runner: Runner = subprocess.run,
) -> DownloadResult:
    args = build_yt_dlp_args(
        video_id,
        artist,
        title,
        music_dir,
        audio_format=audio_format,
        cookie_file=cookie_file,
        output_template=output_template,
        max_filename_length=max_filename_length,
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

