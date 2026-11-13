"""Load Kéo settings from the environment and keo.toml.

This module reads files and nothing else. Startup calls load_settings once and
exits on ConfigError, so every message names the setting to fix.
"""

from __future__ import annotations

import json
import re
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass, fields, replace
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

FORMATS = ("mp3", "m4a", "opus", "ogg", "flac", "wav")
BITRATES = (
    "auto", "disable", "8k", "16k", "24k", "32k", "40k", "48k", "64k", "80k", "96k",
    "112k", "128k", "160k", "192k", "224k", "256k", "320k",
    *(str(level) for level in range(10)),
)
AUDIO_PROVIDERS = ("youtube-music", "youtube", "soundcloud", "bandcamp", "piped")
LYRICS_PROVIDERS = ("genius", "musixmatch", "azlyrics", "synced")
ALBUM_TYPES = ("", "single", "album", "compilation")
RESTRICT = ("", "strict", "ascii", "none")

DEFAULT_TIMEZONE = "Asia/Ho_Chi_Minh"
DEFAULT_HOUR = 22
DEFAULT_RECYCLE_DAYS = 7
RECYCLE_DAYS = ("0", "3", "7", "14", "30", "90")


class ConfigError(Exception):
    """Kéo cannot start with these settings."""


@dataclass(frozen=True)
class SpotdlSettings:
    format: str = "mp3"
    bitrate: str = "128k"
    audio: tuple[str, ...] = ("youtube-music",)
    sponsor_block: bool = False
    ffmpeg_args: str = ""
    cookie_file: str = ""
    lyrics: tuple[str, ...] = ("genius", "azlyrics", "musixmatch")
    genius_access_token: str = ""
    generate_lrc: bool = False
    id3_separator: str = "/"
    skip_album_art: bool = False
    search_query: str = ""
    album_type: str = ""
    only_verified_results: bool = False
    dont_filter_results: bool = False
    skip_explicit: bool = False
    output_template: str = "{artists} - {title}.{output-ext}"
    restrict: str = ""
    max_filename_length: int = 0
    proxy: str = ""
    yt_dlp_args: str = ""
    preload: bool = False
    max_retries: int = 3
    client_id: str = ""
    client_secret: str = ""


@dataclass(frozen=True)
class Settings:
    token: str
    music_dir: Path
    data_dir: Path
    ytm_auth: Path
    ytm_client_id: str
    ytm_client_secret: str
    spotdl: SpotdlSettings
    history_timezone: str
    history_hour: int
    config_path: Path
    recycle_days: int = DEFAULT_RECYCLE_DAYS
    notify_url: str = ""
    media_refresh_url: str = ""


_DEFAULT_SPOTDL = SpotdlSettings()
_SPOTDL_NAMES = tuple(field.name for field in fields(SpotdlSettings))
_ALLOWED = {
    *_SPOTDL_NAMES,
    "history_timezone",
    "history_hour",
    "recycle_days",
    "notify_url",
    "media_refresh_url",
}


def load_settings(env: Mapping[str, str]) -> Settings:
    token = _require(env, "KEO_TOKEN")
    music_dir = Path(_require(env, "KEO_MUSIC_DIR"))
    if not music_dir.is_dir():
        raise ConfigError(f"KEO_MUSIC_DIR is not a folder: {music_dir}")
    ytm_auth = Path(_require(env, "KEO_YTM_AUTH"))
    if not ytm_auth.is_file():
        raise ConfigError(f"KEO_YTM_AUTH file not found: {ytm_auth}")
    client_id = env.get("KEO_YTM_CLIENT_ID", "").strip()
    client_secret = env.get("KEO_YTM_CLIENT_SECRET", "").strip()
    if _is_oauth(ytm_auth) and not (client_id and client_secret):
        raise ConfigError(
            "KEO_YTM_AUTH is an OAuth file, so KEO_YTM_CLIENT_ID and "
            "KEO_YTM_CLIENT_SECRET are required"
        )
    config_path = Path(env.get("KEO_CONFIG", "/config/keo.toml"))
    spotdl, hour, recycle_days, notify_url, media_refresh_url = _load_file(config_path)
    return Settings(
        token=token,
        music_dir=music_dir,
        data_dir=Path(env.get("KEO_DATA_DIR", "/data")),
        ytm_auth=ytm_auth,
        ytm_client_id=client_id,
        ytm_client_secret=client_secret,
        spotdl=spotdl,
        history_timezone=_zone(env.get("TZ", "")),
        history_hour=hour,
        config_path=config_path,
        recycle_days=recycle_days,
        notify_url=notify_url,
        media_refresh_url=media_refresh_url,
    )


def _require(env: Mapping[str, str], name: str) -> str:
    value = env.get(name, "").strip()
    if not value:
        raise ConfigError(f"{name} is not set")
    return value


def _is_oauth(path: Path) -> bool:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ConfigError(f"KEO_YTM_AUTH is not valid JSON: {exc}") from exc
    return isinstance(data, dict) and "refresh_token" in data


def _zone(value: str) -> str:
    name = value.strip() or DEFAULT_TIMEZONE
    try:
        ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ConfigError(f"TZ is not a known time zone: {name}") from exc
    return name


def _load_file(path: Path) -> tuple[SpotdlSettings, int, int]:
    data: dict[str, Any] = {}
    if path.is_file():
        try:
            data = tomllib.loads(path.read_text(encoding="utf-8"))
        except tomllib.TOMLDecodeError as exc:
            raise ConfigError(f"keo.toml is invalid: {exc}") from exc
    unknown = sorted(set(data) - _ALLOWED)
    if unknown:
        raise ConfigError(f"keo.toml has an unknown setting: {unknown[0]}")

    spotdl = replace(
        _DEFAULT_SPOTDL,
        **{
            name: _coerce(name, data[name], getattr(_DEFAULT_SPOTDL, name))
            for name in _SPOTDL_NAMES
            if name in data
        },
    )
    _check_spotdl(spotdl)

    hour = _coerce("history_hour", data.get("history_hour", DEFAULT_HOUR), DEFAULT_HOUR)
    if not 0 <= hour <= 23:
        raise ConfigError("keo.toml: history_hour must be between 0 and 23")
    recycle_days = _coerce("recycle_days", data.get("recycle_days", DEFAULT_RECYCLE_DAYS), DEFAULT_RECYCLE_DAYS)
    if recycle_days < 0:
        raise ConfigError("keo.toml: recycle_days must be 0 or greater")
    notify_url = _coerce("notify_url", data.get("notify_url", ""), "").strip()
    media_refresh_url = _coerce("media_refresh_url", data.get("media_refresh_url", ""), "").strip()
    return spotdl, hour, recycle_days, notify_url, media_refresh_url


def _coerce(name: str, value: Any, default: Any) -> Any:
    if isinstance(default, bool):
        if not isinstance(value, bool):
            raise ConfigError(f"keo.toml: {name} must be true or false")
        return value
    if isinstance(default, int):
        if isinstance(value, bool) or not isinstance(value, int):
            raise ConfigError(f"keo.toml: {name} must be a whole number")
        return value
    if isinstance(default, tuple):
        if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
            raise ConfigError(f"keo.toml: {name} must be a list of strings")
        return tuple(value)
    if not isinstance(value, str):
        raise ConfigError(f"keo.toml: {name} must be a string")
    return value


def _check_spotdl(settings: SpotdlSettings) -> None:
    _one_of("format", settings.format, FORMATS)
    _one_of("bitrate", settings.bitrate, BITRATES)
    _one_of("album_type", settings.album_type, ALBUM_TYPES)
    _one_of("restrict", settings.restrict, RESTRICT)
    _some_of("audio", settings.audio, AUDIO_PROVIDERS)
    _some_of("lyrics", settings.lyrics, LYRICS_PROVIDERS)
    if settings.generate_lrc and "synced" not in settings.lyrics:
        raise ConfigError('keo.toml: generate_lrc needs "synced" in lyrics')
    if settings.cookie_file and not Path(settings.cookie_file).is_file():
        raise ConfigError(f"keo.toml: cookie_file not found: {settings.cookie_file}")
    if settings.max_retries < 0:
        raise ConfigError("keo.toml: max_retries must be 0 or more")
    if settings.max_filename_length < 0:
        raise ConfigError("keo.toml: max_filename_length must be 0 or more")


def _one_of(name: str, value: str, choices: tuple[str, ...]) -> None:
    if value not in choices:
        shown = ", ".join(repr(choice) for choice in choices)
        raise ConfigError(f"keo.toml: {name} must be one of {shown}")


def _some_of(name: str, values: tuple[str, ...], choices: tuple[str, ...]) -> None:
    if not values or any(value not in choices for value in values):
        shown = ", ".join(repr(choice) for choice in choices)
        raise ConfigError(f"keo.toml: {name} must be a non-empty list from {shown}")


_BROWSER_FIELDS = ("cookie", "x-goog-authuser", "authorization")
_OAUTH_FIELDS = ("access_token", "refresh_token", "token_type")
_ENUMS = {
    "format": FORMATS,
    "bitrate": BITRATES,
    "album_type": ALBUM_TYPES,
    "restrict": RESTRICT,
}
_TOGGLES = {"audio": AUDIO_PROVIDERS, "lyrics": LYRICS_PROVIDERS}
_FLAGS = (
    "sponsor_block",
    "generate_lrc",
    "skip_album_art",
    "only_verified_results",
    "dont_filter_results",
    "skip_explicit",
    "preload",
)
_PREMIUM = {"format": frozenset({"m4a", "opus"}), "bitrate": frozenset({"disable"})}
BUTTONS = tuple(_ENUMS) + tuple(_TOGGLES) + _FLAGS


def auth_gaps(path: Path, client_id: str = "", client_secret: str = "") -> tuple[str, ...]:
    """Names of the auth fields that are missing. No network call."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ConfigError(f"KEO_YTM_AUTH could not be read: {exc}") from exc
    if not isinstance(data, dict):
        raise ConfigError("KEO_YTM_AUTH must be a JSON object")
    if "refresh_token" in data:
        gaps = _blank(data, _OAUTH_FIELDS)
        if not client_id.strip():
            gaps.append("KEO_YTM_CLIENT_ID")
        if not client_secret.strip():
            gaps.append("KEO_YTM_CLIENT_SECRET")
        return tuple(gaps)
    headers = {str(key).lower(): value for key, value in data.items()}
    return tuple(_blank(headers, _BROWSER_FIELDS))


def cookie_ready(settings: SpotdlSettings) -> bool:
    return bool(settings.cookie_file) and Path(settings.cookie_file).is_file()


def is_premium(name: str, value: str) -> bool:
    return name in _PREMIUM and value in _PREMIUM[name]


def apply_choice(settings: SpotdlSettings, name: str, value: str) -> SpotdlSettings:
    """Return settings with one button changed. Raise ConfigError and keep the old ones."""
    if name not in BUTTONS:
        raise ConfigError(f"keo.toml: {name} is not a Settings button")
    if is_premium(name, value) and not cookie_ready(settings):
        raise ConfigError(f"keo.toml: {name} {value} needs a cookie file that exists")
    if name in _ENUMS:
        _one_of(name, value, _ENUMS[name])
        return replace(settings, **{name: value})
    if name in _TOGGLES:
        return replace(settings, **{name: _toggle(settings, name, value)})
    if value not in ("true", "false"):
        raise ConfigError(f"keo.toml: {name} must be true or false")
    enabled = value == "true"
    if name == "generate_lrc" and enabled and "synced" not in settings.lyrics:
        raise ConfigError('keo.toml: generate_lrc needs "synced" in lyrics')
    return replace(settings, **{name: enabled})


def save_cookie_file(path: Path, settings: SpotdlSettings, cookie_path: Path) -> SpotdlSettings:
    updated = replace(settings, cookie_file=str(cookie_path))
    text = path.read_text(encoding="utf-8") if path.is_file() else ""
    path.write_text(
        _replace_assignment(text, "cookie_file", _render("cookie_file", updated.cookie_file)),
        encoding="utf-8",
    )
    return updated


def save_choice(path: Path, settings: SpotdlSettings, name: str, value: str) -> SpotdlSettings:
    updated = apply_choice(settings, name, value)
    text = path.read_text(encoding="utf-8") if path.is_file() else ""
    path.write_text(_replace_assignment(text, name, _render(name, getattr(updated, name))), encoding="utf-8")
    return updated


def save_recycle_days(path: Path, value: str) -> int:
    if value not in RECYCLE_DAYS:
        raise ConfigError(f"keo.toml: recycle_days must be one of {', '.join(RECYCLE_DAYS)}")
    days = int(value)
    text = path.read_text(encoding="utf-8") if path.is_file() else ""
    path.write_text(_replace_assignment(text, "recycle_days", _render("recycle_days", days)), encoding="utf-8")
    return days


def save_integrations(path: Path, notify_url: str, media_refresh_url: str) -> tuple[str, str]:
    notify = notify_url.strip()
    refresh = media_refresh_url.strip()
    for name, val in (("notify_url", notify), ("media_refresh_url", refresh)):
        if val and not val.startswith(("http://", "https://")):
            raise ConfigError(f"keo.toml: {name} must start with http:// or https://")
    text = path.read_text(encoding="utf-8") if path.is_file() else ""
    text = _replace_assignment(text, "notify_url", _render("notify_url", notify))
    text = _replace_assignment(text, "media_refresh_url", _render("media_refresh_url", refresh))
    path.write_text(text, encoding="utf-8")
    return notify, refresh


def _blank(data: Mapping[str, Any], names: tuple[str, ...]) -> list[str]:
    return [
        name for name in names if not (isinstance(data.get(name), str) and data[name].strip())
    ]


def _toggle(settings: SpotdlSettings, name: str, value: str) -> tuple[str, ...]:
    choices = _TOGGLES[name]
    if value not in choices:
        shown = ", ".join(repr(choice) for choice in choices)
        raise ConfigError(f"keo.toml: {name} must be one of {shown}")
    current = getattr(settings, name)
    if value in current:
        kept = tuple(item for item in current if item != value)
        if not kept:
            raise ConfigError(f"keo.toml: {name} must keep one source")
        if name == "lyrics" and settings.generate_lrc and "synced" not in kept:
            raise ConfigError('keo.toml: generate_lrc needs "synced" in lyrics')
        return kept
    return current + (value,)


def _render(name: str, value: Any) -> str:
    if isinstance(value, bool):
        return f"{name} = {'true' if value else 'false'}"
    if isinstance(value, tuple):
        items = ", ".join(json.dumps(item) for item in value)
        return f"{name} = [{items}]"
    return f"{name} = {json.dumps(value)}"


def _replace_assignment(text: str, name: str, rendered: str) -> str:
    # shortcut: a trailing comment on the edited line is dropped, upgrade when a line has one.
    pattern = re.compile(rf"^[ \t]*{re.escape(name)}[ \t]*=")
    lines = text.splitlines(keepends=True)
    for index, line in enumerate(lines):
        if pattern.match(line):
            newline = "\r\n" if line.endswith("\r\n") else "\n"
            lines[index] = rendered + newline
            return "".join(lines)
    if text and not text.endswith(("\n", "\r\n")):
        text += "\n"
    return text + rendered + "\n"
