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


def test_settings_integrations_saves_urls(page, config_file):
    resp = page.post(
        "/settings/integrations",
        data={
            "notify_url": "https://ntfy.sh/keo-alerts",
            "media_refresh_url": "http://localhost:4533/rest/startScan",
        },
        follow_redirects=False,
    )
    assert resp.status_code == 303
    assert page.app.state.services.notify_url == "https://ntfy.sh/keo-alerts"
    assert page.app.state.services.media_refresh_url == "http://localhost:4533/rest/startScan"
    text = config_file.read_text(encoding="utf-8")
    assert "https://ntfy.sh/keo-alerts" in text
    assert "http://localhost:4533/rest/startScan" in text


def test_settings_shows_system_health_and_runs_backup(page, tmp_path):
    res = page.get("/settings")
    assert res.status_code == 200
    assert "System" in res.text
    assert "yt-dlp" in res.text
    assert "Run backup now" in res.text

    backup_res = page.post("/settings/backup", follow_redirects=False)
    assert backup_res.status_code == 303
    backups = list((page.app.state.services.data_dir / "backups").iterdir())
    assert len(backups) == 1
    assert (backups[0] / "keo.db").is_file()


def test_settings_check_updates_renders_latest_versions(page, monkeypatch):
    monkeypatch.setattr(
        "keo.web.routes_settings.check_package_updates",
        lambda: {
            "yt-dlp": ("2026.10.21", "2026.11.10", True),
            "ytmusicapi": ("1.12.4", "1.12.4", False),
            "spotdl": ("4.6.0", "4.6.0", False),
            "pymediainfo": ("7.0.1", "7.0.2", True),
        },
    )
    resp = page.post("/settings/check-updates")
    assert resp.status_code == 200
    assert "yt-dlp:" in resp.text and "latest 2026.11.10" in resp.text
    assert "ytmusicapi:" in resp.text and "up to date" in resp.text
    assert "spotdl:" in resp.text and "up to date" in resp.text
    assert "mediainfo (pymediainfo):" in resp.text and "latest 7.0.2" in resp.text


def test_settings_shows_scheduled_tasks_and_runs_one(page, library):
    res = page.get("/settings")
    assert res.status_code == 200
    assert "Scheduled tasks" in res.text
    assert "housekeeping" in res.text
    assert "backup" in res.text

    run_res = page.post("/settings/tasks/housekeeping/run", follow_redirects=False)
    assert run_res.status_code == 303
    tasks = {t.name: t for t in library.list_tasks()}
    assert tasks["housekeeping"].last_run is not None
