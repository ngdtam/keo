"""Dashboard counts. A search query on / goes to /search."""

from __future__ import annotations

from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from keo.web.deps import TEMPLATES, get_services, require_page
from keo.web.routes_library import _collect_rows, _format_bytes
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
    seen: set[str] = set()
    attention = []
    for job in jobs:
        if job.status not in {"queued", "running", "failed"}:
            continue
        href = (
            f"/library/spotify/{job.spotify_id}" if job.spotify_id
            else f"/library/video/{job.video_id}"
        )
        if href in seen:
            continue
        seen.add(href)
        attention.append({"href": href, "artist": job.artist, "title": job.title})
    all_rows = _collect_rows(services)
    library_size = _format_bytes(sum(r.size for r in all_rows if r.size))
    missing_count = sum(1 for r in all_rows if not r.path and r.label not in {"Queued", "Downloading"})
    context.update(
        review_count=len(services.library.list_review()),
        library_count=len(all_rows),
        library_size=library_size,
        missing_count=missing_count,
        active_count=sum(job.status in {"queued", "running"} for job in jobs),
        failed_count=sum(job.status == "failed" for job in jobs),
        attention=attention,
    )
    return TEMPLATES.TemplateResponse(request, "home.html", context)
