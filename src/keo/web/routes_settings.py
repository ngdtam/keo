"""Settings page: auth-file check and one spotDL button at a time."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse

from keo.config import (
    ALBUM_TYPES,
    AUDIO_PROVIDERS,
    BITRATES,
    BUTTONS,
    FORMATS,
    LYRICS_PROVIDERS,
    RESTRICT,
    ConfigError,
    SpotdlSettings,
    auth_gaps,
    cookie_ready,
    is_premium,
    save_choice,
    save_cookie_file,
)
from keo.web.deps import TEMPLATES, get_services, require_page

router = APIRouter()
PAGE = {"response_class": HTMLResponse, "dependencies": [Depends(require_page)]}
COOKIE_LIMIT = 1048576

_CHOICES = (
    ("Format", "format", FORMATS),
    ("Bitrate", "bitrate", BITRATES),
    ("Audio", "audio", AUDIO_PROVIDERS),
    ("Lyrics", "lyrics", LYRICS_PROVIDERS),
    ("Album type", "album_type", ALBUM_TYPES),
    ("Filename restrict", "restrict", RESTRICT),
)
_FLAG_TITLES = (
    ("Sponsor block", "sponsor_block"),
    ("Synced LRC file", "generate_lrc"),
    ("Skip album art", "skip_album_art"),
    ("Only verified results", "only_verified_results"),
    ("Don't filter results", "dont_filter_results"),
    ("Skip explicit", "skip_explicit"),
    ("Preload", "preload"),
)
_EMPTY_LABELS = {"album_type": "any", "restrict": "default"}


def _label(name: str, value: str) -> str:
    if value == "":
        return _EMPTY_LABELS.get(name, value)
    if value == "true":
        return "On"
    if value == "false":
        return "Off"
    return value


def _group(settings: SpotdlSettings, ready: bool, title: str, name: str, choices: tuple[str, ...]):
    current = getattr(settings, name)
    buttons = []
    for value in choices:
        selected = value in current if isinstance(current, tuple) else (
            (current and value == "true") or (not current and value == "false")
            if isinstance(current, bool)
            else current == value
        )
        disabled = (is_premium(name, value) and not ready) or (
            isinstance(current, tuple) and selected and len(current) == 1
        )
        if name == "generate_lrc" and value == "true" and "synced" not in settings.lyrics:
            disabled = True
        buttons.append(
            {
                "name": name,
                "value": value,
                "label": _label(name, value),
                "selected": selected,
                "disabled": disabled,
                "premium": is_premium(name, value),
            }
        )
    return {"title": title, "choices": buttons}


def _context(request: Request, error: str | None = None) -> dict:
    services = get_services(request)
    settings = services.spotdl.settings
    ready = cookie_ready(settings)
    auth_error = ""
    gaps: tuple[str, ...] = ()
    if services.ytm_auth is None:
        auth_error = "KEO_YTM_AUTH is not set"
    else:
        try:
            gaps = auth_gaps(services.ytm_auth, services.ytm_client_id, services.ytm_client_secret)
        except ConfigError as exc:
            auth_error = str(exc)
    warnings = []
    if not ready and is_premium("format", settings.format):
        warnings.append(f"format is {settings.format}, which needs a cookie file")
    if not ready and is_premium("bitrate", settings.bitrate):
        warnings.append(f"bitrate is {settings.bitrate}, which needs a cookie file")
    groups = [_group(settings, ready, *choice) for choice in _CHOICES]
    groups += [_group(settings, ready, title, name, ("true", "false")) for title, name in _FLAG_TITLES]
    return {
        "auth_error": auth_error,
        "gaps": gaps,
        "cookie_line": "Cookie file found" if ready else "Cookie file missing",
        "warnings": warnings,
        "groups": groups,
        "error": error,
    }


@router.get("/settings", **PAGE)
def settings_page(request: Request):
    return TEMPLATES.TemplateResponse(request, "settings.html", _context(request))


@router.post("/settings", **PAGE)
async def save_settings(request: Request):
    services = get_services(request)
    if services.config_path is None:
        return TEMPLATES.TemplateResponse(
            request, "settings.html", _context(request, "Kéo has no config file."), status_code=400
        )
    form = await request.form()
    posted = [
        (key, value) for key, value in form.items() if key in BUTTONS and isinstance(value, str)
    ]
    if len(posted) != 1:
        return TEMPLATES.TemplateResponse(
            request, "settings.html", _context(request, "Choose one setting."), status_code=400
        )
    name, value = posted[0]
    try:
        updated = save_choice(services.config_path, services.spotdl.settings, name, value)
    except ConfigError as exc:
        return TEMPLATES.TemplateResponse(
            request, "settings.html", _context(request, str(exc)), status_code=400
        )
    except OSError as exc:
        return TEMPLATES.TemplateResponse(
            request,
            "settings.html",
            _context(request, f"keo.toml could not be saved: {exc}"),
            status_code=400,
        )
    services.spotdl.use(updated)
    return RedirectResponse("/settings", status_code=303)


@router.post("/settings/cookie", **PAGE)
async def upload_cookie(request: Request, cookie: UploadFile):
    services = get_services(request)
    if services.config_path is None:
        return TEMPLATES.TemplateResponse(
            request, "settings.html", _context(request, "Kéo has no config file."), status_code=400
        )
    if services.data_dir is None:
        return TEMPLATES.TemplateResponse(
            request, "settings.html", _context(request, "Kéo has no data directory."), status_code=400
        )
    raw = await cookie.read()
    if len(raw) > COOKIE_LIMIT:
        return TEMPLATES.TemplateResponse(
            request, "settings.html", _context(request, "The file is too large."), status_code=400
        )
    if not raw.strip():
        return TEMPLATES.TemplateResponse(
            request, "settings.html", _context(request, "The file is empty."), status_code=400
        )
    path = services.data_dir / "cookies.txt"
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
        updated = save_cookie_file(services.config_path, services.spotdl.settings, path)
    except OSError as exc:
        return TEMPLATES.TemplateResponse(
            request,
            "settings.html",
            _context(request, f"The cookie file could not be saved: {exc}"),
            status_code=400,
        )
    services.spotdl.use(updated)
    return RedirectResponse("/settings", status_code=303)
