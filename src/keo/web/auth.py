"""The shared-token check for the JSON API and the browser session cookie."""

from __future__ import annotations

import hashlib
import hmac

from fastapi import Request

COOKIE_NAME = "keo_session"
COOKIE_MAX_AGE = 30 * 24 * 60 * 60


def session_value(token: str) -> str:
    return hmac.new(token.encode(), b"keo-session", hashlib.sha256).hexdigest()


def is_authorized(request: Request, token: str) -> bool:
    header = request.headers.get("authorization", "")
    if header.startswith("Bearer ") and hmac.compare_digest(
        header.removeprefix("Bearer ").encode(), token.encode()
    ):
        return True
    cookie = request.cookies.get(COOKIE_NAME, "")
    return bool(cookie) and hmac.compare_digest(cookie.encode(), session_value(token).encode())
