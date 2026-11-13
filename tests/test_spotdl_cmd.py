import json
import subprocess
import sys

import pytest

from keo.config import (
    ALBUM_TYPES,
    AUDIO_PROVIDERS,
    BITRATES,
    FORMATS,
    LYRICS_PROVIDERS,
    RESTRICT,
    SpotdlSettings,
    apply_choice,
)
from keo.spotdl_cmd import Paths, Spotdl, build_args, parse_save_output, tail

URL = "https://open.spotify.com/track/abc123"
SONG = {
    "name": "Lạc Trôi",
    "artists": ["Sơn Tùng M-TP"],
    "artist": "Sơn Tùng M-TP",
    "album_name": "Lạc Trôi",
    "url": URL,
    "song_id": "abc123",
}


@pytest.fixture
def paths(tmp_path):
    music = tmp_path / "music"
    music.mkdir()
    data = tmp_path / "data"
    data.mkdir()
    return Paths(music, data)


def values_after(args, flag, count=1):
    index = args.index(flag)
    return args[index + 1 : index + 1 + count]


def runner(returncode=0, stdout="", on_run=None, timeout=False):
    calls = []

    def run(args, **kwargs):
        calls.append((args, kwargs))
        if timeout:
            raise subprocess.TimeoutExpired(args, kwargs["timeout"])
        if on_run is not None:
            on_run()
        return subprocess.CompletedProcess(args, returncode, stdout=stdout, stderr="")

    run.calls = calls
    return run


def test_download_args_always_keep_the_library_rules(paths):
    args = build_args(SpotdlSettings(), paths, "download", URL)
    assert args[:2] == ["download", URL]
    assert values_after(args, "--output") == [
        str(paths.music_dir / "{artists} - {title}.{output-ext}")
    ]
    assert values_after(args, "--archive") == [str(paths.archive)]
    assert values_after(args, "--overwrite") == ["skip"]
    assert values_after(args, "--detect-formats", len(FORMATS)) == list(FORMATS)
    assert values_after(args, "--threads") == ["1"]


def test_regular_account_defaults(paths):
    args = build_args(SpotdlSettings(), paths, "download", URL)
    assert values_after(args, "--format") == ["mp3"]
    assert values_after(args, "--bitrate") == ["128k"]
    assert "--cookie-file" not in args


def test_premium_account_passes_the_cookie_and_keeps_the_original_audio(paths, tmp_path):
    cookies = str(tmp_path / "cookies.txt")
    settings = SpotdlSettings(cookie_file=cookies, format="opus", bitrate="disable")
    args = build_args(settings, paths, "download", URL)
    assert values_after(args, "--cookie-file") == [cookies]
    assert values_after(args, "--format") == ["opus"]
    assert values_after(args, "--bitrate") == ["disable"]


def test_optional_flags_are_left_out_by_default(paths):
    args = build_args(SpotdlSettings(), paths, "download", URL)
    for flag in (
        "--sponsor-block", "--generate-lrc", "--skip-explicit", "--skip-album-art",
        "--proxy", "--client-id", "--client-secret", "--search-query",
        "--genius-access-token", "--yt-dlp-args", "--ffmpeg-args", "--restrict",
        "--max-filename-length", "--album-type", "--only-verified-results",
        "--dont-filter-results", "--preload",
    ):
        assert flag not in args


def test_operator_choices_are_passed(paths):
    settings = SpotdlSettings(
        sponsor_block=True,
        generate_lrc=True,
        lyrics=("synced", "genius"),
        search_query="{artist} - {title}",
        proxy="http://proxy:3128",
        restrict="ascii",
        max_filename_length=120,
        audio=("youtube-music", "youtube"),
    )
    args = build_args(settings, paths, "download", URL)
    assert "--sponsor-block" in args
    assert "--generate-lrc" in args
    assert values_after(args, "--lyrics", 2) == ["synced", "genius"]
    assert values_after(args, "--audio", 2) == ["youtube-music", "youtube"]
    assert values_after(args, "--search-query") == ["{artist} - {title}"]
    assert values_after(args, "--proxy") == ["http://proxy:3128"]
    assert values_after(args, "--restrict") == ["ascii"]
    assert values_after(args, "--max-filename-length") == ["120"]


def test_save_args_print_metadata_and_skip_library_flags(paths):
    args = build_args(SpotdlSettings(), paths, "save", "Sơn Tùng M-TP - Lạc Trôi")
    assert args[:4] == ["save", "Sơn Tùng M-TP - Lạc Trôi", "--save-file", "-"]
    assert "--archive" not in args
    assert "--output" not in args


def test_parse_save_output_reads_the_first_song_after_log_lines():
    stdout = (
        "[10:00:01] INFO     Fetching song\n"
        + json.dumps([SONG, {**SONG, "song_id": "zzz"}], indent=4, ensure_ascii=False)
        + "\n"
    )
    candidate = parse_save_output(stdout)
    assert candidate.url == URL
    assert candidate.song_id == "abc123"
    assert candidate.name == "Lạc Trôi"
    assert candidate.artists == ("Sơn Tùng M-TP",)
    assert candidate.primary_artist == "Sơn Tùng M-TP"


@pytest.mark.parametrize(
    "stdout", ["", "not json", "[]", "[10:00:01] INFO     Nothing found\n"]
)
def test_parse_save_output_without_a_song(stdout):
    assert parse_save_output(stdout) is None


def test_tail_keeps_the_last_40_lines():
    text = "\n".join(f"line {n}" for n in range(100))
    assert tail(text).splitlines() == [f"line {n}" for n in range(60, 100)]


def test_lookup_runs_spotdl_save_in_its_own_home(paths):
    run = runner(stdout=json.dumps([SONG]))
    lookup = Spotdl(SpotdlSettings(), paths, runner=run).lookup("Sơn Tùng M-TP - Lạc Trôi")
    assert lookup.candidate.song_id == "abc123"
    args, kwargs = run.calls[0]
    assert args[:3] == ["spotdl", "save", "Sơn Tùng M-TP - Lạc Trôi"]
    assert kwargs["env"]["HOME"] == str(paths.home)
    for folder in (".config/spotdl", ".spotdl"):
        config = (paths.home / folder / "config.json").read_text(encoding="utf-8")
        assert json.loads(config) == {"load_config": False}


def test_lookup_that_exits_non_zero_has_no_candidate(paths):
    run = runner(returncode=1, stdout=json.dumps([SONG]))
    assert Spotdl(SpotdlSettings(), paths, runner=run).lookup("x").candidate is None


def test_download_is_finished_when_the_archive_has_the_song(paths):
    def add_to_archive():
        paths.archive.write_text(URL + "\n", encoding="utf-8")

    spotdl = Spotdl(SpotdlSettings(), paths, runner=runner(on_run=add_to_archive))
    assert spotdl.download(URL, "abc123").ok


def test_download_exit_zero_without_an_archive_entry_is_not_finished(paths):
    spotdl = Spotdl(SpotdlSettings(), paths, runner=runner())
    assert not spotdl.download(URL, "abc123").ok


def test_download_non_zero_exit_is_not_finished(paths):
    def add_to_archive():
        paths.archive.write_text(URL + "\n", encoding="utf-8")

    spotdl = Spotdl(SpotdlSettings(), paths, runner=runner(returncode=1, on_run=add_to_archive))
    assert not spotdl.download(URL, "abc123").ok


def test_download_timeout_is_not_finished(paths):
    result = Spotdl(SpotdlSettings(), paths, runner=runner(timeout=True)).download(URL, "abc123")
    assert not result.ok
    assert "timed out" in result.output


def test_use_changes_the_next_download(paths):
    run = runner()
    spotdl = Spotdl(SpotdlSettings(), paths, runner=run)
    spotdl.use(SpotdlSettings(format="ogg"))
    spotdl.download(URL, "abc123")
    assert values_after(run.calls[0][0], "--format") == ["ogg"]


def _button_settings(base):
    settings = [base]
    for name, values in (
        ("format", FORMATS),
        ("bitrate", BITRATES),
        ("album_type", ALBUM_TYPES),
        ("restrict", RESTRICT),
    ):
        settings.extend(apply_choice(base, name, value) for value in values)
    for provider in AUDIO_PROVIDERS:
        if provider not in base.audio:
            settings.append(apply_choice(base, "audio", provider))
    for provider in LYRICS_PROVIDERS:
        settings.append(apply_choice(base, "lyrics", provider))
    for name in (
        "sponsor_block",
        "skip_album_art",
        "only_verified_results",
        "dont_filter_results",
        "skip_explicit",
        "preload",
    ):
        settings.append(apply_choice(base, name, "true"))
        settings.append(apply_choice(base, name, "false"))
    with_synced = apply_choice(base, "lyrics", "synced")
    settings.append(apply_choice(with_synced, "generate_lrc", "true"))
    return settings


def _parse_spotdl(args):
    from spotdl.utils.arguments import create_parser

    sys.argv = ["spotdl", *args]
    try:
        return create_parser().parse_args(list(args))
    except SystemExit as exc:
        raise AssertionError("spotdl rejected: " + " ".join(args)) from exc


def test_spotdl_accepts_every_settings_button(paths, tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    cookies = tmp_path / "cookies.txt"
    cookies.write_text("cookies", encoding="utf-8")
    base = SpotdlSettings(cookie_file=str(cookies))
    for settings in _button_settings(base):
        for operation in ("download", "save"):
            args = build_args(settings, paths, operation, URL)
            parsed = _parse_spotdl(args)
            assert tuple(parsed.audio_providers) == settings.audio
            assert tuple(parsed.lyrics_providers) == settings.lyrics
            if "--cookie-file" in args:
                assert parsed.cookie_file == settings.cookie_file
            else:
                assert parsed.cookie_file is None
            if operation == "download":
                assert parsed.format == settings.format
                assert parsed.bitrate == settings.bitrate
                assert parsed.album_type == (settings.album_type or None)
                assert parsed.restrict == (settings.restrict or None)
                assert parsed.sponsor_block is (True if settings.sponsor_block else None)
                assert parsed.generate_lrc is (True if settings.generate_lrc else None)
                assert parsed.skip_album_art is (True if settings.skip_album_art else None)
                assert parsed.only_verified_results is (
                    True if settings.only_verified_results else None
                )
                assert parsed.filter_results is (False if settings.dont_filter_results else None)
                assert parsed.skip_explicit is (True if settings.skip_explicit else None)
                assert "--preload" not in args
            else:
                assert parsed.preload is (True if settings.preload else None)
                assert "--format" not in args
                assert "--bitrate" not in args


def test_a_free_format_omits_the_cookie_file(paths, tmp_path):
    cookies = str(tmp_path / "cookies.txt")
    settings = SpotdlSettings(cookie_file=cookies, format="mp3", bitrate="128k")
    args = build_args(settings, paths, "download", URL)
    assert "--cookie-file" not in args


def test_bitrate_disable_passes_the_cookie_file(paths, tmp_path):
    cookies = str(tmp_path / "cookies.txt")
    settings = SpotdlSettings(cookie_file=cookies, format="mp3", bitrate="disable")
    args = build_args(settings, paths, "download", URL)
    assert values_after(args, "--cookie-file") == [cookies]


def test_download_pairs_youtube_music_url_with_spotify_url(paths, tmp_path):
    seen = []

    def runner(args, **kwargs):
        seen.append(args)
        paths.archive.write_text(URL + "\n", encoding="utf-8")
        return subprocess.CompletedProcess(args, 0, stdout="ok", stderr="")

    spotdl = Spotdl(SpotdlSettings(), paths, runner=runner)
    res = spotdl.download(URL, None, video_id="vid1")
    assert res.ok is True
    assert f"https://music.youtube.com/watch?v=vid1|{URL}" in seen[0]
