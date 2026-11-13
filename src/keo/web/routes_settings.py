"""Settings page: auth-file check and one spotDL button at a time."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from typing import Annotated

from keo.config import (
    ALBUM_TYPES,
    AUDIO_PROVIDERS,
    BITRATES,
    BUTTONS,
    FORMATS,
    LYRICS_PROVIDERS,
    RECYCLE_DAYS,
    RESTRICT,
    ConfigError,
    SpotdlSettings,
    auth_gaps,
    cookie_ready,
    is_premium,
    save_choice,
    save_cookie_file,
    save_integrations,
    save_recycle_days,
)
from keo.history import run_pull
from keo.recycle import cleanup_recycle
from keo.scan import scan
from keo.system import (
    backup_data,
    check_package_updates,
    last_backup_date,
    run_health_checks,
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
    groups.append(
        {
            "title": "Trash retention (days)",
            "choices": [
                {
                    "name": "recycle_days",
                    "value": value,
                    "label": "Never" if value == "0" else value,
                    "active": str(services.recycle_days) == value,
                    "disabled": False,
                    "premium": False,
                }
                for value in RECYCLE_DAYS
            ],
        }
    )
    backoff = services.worker.backoff_status()
    backoff_line = ""
    if backoff["active"]:
        mins = max(1, (backoff["seconds_remaining"] + 59) // 60)
        backoff_line = f"Worker paused for ~{mins}m ({backoff['reason']})"
    return {
        "auth_error": auth_error,
        "gaps": gaps,
        "cookie_line": services.cookies.line(settings),
        "backoff_line": backoff_line,
        "notify_url": services.notify_url,
        "media_refresh_url": services.media_refresh_url,
        "health_checks": run_health_checks(services),
        "last_backup": last_backup_date(services.data_dir) if services.data_dir else None,
        "tasks": services.library.list_tasks(),
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
    valid_keys = set(BUTTONS) | {"recycle_days"}
    posted = [
        (key, value) for key, value in form.items() if key in valid_keys and isinstance(value, str)
    ]
    if len(posted) != 1:
        return TEMPLATES.TemplateResponse(
            request, "settings.html", _context(request, "Choose one setting."), status_code=400
        )
    name, value = posted[0]
    try:
        if name == "recycle_days":
            services.recycle_days = save_recycle_days(services.config_path, value)
        else:
            updated = save_choice(services.config_path, services.spotdl.settings, name, value)
            services.spotdl.use(updated)
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
    services.cookies.check(str(path))
    return RedirectResponse("/settings", status_code=303)


@router.post("/settings/cookie/check", **PAGE)
def check_cookie(request: Request):
    services = get_services(request)
    settings = services.spotdl.settings
    if cookie_ready(settings):
        services.cookies.check(settings.cookie_file)
    return RedirectResponse("/settings", status_code=303)


@router.post("/settings/integrations", **PAGE)
def save_integrations_route(
    request: Request,
    notify_url: Annotated[str, Form()] = "",
    media_refresh_url: Annotated[str, Form()] = "",
):
    services = get_services(request)
    if services.config_path is None:
        return TEMPLATES.TemplateResponse(
            request, "settings.html", _context(request, "Kéo has no config file."), status_code=400
        )
    try:
        notify, refresh = save_integrations(services.config_path, notify_url, media_refresh_url)
    except ConfigError as exc:
        return TEMPLATES.TemplateResponse(
            request, "settings.html", _context(request, str(exc)), status_code=400
        )
    services.notify_url = notify
    services.media_refresh_url = refresh
    return RedirectResponse("/settings", status_code=303)


@router.post("/settings/backup", **PAGE)
def run_backup_route(request: Request):
    services = get_services(request)
    if services.data_dir is not None:
        backup_data(services.data_dir, services.config_path)
    return RedirectResponse("/settings", status_code=303)


@router.post("/settings/check-updates", **PAGE)
def check_updates_route(request: Request):
    services = get_services(request)
    services.package_updates = check_package_updates()
    return RedirectResponse("/settings", status_code=303)


@router.post("/settings/tasks/{name}/run", **PAGE)
def run_task_route(request: Request, name: str):
    services = get_services(request)
    if name == "history_pull":
        run_pull(
            catalog=services.catalog,
            library=services.library,
            worker=services.worker,
            spotdl=services.spotdl,
            cookies=services.cookies,
            progress=services.history_progress,
        )
        services.library.record_task_run("history_pull", 24)
    elif name == "library_scan":
        if services.music_dir is not None:
            scan(services.music_dir, services.library)
        services.library.record_task_run("library_scan", 24)
    elif name == "recycle_sweep":
        if services.music_dir is not None:
            cleanup_recycle(services.music_dir, services.recycle_days)
        services.library.record_task_run("recycle_sweep", 24)
    elif name == "housekeeping":
        services.library.housekeep()
        services.library.record_task_run("housekeeping", 24)
    elif name == "backup":
        if services.data_dir is not None:
            backup_data(services.data_dir, services.config_path)
        services.library.record_task_run("backup", 168)
    return RedirectResponse("/settings", status_code=303)
