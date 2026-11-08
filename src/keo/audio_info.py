"""Codec and bitrate for one audio file, read with libmediainfo."""

from __future__ import annotations

import threading
from dataclasses import dataclass
from pathlib import Path

_cache: dict[tuple[str, int, int], "AudioView | None"] = {}
_lock = threading.Lock()


@dataclass(frozen=True)
class AudioView:
    card: str | None
    rows: tuple[tuple[str, str], ...]


def reset_cache() -> None:
    with _lock:
        _cache.clear()


def _text(track, name: str) -> str | None:
    value = getattr(track, name, None)
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _number(track, name: str) -> float | None:
    value = getattr(track, name, None)
    if value is None or value == "":
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number <= 0:
        return None
    return number


def _whole(track, name: str) -> int | None:
    number = _number(track, name)
    if number is None or number != int(number):
        return None
    return int(number)


def view_track(track) -> AudioView | None:
    raw = _text(track, "format")
    format_name = "MP3" if raw == "MPEG Audio" else raw
    profile = _text(track, "format_profile")
    bit_rate = _number(track, "bit_rate")
    if bit_rate is None:
        bit_rate = _number(track, "overall_bit_rate")
    kbps = int(bit_rate / 1000 + 0.5) if bit_rate is not None else 0
    if kbps <= 0:
        kbps_text = None
    else:
        mode = _text(track, "bit_rate_mode")
        kbps_text = f"{kbps} kbps" + (f" {mode}" if mode else "")

    hz = _number(track, "sampling_rate")
    if hz is None:
        sample = None
    elif hz % 1000 == 0:
        sample = f"{int(hz // 1000)} kHz"
    else:
        sample = f"{int(hz / 100 + 0.5) / 10} kHz"

    count = _whole(track, "channel_s")
    layout = _text(track, "channel_layout")
    channels = None if count is None else (f"{count} · {layout}" if layout else str(count))

    depth = _whole(track, "bit_depth")
    ms = _number(track, "duration")
    if ms is None:
        duration = None
    else:
        seconds = int(ms / 1000 + 0.5)
        if seconds >= 3600:
            hours, rest = divmod(seconds, 3600)
            minutes, secs = divmod(rest, 60)
            duration = f"{hours}:{minutes:02d}:{secs:02d}"
        else:
            minutes, secs = divmod(seconds, 60)
            duration = f"{minutes}:{secs:02d}"

    rows: list[tuple[str, str]] = []
    if format_name and profile:
        rows.append(("Format", f"{format_name} {profile}"))
    elif format_name:
        rows.append(("Format", format_name))
    if kbps_text:
        rows.append(("Bitrate", kbps_text))
    if sample:
        rows.append(("Sample rate", sample))
    if channels:
        rows.append(("Channels", channels))
    if depth is not None:
        rows.append(("Bit depth", f"{depth} bit"))
    if duration:
        rows.append(("Duration", duration))

    if format_name and kbps_text:
        card = f"{format_name} · {kbps} kbps"
    else:
        card = format_name
    if card is None and not rows:
        return None
    return AudioView(card, tuple(rows))


def _library_missing(exc: OSError) -> bool:
    text = str(exc).lower()
    return "library" in text or "libmediainfo" in text


class _Overall:
    """Audio track plus the container bitrate. Opus in Ogg leaves BitRate empty."""

    def __init__(self, track, overall) -> None:
        self._track = track
        self.overall_bit_rate = overall

    def __getattr__(self, name):
        return getattr(self._track, name)


def _parse(path: Path) -> AudioView | None:
    from pymediainfo import MediaInfo

    media = MediaInfo.parse(path)
    general = None
    audio = None
    for track in media.tracks:
        if track.track_type == "General" and general is None:
            general = track
        elif track.track_type == "Audio" and audio is None:
            audio = track
    if audio is None:
        return None
    if _number(audio, "bit_rate") is None and general is not None:
        overall = getattr(general, "overall_bit_rate", None)
        if overall not in (None, ""):
            audio = _Overall(audio, overall)
    return view_track(audio)


def load(path: Path) -> AudioView | None:
    try:
        stat = path.stat()
        key = (str(path.resolve()), stat.st_size, stat.st_mtime_ns)
    except OSError:
        return None
    with _lock:
        if key in _cache:
            return _cache[key]
    try:
        view = _parse(path)
    except ImportError:
        return None
    except OSError as exc:
        if _library_missing(exc):
            return None
        view = None
    except Exception:
        view = None
    with _lock:
        _cache[key] = view
    return view
