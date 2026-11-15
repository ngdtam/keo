from fastapi.testclient import TestClient

from keo.config import SpotdlSettings
from keo.web import Services, create_app
from tests.fakes import TOKEN

COMPLETE = '{"cookie": "SID=x", "x-goog-authuser": "0", "authorization": "SAPISIDHASH present"}'
GAP = '{"Cookie": "SID=x", "Authorization": "SAPISIDHASH present"}'


def test_dashboard_requires_session(client):
    assert client.get("/api/dashboard").status_code == 401


def test_dashboard_shows_zero_counts(logged_in):
    data = logged_in.get("/api/dashboard").json()
    assert data["review_count"] == 0
    assert data["library_count"] == 0
    assert data["missing_count"] == 0
    assert data["active_count"] == 0
    assert data["failed_count"] == 0
    assert data["nav_active_jobs_count"] == 0


def test_dashboard_counts_review_and_jobs(logged_in, library):
    library.add_review("vid1", "Sơn Tùng M-TP", "Lạc Trôi", reason="weak_match")
    library.enqueue("vid2", "Đen", "Queued", source="manual")
    failed = library.enqueue("vid3", "Đen", "Failed", source="manual")
    library.finish(failed.id, ok=False, error="boom")
    data = logged_in.get("/api/dashboard").json()
    assert data["review_count"] == 1
    assert data["active_count"] == 1
    assert data["nav_active_jobs_count"] == 1
    assert data["failed_count"] == 1


def _open(tmp_path, catalog, library, worker, spotdl, auth_text, settings=None):
    auth = tmp_path / "browser.json"
    auth.write_text(auth_text, encoding="utf-8")
    if settings is not None:
        spotdl.settings = settings
    services = Services(
        token=TOKEN,
        catalog=catalog,
        library=library,
        worker=worker,
        spotdl=spotdl,
        cookies=worker.cookies,
        run_later=lambda fn: fn(),
        ytm_auth=auth,
    )
    client = TestClient(create_app(services))
    client.post("/api/auth/login", json={"token": TOKEN})
    return client


def test_missing_cookie_alone_hides_the_warning(tmp_path, catalog, library, worker, spotdl):
    client = _open(tmp_path, catalog, library, worker, spotdl, COMPLETE)
    data = client.get("/api/dashboard").json()
    assert data["gaps"] == []
    assert data["warnings"] == []
    client.close()


def test_auth_gap_shows_the_settings_sentence(tmp_path, catalog, library, worker, spotdl):
    client = _open(tmp_path, catalog, library, worker, spotdl, GAP)
    data = client.get("/api/dashboard").json()
    assert "x-goog-authuser" in data["gaps"]
    client.close()


def test_premium_format_without_a_cookie_shows_the_warning(tmp_path, catalog, library, worker, spotdl):
    client = _open(
        tmp_path, catalog, library, worker, spotdl, COMPLETE, SpotdlSettings(format="opus")
    )
    data = client.get("/api/dashboard").json()
    assert any("format is opus, which needs a cookie file" in w for w in data["warnings"])
    client.close()
