import json
from pathlib import Path

import pytest

from keo.config import ConfigError, SpotdlSettings, load_settings

REPO = Path(__file__).resolve().parents[1]


@pytest.fixture
def env(tmp_path):
    music = tmp_path / "music"
    music.mkdir()
    auth = tmp_path / "browser.json"
    auth.write_text(json.dumps({"cookie": "SID=x"}), encoding="utf-8")
    return {
        "KEO_TOKEN": "secret",
        "KEO_MUSIC_DIR": str(music),
        "KEO_DATA_DIR": str(tmp_path / "data"),
        "KEO_YTM_AUTH": str(auth),
        "KEO_CONFIG": str(tmp_path / "keo.toml"),
    }


def write_config(env, text):
    Path(env["KEO_CONFIG"]).write_text(text, encoding="utf-8")


def test_defaults_suit_a_regular_account(env):
    settings = load_settings(env)
    assert settings.spotdl == SpotdlSettings()
    assert settings.spotdl.format == "mp3"
    assert settings.spotdl.bitrate == "128k"
    assert settings.spotdl.cookie_file == ""
    assert settings.history_timezone == "Asia/Ho_Chi_Minh"
    assert settings.history_hour == 22
    assert settings.data_dir == Path(env["KEO_DATA_DIR"])


def test_example_file_matches_the_defaults(env):
    env["KEO_CONFIG"] = str(REPO / "keo.example.toml")
    settings = load_settings(env)
    assert settings.spotdl == SpotdlSettings()
    assert (settings.history_timezone, settings.history_hour) == ("Asia/Ho_Chi_Minh", 22)


def test_premium_settings_load(env, tmp_path):
    cookies = tmp_path / "cookies.txt"
    cookies.write_text("# Netscape HTTP Cookie File\n", encoding="utf-8")
    write_config(env, f'cookie_file = "{cookies}"\nformat = "opus"\nbitrate = "disable"\n')
    spotdl = load_settings(env).spotdl
    assert (spotdl.cookie_file, spotdl.format, spotdl.bitrate) == (str(cookies), "opus", "disable")


@pytest.mark.parametrize("name", ["KEO_TOKEN", "KEO_MUSIC_DIR", "KEO_YTM_AUTH"])
def test_missing_required_variable_is_named(env, name):
    del env[name]
    with pytest.raises(ConfigError, match=name):
        load_settings(env)


def test_music_dir_must_exist(env, tmp_path):
    env["KEO_MUSIC_DIR"] = str(tmp_path / "nope")
    with pytest.raises(ConfigError, match="KEO_MUSIC_DIR"):
        load_settings(env)


def test_ytm_auth_file_must_exist(env, tmp_path):
    env["KEO_YTM_AUTH"] = str(tmp_path / "missing.json")
    with pytest.raises(ConfigError, match="KEO_YTM_AUTH"):
        load_settings(env)


def test_oauth_file_needs_client_credentials(env):
    Path(env["KEO_YTM_AUTH"]).write_text(
        json.dumps({"access_token": "a", "refresh_token": "r"}), encoding="utf-8"
    )
    with pytest.raises(ConfigError, match="KEO_YTM_CLIENT_ID"):
        load_settings(env)
    env["KEO_YTM_CLIENT_ID"] = "client"
    env["KEO_YTM_CLIENT_SECRET"] = "shh"
    settings = load_settings(env)
    assert (settings.ytm_client_id, settings.ytm_client_secret) == ("client", "shh")


def test_tz_overrides_the_file_and_rejects_an_unknown_zone(env):
    write_config(env, 'history_timezone = "Europe/Paris"\n')
    env["TZ"] = "Asia/Tokyo"
    assert load_settings(env).history_timezone == "Asia/Tokyo"
    env["TZ"] = "Mars/Base"
    with pytest.raises(ConfigError, match="TZ"):
        load_settings(env)


def test_file_timezone_is_ignored(env):
    write_config(env, 'history_timezone = "Mars/Base"\n')
    assert load_settings(env).history_timezone == "Asia/Ho_Chi_Minh"


@pytest.mark.parametrize(
    ("text", "setting"),
    [
        ("generate_lrc = true\n", "generate_lrc"),
        ('cookie_file = "/missing/cookies.txt"\n', "cookie_file"),
        ('formatt = "mp3"\n', "formatt"),
        ('format = "aac"\n', "format"),
        ('bitrate = "999k"\n', "bitrate"),
        ('audio = ["napster"]\n', "audio"),
        ("history_hour = 24\n", "history_hour"),
        ('sponsor_block = "yes"\n', "sponsor_block"),
    ],
)
def test_bad_setting_is_named(env, text, setting):
    write_config(env, text)
    with pytest.raises(ConfigError, match=setting):
        load_settings(env)


def test_generate_lrc_with_synced_lyrics_is_accepted(env):
    write_config(env, 'generate_lrc = true\nlyrics = ["synced", "genius"]\n')
    assert load_settings(env).spotdl.generate_lrc is True


def test_invalid_toml_is_rejected(env):
    write_config(env, "format = \n")
    with pytest.raises(ConfigError, match="keo.toml is invalid"):
        load_settings(env)


def test_recycle_days_default_and_validation(env):
    assert load_settings(env).recycle_days == 7
    write_config(env, "recycle_days = 14\n")
    assert load_settings(env).recycle_days == 14
    write_config(env, "recycle_days = -1\n")
    with pytest.raises(ConfigError, match="recycle_days"):
        load_settings(env)
