"""Load Kéo settings from the environment and keo.toml.

This module reads files and nothing else. Startup calls load_settings once and
exits on ConfigError, so every message names the setting to fix.
"""

from __future__ import annotations

import json
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
DEFAULT_HOUR = 23


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


_DEFAULT_SPOTDL = SpotdlSettings()
_SPOTDL_NAMES = tuple(field.name for field in fields(SpotdlSettings))
_ALLOWED = {*_SPOTDL_NAMES, "history_timezone", "history_hour"}


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
    spotdl, timezone, hour = _load_file(Path(env.get("KEO_CONFIG", "/config/keo.toml")))
    return Settings(
        token=token,
        music_dir=music_dir,
        data_dir=Path(env.get("KEO_DATA_DIR", "/data")),
        ytm_auth=ytm_auth,
        ytm_client_id=client_id,
        ytm_client_secret=client_secret,
        spotdl=spotdl,
        history_timezone=timezone,
        history_hour=hour,
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


def _load_file(path: Path) -> tuple[SpotdlSettings, str, int]:
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

    timezone = _coerce(
        "history_timezone", data.get("history_timezone", DEFAULT_TIMEZONE), DEFAULT_TIMEZONE
    )
    try:
        ZoneInfo(timezone)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ConfigError(
            f"keo.toml: history_timezone is not a known time zone: {timezone}"
        ) from exc
    hour = _coerce("history_hour", data.get("history_hour", DEFAULT_HOUR), DEFAULT_HOUR)
    if not 0 <= hour <= 23:
        raise ConfigError("keo.toml: history_hour must be between 0 and 23")
    return spotdl, timezone, hour


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
