import re

import pytest
from fastapi.testclient import TestClient

from keo.config import SpotdlSettings
from keo.cookies import ProbeResult
from keo.web import Services, create_app
from tests.fakes import TOKEN


def button(html, name, value):
    for tag in re.findall(r"<button\b[^>]*>", html):
        if f'name="{name}"' in tag and f'value="{value}"' in tag:
            return tag
    return ""


@pytest.fixture
def config_file(tmp_path):
    path = tmp_path / "keo.toml"
    path.write_text('# keep this\nformat = "mp3"\nbitrate = "128k"\n', encoding="utf-8")
    return path


@pytest.fixture
def auth_file(tmp_path):
    path = tmp_path / "browser.json"
    path.write_text(
        '{"Cookie": "SID=do-not-show", "Authorization": "SAPISIDHASH present"}',
        encoding="utf-8",
    )
    return path


@pytest.fixture
def page(catalog, library, worker, spotdl, config_file, auth_file, cookies):
    spotdl.settings = SpotdlSettings()
    services = Services(
        token=TOKEN,
        catalog=catalog,
        library=library,
        worker=worker,
        spotdl=spotdl,
        cookies=cookies,
        run_later=lambda fn: fn(),
        config_path=config_file,
        ytm_auth=auth_file,
        data_dir=config_file.parent,
    )
    with TestClient(create_app(services)) as client:
        client.post("/login", data={"token": TOKEN})
        yield client


def test_settings_without_a_session_redirects_to_login(client):
    response = client.get("/settings", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/login"


def test_settings_page_names_a_missing_header_and_hides_its_value(page):
    response = page.get("/settings")
    assert response.status_code == 200
    assert "x-goog-authuser" in response.text
    assert "SID=do-not-show" not in response.text
    assert "Cookie file missing" in response.text
    assert "disabled" in button(response.text, "format", "opus")
    assert 'class="active btn"' in button(response.text, "format", "mp3")
    assert 'aria-pressed="true"' in button(response.text, "format", "mp3")
    assert 'href="/settings"' in response.text


def test_premium_tap_without_a_cookie_file_changes_nothing(page, config_file, spotdl):
    response = page.post("/settings", data={"format": "opus"})
    assert response.status_code == 400
    assert "cookie" in response.text
    assert config_file.read_text(encoding="utf-8") == '# keep this\nformat = "mp3"\nbitrate = "128k"\n'
    assert spotdl.settings.format == "mp3"


def test_a_normal_tap_saves_the_line_and_the_next_download(page, config_file, spotdl):
    response = page.post("/settings", data={"format": "ogg"}, follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/settings"
    assert config_file.read_text(encoding="utf-8") == (
        '# keep this\nformat = "ogg"\nbitrate = "128k"\n'
    )
    assert spotdl.settings.format == "ogg"


def test_current_premium_choice_without_a_cookie_file_is_shown(page, spotdl):
    spotdl.settings = SpotdlSettings(format="opus")
    response = page.get("/settings")
    assert "format is opus, which needs a cookie file" in response.text


def test_cookie_upload_writes_the_file_and_hides_the_bytes(page, config_file, spotdl, probe):
    probe.results.append(ProbeResult([{"format_id": "141"}], ""))
    response = page.post(
        "/settings/cookie",
        files={"cookie": ("cookies.txt", b"cookie-marker", "text/plain")},
        follow_redirects=False,
    )
    assert response.status_code == 303
    stored = config_file.parent / "cookies.txt"
    assert stored.read_bytes() == b"cookie-marker"
    assert f'cookie_file = "{stored}"' in config_file.read_text(encoding="utf-8")
    assert spotdl.settings.cookie_file == str(stored)
    html = page.get("/settings").text
    assert "Premium" in html
    assert "cookie-marker" not in html
    assert probe.calls == [str(stored)]


def test_empty_or_huge_cookie_upload_changes_nothing(page, config_file, probe):
    before = config_file.read_text(encoding="utf-8")
    empty = page.post("/settings/cookie", files={"cookie": ("cookies.txt", b"  \n", "text/plain")})
    assert empty.status_code == 400
    assert "The file is empty." in empty.text
    huge = page.post(
        "/settings/cookie",
        files={"cookie": ("cookies.txt", b"x" * 1048577, "text/plain")},
    )
    assert huge.status_code == 400
    assert "The file is too large." in huge.text
    assert config_file.read_text(encoding="utf-8") == before
    assert not (config_file.parent / "cookies.txt").exists()
    assert probe.calls == []


def test_opus_is_saved_when_the_cookie_file_exists(page, config_file, spotdl, tmp_path):
    cookies = tmp_path / "cookies.txt"
    cookies.write_text("cookies", encoding="utf-8")
    spotdl.settings = SpotdlSettings(cookie_file=str(cookies))
    response = page.post("/settings", data={"format": "opus"}, follow_redirects=False)
    assert response.status_code == 303
    assert 'format = "opus"' in config_file.read_text(encoding="utf-8")
    assert "# keep this" in config_file.read_text(encoding="utf-8")
    assert spotdl.settings.format == "opus"
    assert "Cookie file found" in page.get("/settings").text


def test_check_cookie_without_a_file_does_not_probe(page, probe):
    response = page.post("/settings/cookie/check", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/settings"
    assert "Cookie file missing" in page.get("/settings").text
    assert probe.calls == []


def test_check_cookie_stores_a_free_verdict(page, config_file, spotdl, probe, tmp_path):
    path = tmp_path / "cookies.txt"
    path.write_text("a", encoding="utf-8")
    spotdl.settings = SpotdlSettings(cookie_file=str(path))
    probe.results.append(ProbeResult([{"format_id": "251"}], ""))
    response = page.post("/settings/cookie/check", follow_redirects=False)
    assert response.status_code == 303
    assert "Account is not Premium" in page.get("/settings").text
    assert probe.calls == [str(path)]


def test_a_failed_check_keeps_the_failed_line(page, spotdl, probe, tmp_path):
    path = tmp_path / "cookies.txt"
    path.write_text("a", encoding="utf-8")
    spotdl.settings = SpotdlSettings(format="opus", cookie_file=str(path))
    probe.results.append(ProbeResult(None, "timed out"))
    page.post("/settings/cookie/check", follow_redirects=True)
    assert "Cookie check failed" in page.get("/settings").text
    assert "format is opus, which needs a cookie file" not in page.get("/settings").text


def test_settings_saves_recycle_days(page, config_file):
    resp = page.post("/settings", data={"recycle_days": "30"}, follow_redirects=False)
    assert resp.status_code == 303
    assert "recycle_days = 30" in config_file.read_text(encoding="utf-8")
    assert page.app.state.services.recycle_days == 30
