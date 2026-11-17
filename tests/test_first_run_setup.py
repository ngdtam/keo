import json
import sqlite3
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from starlette.requests import Request

import keo.web.app as web_app
from keo.catalog import Catalog
from keo.library import LATEST_VERSION, Library
from keo.web import Services, create_app
from keo.web.auth import (
    COOKIE_NAME,
    ensure_services_auth_user,
    is_authorized,
    session_value,
)


def _make_request(
    *,
    headers: dict[str, str] | None = None,
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
        "query_string": b"",
    }
    return Request(scope)


def test_fresh_library_needs_setup(tmp_path):
    lib = Library(tmp_path / "keo.db", tmp_path / "archive.txt")
    assert lib.needs_setup() is True
    assert lib.get_auth_user() is None


def test_create_initial_user_and_verify(tmp_path):
    lib = Library(tmp_path / "keo.db", tmp_path / "archive.txt")

    with pytest.raises(ValueError, match="Username"):
        lib.create_initial_user("   ", "mypassword")
    with pytest.raises(ValueError, match="Password"):
        lib.create_initial_user("tam", "   ")

    user = lib.create_initial_user("  Tam ", "mypassword")
    assert lib.needs_setup() is False
    assert user.username == "tam"
    assert len(user.api_key) == 32
    assert user.api_key_rotated is False
    assert len(user.session_secret) == 64

    verified = lib.verify_user("tam", "mypassword")
    assert verified is not None
    assert verified.username == "tam"
    assert lib.verify_user("tam", "wrong") is None


def test_reset_auth_password_preserves_api_key_and_allows_re_setup(tmp_path):
    lib = Library(tmp_path / "keo.db", tmp_path / "archive.txt")
    initial = lib.create_initial_user("tam", "mypassword")
    rotated_key = lib.reset_api_key()
    before_reset = lib.get_auth_user()
    assert before_reset is not None
    assert before_reset.api_key == rotated_key
    assert before_reset.api_key_rotated is True

    lib.reset_auth_password()
    assert lib.needs_setup() is True
    assert lib.verify_user("tam", "mypassword") is None

    after_reset = lib.get_auth_user()
    assert after_reset is not None
    assert after_reset.password_hash == ""
    assert after_reset.salt == ""
    assert after_reset.session_secret != initial.session_secret
    assert len(after_reset.session_secret) == 64
    assert after_reset.api_key == rotated_key
    assert after_reset.api_key_rotated is True

    recreated = lib.create_initial_user("newuser", "newpass")
    assert lib.needs_setup() is False
    assert recreated.username == "newuser"
    assert recreated.api_key == rotated_key
    assert recreated.api_key_rotated is True
    assert lib.verify_user("newuser", "newpass") is not None


def test_library_migrates_v7_to_v8_adds_session_secret(tmp_path):
    db_path = tmp_path / "v7.db"
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
        CREATE TABLE auth_user (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            username TEXT NOT NULL,
            password_hash TEXT NOT NULL,
            salt TEXT NOT NULL,
            api_key TEXT NOT NULL,
            api_key_rotated INTEGER NOT NULL DEFAULT 0,
            updated_at TEXT NOT NULL
        );
        INSERT INTO auth_user (id, username, password_hash, salt, api_key, api_key_rotated, updated_at)
        VALUES (1, 'admin', 'deadbeef', 'cafebabe', '0123456789abcdef0123456789abcdef', 1, '2026-01-01T00:00:00+00:00');
        PRAGMA user_version = 7;
        """
    )
    con.close()

    lib = Library(db_path, tmp_path / "archive.txt")
    assert LATEST_VERSION == 8
    assert sqlite3.connect(db_path).execute("PRAGMA user_version").fetchone()[0] == 8
    user = lib.get_auth_user()
    assert user is not None
    assert user.username == "admin"
    assert user.password_hash == "deadbeef"
    assert user.api_key == "0123456789abcdef0123456789abcdef"
    assert user.api_key_rotated is True
    assert user.session_secret == ""


def test_token_free_auth_helpers_sessions_and_lockout_reset(
    catalog, library, worker, spotdl, cookies, tmp_path, monkeypatch
):
    services = SimpleNamespace(token="", library=library)

    # Without KEO_TOKEN, ensure_services_auth_user does not auto-seed
    assert ensure_services_auth_user(services) is None
    assert library.get_auth_user() is None
    assert is_authorized(_make_request(), services) is False
    assert is_authorized(_make_request(headers={"X-Api-Key": "anything"}), services) is False

    # After create_initial_user, cookie session and API key both work with token == ""
    user = library.create_initial_user("tam", "secret-pass")
    cookie_val = session_value("", user)
    assert cookie_val
    assert is_authorized(_make_request(cookies={COOKIE_NAME: cookie_val}), services) is True
    assert is_authorized(_make_request(headers={"X-Api-Key": user.api_key}), services) is True

    # Updating credentials rotates session_secret and invalidates older cookie even when token == ""
    updated = library.update_credentials("tam", "newer-pass")
    assert updated.session_secret != user.session_secret
    assert is_authorized(_make_request(cookies={COOKIE_NAME: cookie_val}), services) is False
    new_cookie = session_value("", updated)
    assert is_authorized(_make_request(cookies={COOKIE_NAME: new_cookie}), services) is True

    # KEO_RESET_PASSWORD=1 with token="" clears password, invalidates cookie, keeps API key
    rotated_key = library.reset_api_key()
    monkeypatch.setenv("KEO_RESET_PASSWORD", "1")
    app_services = Services(
        token="",
        catalog=catalog,
        library=library,
        worker=worker,
        spotdl=spotdl,
        cookies=cookies,
        run_later=lambda fn: fn(),
        music_dir=tmp_path,
    )
    with TestClient(create_app(app_services)) as client:
        assert library.needs_setup() is True
        client.cookies.set(COOKIE_NAME, new_cookie)
        assert client.get("/api/dashboard").status_code == 401
        client.cookies.clear()
        assert client.get("/api/system/status", headers={"X-Api-Key": rotated_key}).status_code == 200


_VALID_RAW_HEADERS = (
    "cookie: __Secure-3PAPISID=x; SAPISID=x; SID=y\n"
    "x-goog-authuser: 0\n"
    "authorization: SAPISIDHASH 0_deadbeef\n"
)


def test_first_run_setup_api_and_ytm_hot_reload(library, worker, spotdl, cookies, tmp_path):
    data_dir = tmp_path / "data"
    music_dir = tmp_path / "music"
    music_dir.mkdir()
    services = Services(
        token="",
        catalog=Catalog(None),
        library=library,
        worker=worker,
        spotdl=spotdl,
        cookies=cookies,
        run_later=lambda fn: fn(),
        data_dir=data_dir,
        music_dir=music_dir,
    )

    with TestClient(create_app(services)) as client:
        # 1. Initial status: needs setup, ytm not configured
        status = client.get("/api/auth/status")
        assert status.status_code == 200
        assert status.json() == {"needs_setup": True, "ytm_configured": False}

        # 2. Setup validation errors (blank username, blank password, mismatching password)
        assert (
            client.post(
                "/api/auth/setup",
                json={"username": "  ", "password": "secret", "password_confirmation": "secret"},
            ).status_code
            == 400
        )
        assert (
            client.post(
                "/api/auth/setup",
                json={"username": "tam", "password": "   ", "password_confirmation": "   "},
            ).status_code
            == 400
        )
        mismatch = client.post(
            "/api/auth/setup",
            json={"username": "tam", "password": "secret", "password_confirmation": "other"},
        )
        assert mismatch.status_code == 400

        # Valid setup succeeds, sets keo_session cookie, returns api_key
        setup_res = client.post(
            "/api/auth/setup",
            json={"username": "  Tam ", "password": "secret", "password_confirmation": "secret"},
        )
        assert setup_res.status_code == 200
        body = setup_res.json()
        assert body["ok"] is True
        assert body["username"] == "tam"
        assert len(body["api_key"]) == 32
        assert client.cookies.get(COOKIE_NAME)
        assert client.get("/api/dashboard").status_code == 200

        # 3. Second setup attempt returns 409 Conflict
        second = client.post(
            "/api/auth/setup",
            json={"username": "other", "password": "secret", "password_confirmation": "secret"},
        )
        assert second.status_code == 409
        assert "already complete" in second.json()["detail"].lower()

        # 4. Logout + login works with token=""
        client.post("/api/auth/logout")
        assert client.get("/api/dashboard").status_code == 401
        login_res = client.post(
            "/api/auth/login",
            json={"username": "tam", "password": "secret", "remember": True},
        )
        assert login_res.status_code == 200
        assert login_res.json() == {"ok": True, "username": "tam"}
        assert client.get("/api/dashboard").status_code == 200

        # 5. YTM setup: unauthenticated rejected; incomplete headers rejected; empty payload rejected
        unauth_ytm = TestClient(client.app).post(
            "/api/settings/ytm", json={"headers_raw": _VALID_RAW_HEADERS}
        )
        assert unauth_ytm.status_code == 401

        assert client.post("/api/settings/ytm", json={"headers_raw": ""}).status_code == 400
        assert (
            client.post("/api/settings/ytm", json={"headers_raw": "cookie: SID=y\n"}).status_code
            == 400
        )
        # Missing authorization / __Secure-3PAPISID fails open_ytmusic -> 400, catalog stays unconfigured
        assert (
            client.post(
                "/api/settings/ytm",
                json={"headers_raw": "cookie: SAPISID=x; SID=y\nx-goog-authuser: 0\n"},
            ).status_code
            == 400
        )
        assert services.catalog.is_configured() is False
        assert not (data_dir / "browser.json").exists()

        # Invalid JSON file upload rejected; valid JSON object missing auth fields rejected
        assert (
            client.post(
                "/api/settings/ytm",
                files={"auth_file": ("browser.json", b"not-json", "application/json")},
            ).status_code
            == 400
        )
        assert (
            client.post(
                "/api/settings/ytm",
                files={"auth_file": ("browser.json", b'["not","a","dict"]', "application/json")},
            ).status_code
            == 400
        )
        assert (
            client.post(
                "/api/settings/ytm",
                files={"auth_file": ("browser.json", b'{"foo":"bar"}', "application/json")},
            ).status_code
            == 400
        )

        # Valid raw headers (JSON) configures catalog and writes data_dir/browser.json
        ytm_ok = client.post("/api/settings/ytm", json={"headers_raw": _VALID_RAW_HEADERS})
        assert ytm_ok.status_code == 200
        assert ytm_ok.json() == {"ok": True, "ytm_configured": True}
        assert (data_dir / "browser.json").is_file()
        assert services.ytm_auth == data_dir / "browser.json"
        assert services.catalog.is_configured() is True

        # Status now shows setup complete & ytm configured
        status_after = client.get("/api/auth/status")
        assert status_after.status_code == 200
        assert status_after.json() == {"needs_setup": False, "ytm_configured": True}

        # Bad upload after valid setup preserves existing browser.json & configured catalog
        prev_bytes = (data_dir / "browser.json").read_bytes()
        bad_after = client.post(
            "/api/settings/ytm",
            files={"auth_file": ("browser.json", b'{"foo":"bar"}', "application/json")},
        )
        assert bad_after.status_code == 400
        assert (data_dir / "browser.json").read_bytes() == prev_bytes
        assert services.catalog.is_configured() is True

        # Valid browser.json file upload also works
        valid_browser_json = json.dumps(
            {
                "cookie": "__Secure-3PAPISID=uploaded; SID=y",
                "x-goog-authuser": "0",
                "authorization": "SAPISIDHASH 1_uploaded",
            }
        ).encode("utf-8")
        upload_ok = client.post(
            "/api/settings/ytm",
            files={"auth_file": ("browser.json", valid_browser_json, "application/json")},
        )
        assert upload_ok.status_code == 200
        assert upload_ok.json() == {"ok": True, "ytm_configured": True}
        assert json.loads((data_dir / "browser.json").read_text("utf-8"))["cookie"] == (
            "__Secure-3PAPISID=uploaded; SID=y"
        )

        # Valid multipart headers_raw form field also works
        form_ok = client.post(
            "/api/settings/ytm",
            data={"headers_raw": _VALID_RAW_HEADERS},
        )
        assert form_ok.status_code == 200
        assert form_ok.json() == {"ok": True, "ytm_configured": True}

        # Oversized auth_file is rejected with 400
        oversized = client.post(
            "/api/settings/ytm",
            files={"auth_file": ("browser.json", b"x" * (1_048_576 + 1), "application/json")},
        )
        assert oversized.status_code == 400

        # API key returned by /api/auth/setup works on /api/settings/ytm without cookies
        api_client = TestClient(client.app)
        assert (
            api_client.post(
                "/api/settings/ytm",
                json={"headers_raw": _VALID_RAW_HEADERS},
                headers={"X-Api-Key": body["api_key"]},
            ).status_code
            == 200
        )

        # Missing data_dir returns 400
        services.data_dir = None
        assert client.post("/api/settings/ytm", json={"headers_raw": _VALID_RAW_HEADERS}).status_code == 400


def test_setup_spa_redirects(library, worker, spotdl, cookies, tmp_path, monkeypatch):
    ui_dir = tmp_path / "ui"
    ui_dir.mkdir()
    (ui_dir / "index.html").write_text('<!doctype html><html><body><div id="app"></div></body></html>')
    monkeypatch.setattr(web_app, "UI_DIST_DIR", ui_dir)

    music_dir = tmp_path / "music"
    music_dir.mkdir()
    services = Services(
        token="",
        catalog=Catalog(None),
        library=library,
        worker=worker,
        spotdl=spotdl,
        cookies=cookies,
        run_later=lambda fn: fn(),
        data_dir=tmp_path / "data",
        music_dir=music_dir,
    )

    with TestClient(create_app(services, serve_ui=True)) as spa:
        # While needs_setup=True: /setup serves 200, / and /login redirect to /setup
        setup_page = spa.get("/setup")
        assert setup_page.status_code == 200
        assert '<div id="app"></div>' in setup_page.text

        root_redirect = spa.get("/", follow_redirects=False)
        assert root_redirect.status_code == 303
        assert root_redirect.headers["location"] == "/setup"

        login_redirect = spa.get("/login", follow_redirects=False)
        assert login_redirect.status_code == 303
        assert login_redirect.headers["location"] == "/setup"

        # Complete setup (sets keo_session cookie on spa client)
        assert (
            spa.post(
                "/api/auth/setup",
                json={"username": "tam", "password": "secret", "password_confirmation": "secret"},
            ).status_code
            == 200
        )

        # Authenticated after setup: /setup redirects to /, / serves 200
        setup_authed = spa.get("/setup", follow_redirects=False)
        assert setup_authed.status_code == 303
        assert setup_authed.headers["location"] == "/"
        assert spa.get("/").status_code == 200

        # Unauthenticated after setup: /setup and / redirect to /login, /login serves 200
        spa.cookies.clear()
        setup_unauth = spa.get("/setup", follow_redirects=False)
        assert setup_unauth.status_code == 303
        assert setup_unauth.headers["location"] == "/login"

        root_unauth = spa.get("/", follow_redirects=False)
        assert root_unauth.status_code == 303
        assert root_unauth.headers["location"] == "/login"

        assert spa.get("/login").status_code == 200


def test_v7_migrated_user_login_without_token_backfills_session_secret(
    catalog, worker, spotdl, cookies, tmp_path
):
    from keo.library import hash_password

    db_path = tmp_path / "v7.db"
    salt = "00112233445566778899aabbccddeeff"
    pw_hash = hash_password("legacy-pass", salt)
    con = sqlite3.connect(db_path)
    con.executescript(
        f"""
        CREATE TABLE songs (
            id INTEGER PRIMARY KEY, video_id TEXT UNIQUE, artist_key TEXT NOT NULL,
            title_key TEXT NOT NULL, spotify_id TEXT UNIQUE, artist TEXT NOT NULL,
            title TEXT NOT NULL, audio_path TEXT, audio_mtime REAL, audio_size INTEGER,
            audio_card TEXT, audio_details TEXT, has_cover INTEGER,
            UNIQUE (artist_key, title_key)
        );
        CREATE TABLE auth_user (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            username TEXT NOT NULL,
            password_hash TEXT NOT NULL,
            salt TEXT NOT NULL,
            api_key TEXT NOT NULL,
            api_key_rotated INTEGER NOT NULL DEFAULT 0,
            updated_at TEXT NOT NULL
        );
        INSERT INTO auth_user (id, username, password_hash, salt, api_key, api_key_rotated, updated_at)
        VALUES (1, 'admin', '{pw_hash}', '{salt}', '0123456789abcdef0123456789abcdef', 0, '2026-01-01T00:00:00+00:00');
        PRAGMA user_version = 7;
        """
    )
    con.close()

    lib = Library(db_path, tmp_path / "archive.txt")
    migrated = lib.get_auth_user()
    assert migrated is not None
    assert migrated.session_secret == ""

    music_dir = tmp_path / "music"
    music_dir.mkdir()
    services = Services(
        token="",
        catalog=catalog,
        library=lib,
        worker=worker,
        spotdl=spotdl,
        cookies=cookies,
        run_later=lambda fn: fn(),
        data_dir=tmp_path / "data",
        music_dir=music_dir,
    )
    with TestClient(create_app(services)) as client:
        login_res = client.post(
            "/api/auth/login",
            json={"username": "admin", "password": "legacy-pass"},
        )
        assert login_res.status_code == 200
        assert client.cookies.get(COOKIE_NAME)
        assert client.get("/api/dashboard").status_code == 200
        refreshed = lib.get_auth_user()
        assert refreshed is not None
        assert len(refreshed.session_secret) == 64
