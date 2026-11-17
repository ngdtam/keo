import sqlite3
from types import SimpleNamespace

from fastapi.testclient import TestClient
from starlette.requests import Request

from keo.cookies import CookieGate
from keo.jobs import Worker
from keo.library import LATEST_VERSION, Library
from keo.system import run_health_checks
from keo.web import Services, create_app
from keo.web.auth import COOKIE_NAME, extract_api_key, is_authorized, session_value
from tests.fakes import TOKEN, FakeSpotdl


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


def test_login_username_password_remember_and_credentials_update(client):
    # Wrong password rejected
    bad = client.post(
        "/api/auth/login",
        json={"username": "admin", "password": "wrong", "remember": False},
    )
    assert bad.status_code == 401

    # Session-only login (remember=False -> no Max-Age in Set-Cookie)
    ok_session = client.post(
        "/api/auth/login",
        json={"username": "  Admin ", "password": TOKEN, "remember": False},
    )
    assert ok_session.status_code == 200
    assert ok_session.json() == {"ok": True, "username": "admin"}
    assert "max-age" not in ok_session.headers.get("set-cookie", "").lower()

    # Persistent login (remember=True -> Max-Age present)
    ok_persist = client.post(
        "/api/auth/login",
        json={"username": "admin", "password": TOKEN, "remember": True},
    )
    assert ok_persist.status_code == 200
    assert "max-age=2592000" in ok_persist.headers.get("set-cookie", "").lower()
    old_cookie = ok_persist.cookies.get(COOKIE_NAME)
    assert old_cookie

    # Validation errors on credentials update
    mismatch = client.post(
        "/api/auth/credentials",
        json={"username": "tam", "password": "newpassword", "password_confirmation": "different"},
    )
    assert mismatch.status_code == 400
    assert "match" in mismatch.json()["detail"].lower()

    blank = client.post(
        "/api/auth/credentials",
        json={"username": "   ", "password": "newpassword", "password_confirmation": "newpassword"},
    )
    assert blank.status_code == 400

    # Valid credentials update refreshes cookie and updates username
    upd = client.post(
        "/api/auth/credentials",
        json={"username": "  Tam ", "password": "newpassword", "password_confirmation": "newpassword"},
    )
    assert upd.status_code == 200
    assert upd.json() == {"ok": True, "username": "tam"}
    assert client.get("/api/dashboard").status_code == 200

    # Older browser cookie is rejected with 401 after credentials update
    with TestClient(client.app) as stale_client:
        stale_client.cookies.set(COOKIE_NAME, old_cookie)
        assert stale_client.get("/api/dashboard").status_code == 401

    # Old password no longer works on a fresh login; new username + password works
    client.post("/api/auth/logout")
    assert client.post("/api/auth/login", json={"username": "admin", "password": TOKEN}).status_code == 401
    assert client.post("/api/auth/login", json={"username": "TAM", "password": "newpassword"}).status_code == 200


def test_api_key_headers_query_rotation_and_session_isolation(client):
    # Log in as browser user
    client.post("/api/auth/login", json={"username": "admin", "password": TOKEN})
    settings_res = client.get("/api/settings")
    assert settings_res.status_code == 200
    initial_api_key = settings_res.json()["api_key"]
    assert settings_res.json()["username"] == "admin"
    assert len(initial_api_key) == 32

    # Clear cookies to act as an external client
    client.cookies.clear()

    # 1. X-Api-Key header works
    assert client.get("/api/system/status", headers={"X-Api-Key": initial_api_key}).status_code == 200
    # 2. ?apikey= query param works
    assert client.get(f"/api/system/status?apikey={initial_api_key}").status_code == 200
    # 3. Authorization: Bearer works
    assert client.get("/api/system/status", headers={"Authorization": f"Bearer {initial_api_key}"}).status_code == 200

    # Rotate API key while logged in via browser session
    client.post("/api/auth/login", json={"username": "admin", "password": TOKEN})
    rot = client.post("/api/auth/api-key/reset")
    assert rot.status_code == 200
    new_key = rot.json()["api_key"]
    assert len(new_key) == 32
    assert new_key != initial_api_key

    # Browser session still works after API key rotation!
    assert client.get("/api/dashboard").status_code == 200

    # External client: old key and bootstrap TOKEN now rejected, new_key works
    client.cookies.clear()
    assert client.get("/api/system/status", headers={"X-Api-Key": initial_api_key}).status_code == 401
    assert client.get("/api/system/status", headers={"Authorization": f"Bearer {TOKEN}"}).status_code == 401
    assert client.get("/api/system/status", headers={"X-Api-Key": new_key}).status_code == 200


def test_ping_cors_and_openapi_security_schemes(client):
    # Public /ping requires no auth
    ping = client.get("/ping")
    assert ping.status_code == 200
    assert ping.json() == {"status": "ok"}

    # CORS preflight from an external website origin
    cors = client.options(
        "/api/library",
        headers={
            "Origin": "https://dashboard.example.com",
            "Access-Control-Request-Method": "GET",
            "Access-Control-Request-Headers": "X-Api-Key",
        },
    )
    assert cors.status_code == 200
    assert cors.headers.get("access-control-allow-origin") == "*"
    assert "access-control-allow-credentials" not in cors.headers

    # OpenAPI schema includes X-Api-Key, apikey, and BearerAuth securitySchemes
    schema = client.get("/openapi.json").json()
    schemes = schema["components"]["securitySchemes"]
    assert "X-Api-Key" in schemes
    assert schemes["X-Api-Key"]["in"] == "header"
    assert "apikey" in schemes
    assert schemes["apikey"]["in"] == "query"
    assert "BearerAuth" in schemes


def test_create_app_reset_password_env_preserves_api_key(
    catalog, library, worker, spotdl, cookies, tmp_path, monkeypatch
):
    library.ensure_auth_user(TOKEN)
    library.update_credentials("customuser", "custom-pass")
    rotated_key = library.reset_api_key()

    monkeypatch.setenv("KEO_RESET_PASSWORD", "1")
    monkeypatch.setenv("KEO_USERNAME", "admin")
    services = Services(
        token=TOKEN,
        catalog=catalog,
        library=library,
        worker=worker,
        spotdl=spotdl,
        cookies=cookies,
        run_later=lambda fn: fn(),
        music_dir=tmp_path,
    )
    with TestClient(create_app(services)) as reset_client:
        # Custom credentials replaced by default username + services.token
        assert (
            reset_client.post(
                "/api/auth/login",
                json={"username": "customuser", "password": "custom-pass"},
            ).status_code
            == 401
        )
        assert (
            reset_client.post(
                "/api/auth/login",
                json={"username": "admin", "password": TOKEN},
            ).status_code
            == 200
        )
        # Existing API key was preserved across the password reset
        assert (
            reset_client.get("/api/system/status", headers={"X-Api-Key": rotated_key}).status_code
            == 200
        )


def test_external_api_cover_library_trash_review_and_tasks(client, library, tmp_path, monkeypatch):
    user = library.ensure_auth_user(TOKEN)
    api_key = user.api_key
    headers = {"X-Api-Key": api_key}

    # Rescan and organize via JSON API
    rescan = client.post("/api/library/rescan", headers=headers)
    assert rescan.status_code == 200
    assert rescan.json()["ok"] is True

    org = client.post("/api/library/organize", headers=headers)
    assert org.status_code == 200
    assert org.json() == {"ok": True, "moved": 0}

    # Add a song on disk and fetch its cover via ?apikey= query param
    music_dir = client.app.state.services.music_dir
    audio_file = music_dir / "Sơn Tùng M-TP - Lạc Trôi.mp3"
    audio_file.write_bytes(b"fake-mp3-bytes")
    library.mark_owned("vid1", "Sơn Tùng M-TP", "Lạc Trôi", "sp1")
    library.set_audio_path("sp1", str(audio_file))

    assert client.get("/api/library/spotify/sp1/cover").status_code == 401
    monkeypatch.setattr("keo.web.routes_library.cover_bytes", lambda path: None)
    assert client.get(f"/api/library/spotify/sp1/cover?apikey={api_key}").status_code == 404
    monkeypatch.setattr(
        "keo.web.routes_library.cover_bytes",
        lambda path: (b"jpeg_data", "image/jpeg"),
    )
    cover = client.get(f"/api/library/spotify/sp1/cover?apikey={api_key}")
    assert cover.status_code == 200
    assert cover.content == b"jpeg_data"
    assert cover.headers["content-type"] == "image/jpeg"

    # Delete song to trash via JSON API
    del_res = client.delete("/api/library/spotify/sp1?scope=file", headers=headers)
    assert del_res.status_code == 200
    assert del_res.json() == {"ok": True, "deleted": "sp1", "scope": "file"}
    assert not audio_file.exists()

    # Trash list, path traversal protection, restore, delete, and empty via JSON API
    trash = client.get(f"/api/library/trash?apikey={api_key}").json()
    assert len(trash["items"]) == 1
    trashed_name = trash["items"][0]["name"]

    bad_restore = client.post("/api/library/trash/restore", json={"name": "../evil"}, headers=headers)
    assert bad_restore.status_code == 400
    bad_delete = client.delete("/api/library/trash/%2e%2e%2fevil", headers=headers)
    assert bad_delete.status_code == 400
    missing_restore = client.post(
        "/api/library/trash/restore", json={"name": "nonexistent.mp3"}, headers=headers
    )
    assert missing_restore.status_code == 404

    restored = client.post("/api/library/trash/restore", json={"name": trashed_name}, headers=headers)
    assert restored.status_code == 200
    assert restored.json() == {"ok": True}
    assert audio_file.exists()

    # Bulk delete via JSON API, then permanent trash delete and empty
    restored_song = library.list_songs()[0]
    bulk = client.post(
        "/api/library/bulk",
        json={"action": "delete", "items": [f"song:{restored_song.id}"]},
        headers=headers,
    )
    assert bulk.status_code == 200
    assert bulk.json() == {"ok": True, "action": "delete"}
    assert not audio_file.exists()

    trash_after_bulk = client.get("/api/library/trash", headers=headers).json()["items"]
    assert len(trash_after_bulk) == 1
    perm_del = client.delete(
        f"/api/library/trash/{trash_after_bulk[0]['name']}",
        headers=headers,
    )
    assert perm_del.status_code == 200
    assert perm_del.json() == {"ok": True}
    assert client.get("/api/library/trash", headers=headers).json()["items"] == []

    # Empty trash endpoint
    empty_res = client.delete("/api/library/trash", headers=headers)
    assert empty_res.status_code == 200
    assert empty_res.json() == {"ok": True}

    # Watch/unwatch playlist and follow/unfollow artist via JSON API
    wp = client.post(
        "/api/review/playlists",
        json={"playlist_id": "https://music.youtube.com/playlist?list=PLvn", "auto_queue": True},
        headers=headers,
    )
    assert wp.status_code == 200
    assert wp.json()["playlist_id"] == "PLvn"
    assert client.post("/api/review/playlists/sync", headers=headers).status_code == 200
    assert client.delete("/api/review/playlists/PLvn", headers=headers).status_code == 200

    fa = client.post("/api/review/artists", json={"artist": "Sơn Tùng M-TP"}, headers=headers)
    assert fa.status_code == 200
    cid = fa.json()["channel_id"]
    assert client.post("/api/review/artists/sync", headers=headers).status_code == 200
    assert client.delete(f"/api/review/artists/{cid}", headers=headers).status_code == 200

    # Run scheduled task via JSON API
    task_ok = client.post("/api/settings/tasks/housekeeping/run", headers=headers)
    assert task_ok.status_code == 200
    assert task_ok.json() == {"ok": True, "task": "housekeeping"}

    task_404 = client.post("/api/settings/tasks/unknown_task/run", headers=headers)
    assert task_404.status_code == 404

    # Validation errors & OpenAPI tags on new endpoints
    assert client.post("/api/library/bulk", json={"action": "bogus", "items": []}, headers=headers).status_code == 400
    assert client.delete("/api/library/spotify/sp1?scope=bogus", headers=headers).status_code == 400
    assert client.post("/api/review/playlists", json={"playlist_id": "   "}, headers=headers).status_code == 400
    assert client.post("/api/review/artists", json={"artist": "   "}, headers=headers).status_code == 400

    schema = client.get("/openapi.json").json()
    assert schema["paths"]["/api/library/rescan"]["post"]["tags"] == ["Library"]
    assert schema["paths"]["/api/review/playlists"]["post"]["tags"] == ["Review & Discovery"]
    assert schema["paths"]["/api/settings/tasks/{name}/run"]["post"]["tags"] == ["Settings & Tasks"]
