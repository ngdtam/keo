import json

import pytest

from keo import main


def test_build_app_exits_and_names_the_setting(monkeypatch):
    monkeypatch.delenv("KEO_MUSIC_DIR", raising=False)
    with pytest.raises(SystemExit, match="KEO_MUSIC_DIR"):
        main.build_app()


def test_build_app_boots_without_token_or_ytm_auth(monkeypatch, tmp_path):
    music = tmp_path / "music"
    music.mkdir()
    monkeypatch.delenv("KEO_TOKEN", raising=False)
    monkeypatch.delenv("KEO_YTM_AUTH", raising=False)
    monkeypatch.setenv("KEO_MUSIC_DIR", str(music))
    monkeypatch.setenv("KEO_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("KEO_CONFIG", str(tmp_path / "keo.toml"))
    app = main.build_app()
    assert app.state.services.token == ""
    assert app.state.services.ytm_auth is None
    assert app.state.services.catalog.is_configured() is False


def test_build_app_wires_the_services(monkeypatch, tmp_path):
    music = tmp_path / "music"
    music.mkdir()
    auth = tmp_path / "browser.json"
    auth.write_text(json.dumps({"cookie": "SID=x"}), encoding="utf-8")
    monkeypatch.setenv("KEO_TOKEN", "secret")
    monkeypatch.setenv("KEO_MUSIC_DIR", str(music))
    monkeypatch.setenv("KEO_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("KEO_YTM_AUTH", str(auth))
    monkeypatch.setenv("KEO_CONFIG", str(tmp_path / "keo.toml"))
    monkeypatch.setattr(main, "open_ytmusic", lambda *args: object())
    app = main.build_app()
    assert app.state.services.token == "secret"
    assert (tmp_path / "data" / "keo.db").exists()
    assert app.state.services.cookies is app.state.services.worker.cookies
