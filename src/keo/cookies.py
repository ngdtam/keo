"""Decide whether a YouTube Music cookie can start a premium download."""

from __future__ import annotations

import sys
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from keo.config import SpotdlSettings, cookie_ready, is_premium

PROBE_URL = "https://music.youtube.com/watch?v=mrGn459nxGM"
FRESH_SECONDS = 1800
PREMIUM = "premium"
EXPIRED = "expired"
FREE = "free"
UNKNOWN = "unknown"
PREMIUM_FORMAT_IDS = frozenset({"141", "774"})
MISSING_LINE = "Cookie file missing"
FOUND_LINE = "Cookie file found"
PREMIUM_LINE = "Premium"
EXPIRED_LINE = "Cookies expired"
FREE_LINE = "Account is not Premium"
FAILED_LINE = "Cookie check failed"
_LINES = {PREMIUM: PREMIUM_LINE, EXPIRED: EXPIRED_LINE, FREE: FREE_LINE}


@dataclass(frozen=True)
class ProbeResult:
    formats: list[dict[str, Any]] | None
    text: str


def classify(text: str, formats: object) -> str:
    if "cookies are no longer valid" in text or "rotated" in text:
        return EXPIRED
    if not isinstance(formats, list):
        return UNKNOWN
    for item in formats:
        if isinstance(item, dict) and str(item.get("format_id", "")) in PREMIUM_FORMAT_IDS:
            return PREMIUM
    return FREE


def settings_are_premium(settings: SpotdlSettings) -> bool:
    return is_premium("format", settings.format) or is_premium("bitrate", settings.bitrate)


def _youtube_dl():
    try:
        from yt_dlp import YoutubeDL
    except ImportError:
        root = Path(__file__).resolve().parents[2] / "sources" / "yt-dlp-2026.10.26"
        if str(root) not in sys.path:
            sys.path.insert(0, str(root))
        try:
            from yt_dlp import YoutubeDL
        except ImportError:
            return None
    return YoutubeDL


class _Logger:
    def __init__(self) -> None:
        self.notes: list[str] = []

    def debug(self, msg: str) -> None:
        return None

    def info(self, msg: str) -> None:
        return None

    def warning(self, msg: str) -> None:
        self.notes.append(str(msg))

    def error(self, msg: str) -> None:
        self.notes.append(str(msg))


def live_probe(cookie_file: str) -> ProbeResult:
    youtube_dl = _youtube_dl()
    if youtube_dl is None:
        return ProbeResult(None, "")
    logger = _Logger()
    try:
        with youtube_dl(
            {
                "skip_download": True,
                "cookiefile": cookie_file,
                "quiet": True,
                "no_warnings": False,
                "logger": logger,
            }
        ) as ydl:
            info = ydl.extract_info(PROBE_URL, download=False)
    except Exception as exc:
        logger.notes.append(str(exc))
        return ProbeResult(None, "\n".join(logger.notes))
    formats = info.get("formats") if isinstance(info, dict) else None
    if not isinstance(formats, list):
        formats = None
    return ProbeResult(formats, "\n".join(logger.notes))


class CookieGate:
    def __init__(
        self,
        probe: Callable[[str], ProbeResult] | None = None,
        clock: Callable[[], float] | None = None,
    ) -> None:
        self._probe = probe or live_probe
        self._clock = clock or time.monotonic
        self._lock = threading.Lock()
        self._status: str | None = None
        self._at = 0.0
        self._latest_unknown = False

    def check(self, cookie_file: str) -> str:
        with self._lock:
            return self._check(cookie_file)

    def allow(self, settings: SpotdlSettings) -> str | None:
        with self._lock:
            if not settings_are_premium(settings):
                return None
            if not cookie_ready(settings):
                return MISSING_LINE
            if self._status in {EXPIRED, FREE}:
                return _LINES[self._status]
            if self._status == PREMIUM and self._clock() - self._at < FRESH_SECONDS:
                return None
            status = self._check(settings.cookie_file)
            if status == PREMIUM:
                return None
            if status in {EXPIRED, FREE}:
                return _LINES[status]
            return FAILED_LINE

    def line(self, settings: SpotdlSettings) -> str:
        with self._lock:
            if not cookie_ready(settings):
                return MISSING_LINE
            if self._latest_unknown:
                return FAILED_LINE
            if self._status in _LINES:
                return _LINES[self._status]
            return FOUND_LINE

    def _check(self, cookie_file: str) -> str:
        if not cookie_file or not Path(cookie_file).is_file():
            return ""
        try:
            result = self._probe(cookie_file)
        except Exception as exc:
            result = ProbeResult(None, str(exc))
        status = classify(result.text, result.formats)
        if status == UNKNOWN:
            self._latest_unknown = True
            return status
        self._latest_unknown = False
        self._status = status
        self._at = self._clock()
        return status
