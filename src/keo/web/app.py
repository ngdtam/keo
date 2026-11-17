"""The FastAPI app: SPA hosting and JSON API behind one token."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from keo.catalog import CatalogError
from keo.web.auth import ensure_services_auth_user, is_authorized
from keo.web.deps import LoginRequired, Services
from keo.web.routes_api import router as api_router
from keo.web.routes_library import router as library_router
from keo.web.routes_queue import router as queue_router
from keo.web.routes_search import router as search_router
from keo.web.routes_browse import router as browse_router
from keo.web.routes_settings import router as settings_router

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


OPENAPI_TAGS = [
    {"name": "System", "description": "Health checks and system status."},
    {"name": "Auth", "description": "Session sign-in, credentials, and API key management."},
    {"name": "Library", "description": "Downloaded tracks, organization, and trash."},
    {"name": "Search", "description": "YouTube Music song search and suggestions."},
    {"name": "Browse", "description": "Charts, moods, playlists, and song radio."},
    {"name": "Queue", "description": "Downloads, jobs, review queue, and history."},
    {"name": "Settings", "description": "Configuration and health diagnostics."},
]


def create_app(
    services: Services,
    lifespan: Any = None,
    serve_ui: bool | None = None,
) -> FastAPI:
    ensure_services_auth_user(
        services,
        reset_password=os.environ.get("KEO_RESET_PASSWORD") == "1",
    )

    app = FastAPI(
        title="Kéo",
        description="Self-hosted YouTube Music downloader, library manager, and external REST API.",
        openapi_tags=OPENAPI_TAGS,
        lifespan=lifespan,
    )
    app.state.services = services
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.get("/ping", tags=["System"])
    def ping():
        return {"status": "ok"}

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

        async def spa_index(request: Request):
            if request.url.path != "/login" and not is_authorized(request, services):
                return RedirectResponse("/login", status_code=303)
            return FileResponse(index_html)

        for path in SPA_ROUTES:
            app.get(path, include_in_schema=False)(spa_index)

    for router in (
        api_router, search_router, browse_router,
        queue_router, library_router, settings_router,
    ):
        app.include_router(router)

    @app.exception_handler(LoginRequired)
    async def login_required(request: Request, exc: LoginRequired):
        return RedirectResponse("/login", status_code=303)

    @app.exception_handler(CatalogError)
    async def catalog_failed(request: Request, exc: CatalogError):
        return JSONResponse({"detail": str(exc)[:300]}, status_code=502)

    return app
