"""CLI helper to reset the Kéo web sign-in password inside the container."""

from __future__ import annotations

import os
import sys
from pathlib import Path

from keo.library import Library


def reset_password(
    data_dir: Path | None = None,
    *,
    token: str | None = None,
    username: str | None = None,
) -> str:
    """Reset the stored password in keo.db while preserving library data and API key."""
    resolved_dir = data_dir or Path(os.environ.get("KEO_DATA_DIR", "/data"))
    db_path = resolved_dir / "keo.db"
    if not db_path.is_file():
        raise FileNotFoundError(f"Database not found: {db_path}")
    lib = Library(db_path, resolved_dir / "archive.txt")
    env_token = (os.environ.get("KEO_TOKEN", "") if token is None else token).strip()
    if env_token:
        env_user = os.environ.get("KEO_USERNAME", "admin") if username is None else username
        user = lib.ensure_auth_user(
            env_token,
            default_username=env_user,
            default_api_key=os.environ.get("KEO_API_KEY", ""),
            reset_password=True,
        )
        return f"Password reset to KEO_TOKEN for user '{user.username}'."
    lib.reset_auth_password()
    return "Password cleared. Refresh Kéo in your browser to create a new account at /setup."


def main() -> None:
    try:
        print(reset_password())
    except FileNotFoundError as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    main()
