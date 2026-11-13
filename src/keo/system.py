"""System health checks and weekly SQLite/config backups."""

from __future__ import annotations

import importlib.metadata
import json
import os
import shutil
import sqlite3
import urllib.request
from collections.abc import Callable
from datetime import date, datetime, timezone
from pathlib import Path

from keo.config import ConfigError, auth_gaps

WATCHED_PACKAGES = ("yt-dlp", "ytmusicapi", "spotdl", "pymediainfo")


def last_backup_date(data_dir: Path) -> date | None:
    backups_dir = data_dir / "backups"
    if not backups_dir.is_dir():
        return None
    dates: list[date] = []
    for entry in backups_dir.iterdir():
        if not entry.is_dir():
            continue
        try:
            dates.append(date.fromisoformat(entry.name))
        except ValueError:
            continue
    return max(dates) if dates else None


def backup_data(
    data_dir: Path,
    config_path: Path | None = None,
    *,
    keep: int = 4,
    today: date | None = None,
) -> Path:
    """Back up keo.db (via sqlite3 backup API), archive.txt, and keo.toml into data/backups/YYYY-MM-DD."""
    day = today or datetime.now(timezone.utc).date()
    backups_dir = data_dir / "backups"
    dest = backups_dir / day.isoformat()
    dest.mkdir(parents=True, exist_ok=True)

    db_path = data_dir / "keo.db"
    if db_path.is_file():
        src_con = sqlite3.connect(db_path)
        dst_con = sqlite3.connect(dest / "keo.db")
        try:
            src_con.backup(dst_con)
        finally:
            dst_con.close()
            src_con.close()

    archive_path = data_dir / "archive.txt"
    if archive_path.is_file():
        shutil.copy2(archive_path, dest / "archive.txt")

    if config_path is not None and config_path.is_file():
        shutil.copy2(config_path, dest / config_path.name)

    if keep > 0 and backups_dir.is_dir():
        dated = sorted(
            (p for p in backups_dir.iterdir() if p.is_dir()),
            key=lambda p: p.name,
            reverse=True,
        )
        for old in dated[keep:]:
            shutil.rmtree(old, ignore_errors=True)

    return dest


def _pkg_version(name: str) -> str:
    try:
        return importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError:
        return "not installed"


def _latest_pypi_version(name: str, timeout: float = 3.0) -> str | None:
    url = f"https://pypi.org/pypi/{name}/json"
    req = urllib.request.Request(url, headers={"Accept": "application/json", "User-Agent": "keo"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        version = data.get("info", {}).get("version")
        return str(version) if version else None
    except Exception:
        return None


def check_package_updates(
    packages: tuple[str, ...] = WATCHED_PACKAGES,
    *,
    version_getter: Callable[[str], str] = _pkg_version,
    latest_getter: Callable[[str], str | None] = _latest_pypi_version,
) -> dict[str, tuple[str, str | None, bool]]:
    """Return {package: (installed, latest, update_available)} for watched packages."""
    results: dict[str, tuple[str, str | None, bool]] = {}
    for pkg in packages:
        installed = version_getter(pkg)
        latest = latest_getter(pkg)
        outdated = bool(
            latest
            and installed != "not installed"
            and latest != installed
        )
        results[pkg] = (installed, latest, outdated)
    return results


def run_health_checks(services) -> list[tuple[str, str]]:
    """Return a list of (level, message) system health checks."""
    checks: list[tuple[str, str]] = []

    if services.ytm_auth is None:
        checks.append(("warn", "YTM auth: KEO_YTM_AUTH is not set"))
    else:
        try:
            gaps = auth_gaps(services.ytm_auth, services.ytm_client_id, services.ytm_client_secret)
            if gaps:
                checks.append(("warn", f"YTM auth missing: {', '.join(gaps)}"))
            else:
                checks.append(("ok", "YTM auth: loaded"))
        except ConfigError as exc:
            checks.append(("error", f"YTM auth error: {exc}"))

    cookie_msg = services.cookies.line(services.spotdl.settings)
    cookie_level = "warn" if "expired" in cookie_msg.lower() or "failed" in cookie_msg.lower() else "ok"
    checks.append((cookie_level, f"Cookie: {cookie_msg}"))

    if services.music_dir is None:
        checks.append(("warn", "Music folder: not configured"))
    elif not services.music_dir.is_dir():
        checks.append(("error", f"Music folder missing: {services.music_dir}"))
    else:
        writable = os.access(services.music_dir, os.W_OK)
        free_gb = shutil.disk_usage(services.music_dir).free / (1024 ** 3)
        level = "ok" if writable and free_gb >= 0.5 else "warn"
        checks.append(
            (level, f"Music folder: {'writable' if writable else 'read-only'}, {free_gb:.1f} GB free")
        )

    ffmpeg = shutil.which("ffmpeg")
    checks.append(("ok" if ffmpeg else "warn", f"ffmpeg: {'found' if ffmpeg else 'missing'}"))
    updates = getattr(services, "package_updates", None) or {}
    for pkg, label in (
        ("yt-dlp", "yt-dlp"),
        ("ytmusicapi", "ytmusicapi"),
        ("spotdl", "spotdl"),
        ("pymediainfo", "mediainfo (pymediainfo)"),
    ):
        installed = _pkg_version(pkg)
        entry = updates.get(pkg)
        if entry is not None:
            _, latest, outdated = entry
            if outdated and latest:
                checks.append(("warn", f"{label}: {installed} (latest {latest})"))
            elif latest:
                checks.append(("ok", f"{label}: {installed} (up to date)"))
            else:
                checks.append(("warn", f"{label}: {installed} (PyPI check failed)"))
        else:
            level = "warn" if installed == "not installed" else "ok"
            checks.append((level, f"{label}: {installed}"))

    pull = services.library.last_pull()
    if pull is None:
        checks.append(("ok", "Last history pull: none yet"))
    elif pull.status == "ok":
        checks.append(("ok", f"Last history pull: ok ({pull.started_at})"))
    else:
        checks.append(("warn", f"Last history pull failed: {pull.error or pull.started_at}"))

    backoff = services.worker.backoff_status()
    if backoff["active"]:
        mins = max(1, (backoff["seconds_remaining"] + 59) // 60)
        checks.append(("warn", f"Worker backoff: paused ~{mins}m ({backoff['reason']})"))
    else:
        checks.append(("ok", "Worker: ready"))

    return checks

