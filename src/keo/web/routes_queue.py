"""Start downloads."""

from __future__ import annotations

from dataclasses import asdict

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel

from keo.library import Job
from keo.web.deps import get_services, require_api

router = APIRouter()


class DownloadRequest(BaseModel):
    video_id: str
    title: str
    artists: list[str]


def queued(job: Job | None) -> dict:
    return {"owned": job is None, "job": asdict(job) if job else None}


@router.post("/api/downloads", dependencies=[Depends(require_api)])
def api_download(request: Request, body: DownloadRequest):
    artist = body.artists[0] if body.artists else ""
    return queued(get_services(request).worker.submit(body.video_id, artist, body.title))
