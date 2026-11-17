import sqlite3
from types import SimpleNamespace

from starlette.requests import Request

from keo.cookies import CookieGate
from keo.jobs import Worker
from keo.library import LATEST_VERSION, Library
from keo.system import run_health_checks
from keo.web.auth import COOKIE_NAME, extract_api_key, is_authorized, session_value
from tests.fakes import FakeSpotdl


def _make_request(
    *,
    headers: dict[str, str] | None = None,
    query_string: str = "",
    cookies: dict[str, str] | None = None,
) -> Request:
    raw_headers: list[tuple[bytes, bytes]] = []
    for k, v in (headers or {}).items():
        raw_headers.append((k.lower().encode("latin-1"), v.encode("latin-1")))
    if cookies:
        cookie_str = "; ".join(f"{k}={v}" for k, v in cookies.items())
        raw_headers.append((b"cookie", cookie_str.encode("latin-1")))
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/api/library",
        "headers": raw_headers,
        "query_string": query_string.encode("latin-1"),
    }
    return Request(scope)


def test_library_auth_user_seed_verify_update_and_rotate(tmp_path):
    lib = Library(tmp_path / "keo.db", tmp_path / "archive.txt")
    user = lib.ensure_auth_user(default_password="initial-secret", default_username="  Admin ")
    assert user.username == "admin"
    assert len(user.api_key) == 32
    assert user.api_key_rotated is False

    # Case-insensitive username + trimmed whitespace
    assert lib.verify_user("ADMIN ", "initial-secret") is not None
    assert lib.verify_user("admin", "wrong-password") is None
    assert lib.verify_user("other", "initial-secret") is None

    # Updating credentials hashes new password and normalizes username
    updated = lib.update_credentials("  TamNguyen ", "new-pass-123")
    assert updated.username == "tamnguyen"
    assert lib.verify_user("tamnguyen", "new-pass-123") is not None
    assert lib.verify_user("admin", "initial-secret") is None

    # Rotating API key generates a new 32-char hex key and marks api_key_rotated=True
    old_key = updated.api_key
    new_key = lib.reset_api_key()
    assert len(new_key) == 32
    assert new_key != old_key
    after = lib.get_auth_user()
    assert after is not None
    assert after.api_key == new_key
    assert after.api_key_rotated is True

    # Emergency password reset preserves the API key while restoring default credentials
    reset_user = lib.ensure_auth_user(
        default_password="initial-secret",
        default_username="admin",
        reset_password=True,
    )
    assert reset_user.username == "admin"
    assert reset_user.api_key == new_key
    assert reset_user.api_key_rotated is True
    assert lib.verify_user("admin", "initial-secret") is not None


def test_library_migrates_v6_to_v7_with_auth_user(tmp_path):
    db_path = tmp_path / "v6.db"
    con = sqlite3.connect(db_path)
    con.executescript(
        """
        CREATE TABLE songs (
            id INTEGER PRIMARY KEY, video_id TEXT UNIQUE, artist_key TEXT NOT NULL,
            title_key TEXT NOT NULL, spotify_id TEXT UNIQUE, artist TEXT NOT NULL,
            title TEXT NOT NULL, audio_path TEXT, audio_mtime REAL, audio_size INTEGER,
            audio_card TEXT, audio_details TEXT, has_cover INTEGER,
            UNIQUE (artist_key, title_key)
        );
        INSERT INTO songs (video_id, artist_key, title_key, spotify_id, artist, title)
        VALUES ('v1', 'a', 'b', 'sp1', 'A', 'B');
        PRAGMA user_version = 6;
        """
    )
    con.close()

    lib = Library(db_path, tmp_path / "archive.txt")
    assert LATEST_VERSION == 7
    assert sqlite3.connect(db_path).execute("PRAGMA user_version").fetchone()[0] == 7
    assert len(lib.list_songs()) == 1
    user = lib.ensure_auth_user("secret")
    assert user.username == "admin"


def test_auth_helpers_api_key_sources_rotation_and_session_isolation(tmp_path):
    lib = Library(tmp_path / "keo.db", tmp_path / "archive.txt")
    services = SimpleNamespace(token="bootstrap-token", library=lib)
    user = lib.ensure_auth_user(services.token)

    # 3-way API key extraction + bootstrap token accepted before rotation
    req_header = _make_request(headers={"X-Api-Key": user.api_key})
    assert extract_api_key(req_header) == user.api_key
    assert is_authorized(req_header, services) is True

    req_query = _make_request(query_string=f"apikey={user.api_key}")
    assert extract_api_key(req_query) == user.api_key
    assert is_authorized(req_query, services) is True

    req_bearer = _make_request(headers={"Authorization": f"Bearer {user.api_key}"})
    assert extract_api_key(req_bearer) == user.api_key
    assert is_authorized(req_bearer, services) is True

    assert is_authorized(_make_request(), services, provided_key=user.api_key) is True
    assert is_authorized(_make_request(), services, provided_key="wrong-key") is False

    req_bootstrap = _make_request(headers={"Authorization": "Bearer bootstrap-token"})
    assert is_authorized(req_bootstrap, services) is True

    # Browser session cookie
    cookie_val = session_value(services.token, user)
    req_cookie = _make_request(cookies={COOKIE_NAME: cookie_val})
    assert is_authorized(req_cookie, services) is True

    # Rotating API key rejects old key and bootstrap token, preserves browser cookie
    new_key = lib.reset_api_key()
    assert is_authorized(_make_request(headers={"X-Api-Key": user.api_key}), services) is False
    assert is_authorized(req_bootstrap, services) is False
    assert is_authorized(_make_request(headers={"X-Api-Key": new_key}), services) is True
    assert is_authorized(req_cookie, services) is True

    # Updating credentials invalidates older browser session cookies, keeps API key valid
    updated = lib.update_credentials("tam", "new-password")
    assert is_authorized(req_cookie, services) is False
    new_cookie = session_value(services.token, updated)
    assert is_authorized(_make_request(cookies={COOKIE_NAME: new_cookie}), services) is True
    assert is_authorized(_make_request(headers={"X-Api-Key": new_key}), services) is True


def test_run_health_checks_api_key_length(tmp_path):
    data_dir = tmp_path / "data"
    music_dir = tmp_path / "music"
    data_dir.mkdir()
    music_dir.mkdir()
    spotdl = FakeSpotdl()
    cookies = CookieGate(probe=lambda _: None, clock=lambda: 0.0)

    # Short API key (< 20 chars) emits a warning
    short_lib = Library(data_dir / "short.db", data_dir / "archive.txt")
    short_lib.ensure_auth_user("bootstrap", default_api_key="short-key")
    short_services = SimpleNamespace(
        token="bootstrap",
        ytm_auth=None,
        ytm_client_id="",
        ytm_client_secret="",
        spotdl=spotdl,
        cookies=cookies,
        music_dir=music_dir,
        data_dir=data_dir,
        library=short_lib,
        worker=Worker(short_lib, spotdl, music_dir=music_dir, cookies=cookies),
    )
    short_checks = run_health_checks(short_services)
    assert ("warn", "API key: shorter than 20 characters") in short_checks

    # Standard 32-char API key does not emit the short-key warning
    ok_lib = Library(data_dir / "ok.db", data_dir / "archive2.txt")
    ok_user = ok_lib.ensure_auth_user("bootstrap")
    assert len(ok_user.api_key) == 32
    ok_services = SimpleNamespace(
        token="bootstrap",
        ytm_auth=None,
        ytm_client_id="",
        ytm_client_secret="",
        spotdl=spotdl,
        cookies=cookies,
        music_dir=music_dir,
        data_dir=data_dir,
        library=ok_lib,
        worker=Worker(ok_lib, spotdl, music_dir=music_dir, cookies=cookies),
    )
    ok_checks = run_health_checks(ok_services)
    assert all("shorter than 20 characters" not in msg for _, msg in ok_checks)
