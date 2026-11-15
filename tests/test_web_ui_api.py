from fastapi.testclient import TestClient

from keo.web import app as web_app
from keo.web import create_app
from tests.fakes import TOKEN


def test_api_login_sets_cookie_and_rejects_bad_token(client):
    bad = client.post("/api/auth/login", json={"token": "wrong-token"})
    assert bad.status_code == 401
    assert bad.json()["detail"] == "Invalid token"

    ok = client.post("/api/auth/login", json={"token": TOKEN})
    assert ok.status_code == 200
    assert ok.json() == {"ok": True}
    assert "keo_session" in ok.cookies

    dash = client.get("/api/dashboard")
    assert dash.status_code == 200


def test_api_logout_clears_cookie(client):
    client.post("/api/auth/login", json={"token": TOKEN})
    assert client.get("/api/dashboard").status_code == 200

    res = client.post("/api/auth/logout")
    assert res.status_code == 200
    assert res.json() == {"ok": True}
    assert client.get("/api/dashboard").status_code == 401


def test_api_unauthenticated_returns_401_json(client):
    for path in (
        "/api/dashboard",
        "/api/library",
        "/api/library/organize/preview",
        "/api/library/trash",
        "/api/library/spotify/sp1",
        "/api/review/full",
        "/api/review/vid1/candidates",
        "/api/activity",
        "/api/settings",
    ):
        res = client.get(path)
        assert res.status_code == 401, path
        assert res.headers["content-type"].startswith("application/json")
        assert "detail" in res.json()


def test_api_dashboard_library_review_activity_and_settings(client, auth, library):
    library.add_review("vid1", "Sơn Tùng M-TP", "Lạc Trôi", reason="weak_match")
    library.enqueue("vid2", "Đen", "Bài Này Chill Phết", source="manual")
    library.record_event("Đen - Bài Này Chill Phết", "queued", "manual")

    dash = client.get("/api/dashboard", headers=auth)
    assert dash.status_code == 200
    d = dash.json()
    assert d["review_count"] == 1
    assert d["active_count"] == 1
    assert d["failed_count"] == 0
    assert d["nav_review_count"] == 1
    assert "attention" in d
    assert "warnings" in d

    lib = client.get("/api/library", headers=auth)
    assert lib.status_code == 200
    lj = lib.json()
    assert lj["total_count"] == 1
    assert len(lj["rows"]) == 1
    assert lj["rows"][0]["title"] == "Bài Này Chill Phết"

    preview = client.get("/api/library/organize/preview", headers=auth)
    assert preview.status_code == 200
    assert "template" in preview.json()
    assert preview.json()["moves"] == []

    trash = client.get("/api/library/trash", headers=auth)
    assert trash.status_code == 200
    assert trash.json()["items"] == []
    assert "recycle_days" in trash.json()

    rev = client.get("/api/review/full", headers=auth)
    assert rev.status_code == 200
    rj = rev.json()
    assert len(rj["review"]) == 1
    assert rj["review"][0]["video_id"] == "vid1"
    assert "watched_playlists" in rj
    assert "followed_artists" in rj
    assert "history_progress" in rj

    cand = client.get("/api/review/vid1/candidates", headers=auth)
    assert cand.status_code == 200
    cj = cand.json()
    assert cj["item"]["video_id"] == "vid1"
    assert len(cj["candidates"]) >= 1

    cand_404 = client.get("/api/review/nonexistent/candidates", headers=auth)
    assert cand_404.status_code == 404

    act = client.get("/api/activity", headers=auth)
    assert act.status_code == 200
    aj = act.json()
    assert len(aj["jobs"]) == 1
    assert len(aj["history"]) == 1

    settings = client.get("/api/settings", headers=auth)
    assert settings.status_code == 200
    sj = settings.json()
    assert "groups" in sj
    assert "auth_error" in sj
    assert "tasks" in sj
    assert "recycle_days" in sj


def test_api_library_detail_handles_spotify_and_404(client, auth, library):
    library.enqueue(
        "vid1",
        "Sơn Tùng M-TP",
        "Lạc Trôi",
        source="manual",
        spotify_id="sp1",
        spotify_url="https://open.spotify.com/track/sp1",
    )

    ok = client.get("/api/library/spotify/sp1", headers=auth)
    assert ok.status_code == 200
    body = ok.json()
    assert body["song"]["ident"] == "sp1"
    assert body["song"]["kind"] == "spotify"
    assert body["song"]["title"] == "Lạc Trôi"
    assert len(body["jobs"]) == 1
    assert body["candidates"] == []

    missing = client.get("/api/library/spotify/does-not-exist", headers=auth)
    assert missing.status_code == 404

    bad_kind = client.get("/api/library/unknown/sp1", headers=auth)
    assert bad_kind.status_code == 404


def test_spa_served_when_enabled(client, tmp_path, monkeypatch):
    (tmp_path / "index.html").write_text('<!doctype html><html><body><div id="app"></div></body></html>')
    monkeypatch.setattr(web_app, "UI_DIST_DIR", tmp_path)
    with TestClient(create_app(client.app.state.services, serve_ui=True)) as spa:
        login_res = spa.get("/login")
        assert login_res.status_code == 200
        assert '<div id="app"></div>' in login_res.text

        unauth = spa.get("/library", follow_redirects=False)
        assert unauth.status_code == 303
        assert unauth.headers["location"] == "/login"

        assert spa.post("/api/auth/login", json={"token": TOKEN}).status_code == 200
        for path in (
            "/",
            "/library",
            "/library/spotify/sp1",
            "/review/vid1/pick",
            "/settings",
        ):
            res = spa.get(path)
            assert res.status_code == 200, path
            assert '<div id="app"></div>' in res.text, path

        spa.cookies.clear()
        api_res = spa.get("/api/dashboard")
        assert api_res.status_code == 401
