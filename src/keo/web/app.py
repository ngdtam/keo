"""The FastAPI app: phone pages and the JSON API behind one token."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from keo.catalog import CatalogError
from keo.web.deps import TEMPLATES, LoginRequired, Services
from keo.web.routes_api import router as api_router
from keo.web.routes_home import router as home_router
from keo.web.routes_library import router as library_router
from keo.web.routes_login import router as login_router
from keo.web.routes_queue import router as queue_router
from keo.web.routes_search import router as search_router
from keo.web.routes_browse import router as browse_router
from keo.web.routes_settings import router as settings_router

STATIC_DIR = Path(__file__).parent / "static"
UI_DIST_DIR = Path(__file__).parent / "ui_dist"
SPA_ROUTES = (
    "/",
    "/login",
    "/library",
    "/library/organize/preview",
    "/library/trash",
    "/library/{kind}/{ident}",
    "/search",
    "/charts",
    "/moods",
    "/moods/{params}",
    "/playlists/{playlist_id}",
    "/songs/{video_id}/radio",
    "/review",
    "/review/{video_id}/pick",
    "/activity",
    "/settings",
)


def create_app(
    services: Services,
    lifespan: Any = None,
    serve_ui: bool | None = None,
) -> FastAPI:
    app = FastAPI(title="Kéo", lifespan=lifespan)
    app.state.services = services
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    if serve_ui is None:
        serve_ui = (
            os.environ.get("KEO_SERVE_UI", "1") == "1"
            and not os.environ.get("PYTEST_CURRENT_TEST")
        )
    index_html = UI_DIST_DIR / "index.html"
    assets_dir = UI_DIST_DIR / "assets"
    if serve_ui and index_html.is_file():
        if assets_dir.is_dir():
            app.mount("/assets", StaticFiles(directory=assets_dir), name="ui-assets")

        async def spa_index():
            return FileResponse(index_html)

        for path in SPA_ROUTES:
            app.get(path, include_in_schema=False)(spa_index)

    for router in (
        api_router, login_router, home_router, search_router, browse_router,
        queue_router, library_router, settings_router,
    ):
        app.include_router(router)

    @app.exception_handler(LoginRequired)
    async def login_required(request: Request, exc: LoginRequired):
        return RedirectResponse("/login", status_code=303)

    @app.exception_handler(CatalogError)
    async def catalog_failed(request: Request, exc: CatalogError):
        reason = str(exc)[:300]
        if request.url.path.startswith("/api/"):
            return JSONResponse({"detail": reason}, status_code=502)
        return TEMPLATES.TemplateResponse(
            request, "error.html", {"message": reason}, status_code=502
        )

    return app
