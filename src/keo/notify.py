"""Lightweight webhook notifications and media-server library refresh."""

from __future__ import annotations

import json
import logging
import urllib.request
from collections.abc import Callable

log = logging.getLogger(__name__)
REQUEST_TIMEOUT_SECONDS = 5


def _is_discord(url: str) -> bool:
    low = url.casefold()
    return "discord.com/api/webhooks/" in low or "discordapp.com/api/webhooks/" in low


def send_notification(
    url: str,
    title: str,
    message: str,
    *,
    opener: Callable[..., object] = urllib.request.urlopen,
) -> bool:
    """POST a short JSON notification to ntfy, Gotify, Discord, or a generic webhook."""
    target = url.strip()
    if not target.startswith(("http://", "https://")):
        return False
    payload = (
        {"content": f"**{title}**\n{message}"}
        if _is_discord(target)
        else {"title": title, "message": message, "content": f"{title}: {message}"}
    )
    req = urllib.request.Request(
        target,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with opener(req, timeout=REQUEST_TIMEOUT_SECONDS):
            return True
    except Exception as exc:
        log.warning("Notification to %s failed: %s", target, exc)
        return False


def trigger_media_refresh(
    url: str,
    *,
    opener: Callable[..., object] = urllib.request.urlopen,
) -> bool:
    """Send a single HTTP GET to refresh Navidrome/Subsonic, Jellyfin, or Plex."""
    target = url.strip()
    if not target.startswith(("http://", "https://")):
        return False
    req = urllib.request.Request(target, method="GET")
    try:
        with opener(req, timeout=REQUEST_TIMEOUT_SECONDS):
            return True
    except Exception as exc:
        log.warning("Media server refresh %s failed: %s", target, exc)
        return False

