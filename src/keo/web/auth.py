"""Authentication helpers for session cookies and 3-way API key verification."""

from __future__ import annotations

import hashlib
import hmac
import os
from typing import Any

from fastapi import Request
from fastapi.security import APIKeyHeader, APIKeyQuery, HTTPBearer

from keo.library import AuthUser

COOKIE_NAME = "keo_session"
COOKIE_MAX_AGE = 30 * 24 * 60 * 60

api_key_header = APIKeyHeader(
    name="X-Api-Key",
    auto_error=False,
    scheme_name="X-Api-Key",
    description="API key passed in X-Api-Key header",
)
api_key_query = APIKeyQuery(
    name="apikey",
    auto_error=False,
    scheme_name="apikey",
    description="API key passed as ?apikey= query parameter",
)
bearer_auth = HTTPBearer(
    auto_error=False,
    scheme_name="BearerAuth",
    description="API key passed as Authorization: Bearer <key>",
)


def session_value(token: str, user: AuthUser | None = None) -> str:
    if user is None:
        key = token.encode()
    else:
        key = f"{user.username}:{user.password_hash}:{token}".encode()
    return hmac.new(key, b"keo-session", hashlib.sha256).hexdigest()


def extract_api_key(request: Request, provided_key: str | None = None) -> str:
    for candidate in (
        provided_key,
        request.query_params.get("apikey"),
        request.headers.get("x-api-key"),
    ):
        if candidate and candidate.strip():
            return candidate.strip()
    auth_header = request.headers.get("authorization", "")
    scheme, _, credentials = auth_header.partition(" ")
    if scheme.lower() == "bearer" and credentials.strip():
        return credentials.strip()
    return ""


def _app_services(request: Request) -> Any:
    try:
        app = request.app
    except (AttributeError, KeyError):
        return None
    return getattr(getattr(app, "state", None), "services", None)


def is_authorized(request: Request, target: Any, provided_key: str | None = None) -> bool:
    is_legacy_str = isinstance(target, str)
    if is_legacy_str:
        token = target
        services = _app_services(request)
    else:
        services = target
        token = getattr(services, "token", "")

    user: AuthUser | None = None
    library = getattr(services, "library", None)
    if library is not None and token:
        default_username = os.environ.get("KEO_USERNAME", "admin")
        user = library.ensure_auth_user(token, default_username=default_username)

    candidate_key = extract_api_key(request, provided_key)
    if candidate_key:
        if user is not None:
            if hmac.compare_digest(candidate_key.encode(), user.api_key.encode()):
                return True
            if not user.api_key_rotated and hmac.compare_digest(
                candidate_key.encode(), token.encode()
            ):
                return True
            return False
        return bool(token) and hmac.compare_digest(candidate_key.encode(), token.encode())

    cookie = request.cookies.get(COOKIE_NAME, "")
    if not cookie or not token:
        return False
    if user is not None:
        if hmac.compare_digest(cookie.encode(), session_value(token, user).encode()):
            return True
        if is_legacy_str:
            return hmac.compare_digest(cookie.encode(), session_value(token).encode())
        return False
    return hmac.compare_digest(cookie.encode(), session_value(token).encode())
