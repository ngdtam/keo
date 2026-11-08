"""Dashboard counts. A search query on / goes to /search."""

from __future__ import annotations

from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from keo.web.deps import TEMPLATES, get_services, require_page
from keo.web.routes_settings import _context

router = APIRouter()
PAGE = {"response_class": HTMLResponse, "dependencies": [Depends(require_page)]}


@router.get("/", **PAGE)
def home_page(request: Request, q: str = ""):
    query = q.strip()
    if query:
        return RedirectResponse("/search?" + urlencode({"q": query}), status_code=303)
    services = get_services(request)
    jobs = services.library.list_jobs()
    context = _context(request)
    context.update(
        review_count=len(services.library.list_review()),
        active_count=sum(job.status in {"queued", "running"} for job in jobs),
        failed_count=sum(job.status == "failed" for job in jobs),
    )
    return TEMPLATES.TemplateResponse(request, "home.html", context)
