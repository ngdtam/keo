"""The FastAPI app: phone pages and the JSON API behind one token."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from keo.catalog import CatalogError
from keo.web.deps import TEMPLATES, LoginRequired, Services
from keo.web.routes_home import router as home_router
from keo.web.routes_login import router as login_router
from keo.web.routes_queue import router as queue_router
from keo.web.routes_search import router as search_router
from keo.web.routes_browse import router as browse_router
from keo.web.routes_settings import router as settings_router

STATIC_DIR = Path(__file__).parent / "static"


def create_app(services: Services, lifespan: Any = None) -> FastAPI:
    app = FastAPI(title="Kéo", lifespan=lifespan)
    app.state.services = services
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
    for router in (login_router, home_router, search_router, browse_router, queue_router, settings_router):
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
