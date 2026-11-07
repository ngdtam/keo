"""Pieces every route uses: the services, the token checks, and the templates."""

from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from fastapi import HTTPException, Request
from fastapi.templating import Jinja2Templates

from keo.catalog import Catalog
from keo.jobs import Worker
from keo.library import Library
from keo.records import SongRow
from keo.spotdl_cmd import Spotdl
from keo.web.auth import is_authorized

TEMPLATES = Jinja2Templates(directory=Path(__file__).parent / "templates")


def _start_thread(fn: Callable[[], object]) -> None:
    threading.Thread(target=fn, daemon=True).start()


@dataclass
class Services:
    token: str
    catalog: Catalog
    library: Library
    worker: Worker
    spotdl: Spotdl
    run_later: Callable[[Callable[[], object]], None] = _start_thread


class LoginRequired(Exception):
    """A page was opened without a valid session."""


def get_services(request: Request) -> Services:
    return request.app.state.services


def require_api(request: Request) -> None:
    if not is_authorized(request, get_services(request).token):
        raise HTTPException(status_code=401, detail="Missing or wrong token")


def require_page(request: Request) -> None:
    if not is_authorized(request, get_services(request).token):
        raise LoginRequired()


def song_json(song: SongRow) -> dict[str, Any]:
    data = asdict(song)
    data["artists"] = list(song.artists)
    return data
