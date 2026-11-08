from fastapi.testclient import TestClient

from keo.config import SpotdlSettings
from keo.web import Services, create_app
from tests.fakes import TOKEN

COMPLETE = '{"cookie": "SID=x", "x-goog-authuser": "0", "authorization": "SAPISIDHASH present"}'
GAP = '{"Cookie": "SID=x", "Authorization": "SAPISIDHASH present"}'


def test_home_and_search_without_a_session_redirect_to_login(client):
    for path in ("/", "/search"):
        response = client.get(path, follow_redirects=False)
        assert response.status_code == 303
        assert response.headers["location"] == "/login"


def test_login_has_no_navbar(client):
    assert "navbar-nav" not in client.get("/login").text


def test_blank_query_stays_on_the_dashboard(logged_in):
    for params in ({"q": ""}, {"q": " "}):
        response = logged_in.get("/", params=params)
        assert response.status_code == 200
        assert "0 waiting" in response.text


def test_search_query_on_home_redirects(logged_in):
    response = logged_in.get("/", params={"q": " lac "}, follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/search?q=lac"


def test_dashboard_shows_zero_counts(logged_in):
    text = logged_in.get("/").text
    assert "0 waiting" in text
    assert "0 queued or running" in text
    assert "0 failed" in text
    assert 'href="/review"' in text
    assert 'href="/jobs"' in text


def test_dashboard_counts_review_and_jobs(logged_in, library):
    library.add_review("vid1", "Sơn Tùng M-TP", "Lạc Trôi", reason="weak_match")
    library.enqueue("vid2", "Đen", "Queued", source="manual")
    failed = library.enqueue("vid3", "Đen", "Failed", source="manual")
    library.finish(failed.id, ok=False, error="boom")
    text = logged_in.get("/").text
    assert "1 waiting" in text
    assert "1 queued or running" in text
    assert "1 failed" in text


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
        run_later=lambda fn: fn(),
        ytm_auth=auth,
    )
    client = TestClient(create_app(services))
    client.post("/login", data={"token": TOKEN})
    return client


def test_missing_cookie_alone_hides_the_warning(tmp_path, catalog, library, worker, spotdl):
    client = _open(tmp_path, catalog, library, worker, spotdl, COMPLETE)
    text = client.get("/").text
    assert "Cookie file missing" not in text
    assert 'role="alert"' not in text
    client.close()


def test_auth_gap_shows_the_settings_sentence(tmp_path, catalog, library, worker, spotdl):
    client = _open(tmp_path, catalog, library, worker, spotdl, GAP)
    text = client.get("/").text
    assert "YouTube Music auth file is missing x-goog-authuser." in text
    client.close()


def test_premium_format_without_a_cookie_shows_the_warning(tmp_path, catalog, library, worker, spotdl):
    client = _open(
        tmp_path, catalog, library, worker, spotdl, COMPLETE, SpotdlSettings(format="opus")
    )
    text = client.get("/").text
    assert "format is opus, which needs a cookie file" in text
    client.close()
