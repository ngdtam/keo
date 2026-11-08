import json
from dataclasses import replace

import pytest

from keo.config import ConfigError, SpotdlSettings, apply_choice, auth_gaps, save_choice


def test_browser_auth_names_a_missing_header(tmp_path):
    path = tmp_path / "browser.json"
    path.write_text(
        json.dumps({"Cookie": "SID=present", "Authorization": "SAPISIDHASH present"}),
        encoding="utf-8",
    )
    assert auth_gaps(path) == ("x-goog-authuser",)


def test_browser_auth_with_the_three_headers_is_complete(tmp_path):
    path = tmp_path / "browser.json"
    path.write_text(
        json.dumps(
            {
                "cookie": "SID=present",
                "X-Goog-AuthUser": "0",
                "authorization": "SAPISIDHASH present",
            }
        ),
        encoding="utf-8",
    )
    assert auth_gaps(path) == ()


def test_blank_browser_header_counts_as_missing(tmp_path):
    path = tmp_path / "browser.json"
    path.write_text(
        json.dumps({"cookie": "  ", "x-goog-authuser": "0", "authorization": "SAPISIDHASH present"}),
        encoding="utf-8",
    )
    assert auth_gaps(path) == ("cookie",)


def test_oauth_auth_names_missing_token_fields_and_client(tmp_path):
    path = tmp_path / "oauth.json"
    path.write_text(json.dumps({"refresh_token": "r", "access_token": ""}), encoding="utf-8")
    assert auth_gaps(path) == (
        "access_token",
        "token_type",
        "KEO_YTM_CLIENT_ID",
        "KEO_YTM_CLIENT_SECRET",
    )


def test_oauth_auth_is_complete_when_the_client_is_set(tmp_path):
    path = tmp_path / "oauth.json"
    path.write_text(
        json.dumps({"refresh_token": "r", "access_token": "a", "token_type": "Bearer"}),
        encoding="utf-8",
    )
    assert auth_gaps(path, client_id="id", client_secret="secret") == ()


def test_unreadable_auth_file_is_named(tmp_path):
    path = tmp_path / "browser.json"
    path.write_text("{", encoding="utf-8")
    with pytest.raises(ConfigError, match="KEO_YTM_AUTH"):
        auth_gaps(path)


def test_opus_without_a_cookie_file_is_refused():
    with pytest.raises(ConfigError, match="cookie"):
        apply_choice(SpotdlSettings(), "format", "opus")


def test_m4a_and_disable_need_a_cookie_file_that_exists(tmp_path):
    missing = replace(SpotdlSettings(), cookie_file=str(tmp_path / "nope.txt"))
    with pytest.raises(ConfigError, match="cookie"):
        apply_choice(missing, "format", "m4a")
    cookies = tmp_path / "cookies.txt"
    cookies.write_text("cookies", encoding="utf-8")
    ready = replace(SpotdlSettings(), cookie_file=str(cookies))
    assert apply_choice(ready, "bitrate", "disable").bitrate == "disable"


def test_a_normal_format_works_without_a_cookie_file():
    assert apply_choice(SpotdlSettings(), "format", "ogg").format == "ogg"


def test_the_last_audio_source_and_lyrics_source_stay():
    with pytest.raises(ConfigError, match="audio"):
        apply_choice(SpotdlSettings(), "audio", "youtube-music")
    with pytest.raises(ConfigError, match="lyrics"):
        apply_choice(SpotdlSettings(lyrics=("genius",)), "lyrics", "genius")


def test_an_audio_source_can_be_added_and_removed():
    added = apply_choice(SpotdlSettings(), "audio", "youtube")
    assert added.audio == ("youtube-music", "youtube")
    assert apply_choice(added, "audio", "youtube").audio == ("youtube-music",)


def test_synced_lrc_needs_synced_lyrics():
    with pytest.raises(ConfigError, match="synced"):
        apply_choice(SpotdlSettings(), "generate_lrc", "true")
    ready = apply_choice(SpotdlSettings(), "lyrics", "synced")
    assert apply_choice(ready, "generate_lrc", "true").generate_lrc is True


def test_removing_synced_while_lrc_is_on_is_refused():
    settings = replace(SpotdlSettings(), lyrics=("synced", "genius"), generate_lrc=True)
    with pytest.raises(ConfigError, match="synced"):
        apply_choice(settings, "lyrics", "synced")


def test_secret_settings_are_not_buttons():
    with pytest.raises(ConfigError, match="client_secret"):
        apply_choice(SpotdlSettings(), "client_secret", "nope")


def test_save_choice_edits_one_line_and_keeps_comments(tmp_path):
    path = tmp_path / "keo.toml"
    path.write_text('# keep this\nformat = "mp3"\n# format = "opus"\nbitrate = "128k"\n', encoding="utf-8")
    updated = save_choice(path, SpotdlSettings(), "format", "ogg")
    assert updated.format == "ogg"
    assert path.read_text(encoding="utf-8") == (
        '# keep this\nformat = "ogg"\n# format = "opus"\nbitrate = "128k"\n'
    )


def test_save_choice_appends_a_missing_line(tmp_path):
    path = tmp_path / "keo.toml"
    path.write_text("# only a comment\n", encoding="utf-8")
    save_choice(path, SpotdlSettings(), "sponsor_block", "true")
    assert path.read_text(encoding="utf-8") == "# only a comment\nsponsor_block = true\n"


def test_refused_choice_does_not_touch_the_file(tmp_path):
    path = tmp_path / "keo.toml"
    original = 'format = "mp3"\n'
    path.write_text(original, encoding="utf-8")
    with pytest.raises(ConfigError):
        save_choice(path, SpotdlSettings(), "format", "opus")
    assert path.read_text(encoding="utf-8") == original
