"""Sign in with the shared token."""

from __future__ import annotations

import hmac
from typing import Annotated

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from keo.web.auth import COOKIE_MAX_AGE, COOKIE_NAME, session_value
from keo.web.deps import TEMPLATES, get_services

router = APIRouter()


@router.get("/login", response_class=HTMLResponse)
def login_page(request: Request):
    return TEMPLATES.TemplateResponse(request, "login.html", {"error": None})


@router.post("/login")
def login(request: Request, token: Annotated[str, Form()] = ""):
    expected = get_services(request).token
    if not hmac.compare_digest(token.encode(), expected.encode()):
        return TEMPLATES.TemplateResponse(
            request, "login.html", {"error": "Wrong token."}, status_code=401
        )
    response = RedirectResponse("/", status_code=303)
    response.set_cookie(
        COOKIE_NAME,
        session_value(expected),
        max_age=COOKIE_MAX_AGE,
        httponly=True,
        samesite="lax",
    )
    return response
