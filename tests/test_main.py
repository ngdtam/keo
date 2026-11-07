import json

import pytest

from keo import main


def test_build_app_exits_and_names_the_setting(monkeypatch):
    monkeypatch.delenv("KEO_TOKEN", raising=False)
    with pytest.raises(SystemExit, match="KEO_TOKEN"):
        main.build_app()


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
