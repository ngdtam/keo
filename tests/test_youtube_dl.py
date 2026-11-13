import subprocess
from pathlib import Path

import pytest

from keo.youtube_dl import (
    DownloadResult,
    build_yt_dlp_args,
    download_youtube_audio,
    sanitize_filename,
    write_tags,
)


def test_sanitize_filename():
    assert sanitize_filename("Artist / Name: Track?") == "Artist _ Name_ Track_"
    assert sanitize_filename('A<B>C"D|E') == "A_B_C_D_E"


def test_build_yt_dlp_args_default(tmp_path):
    music_dir = tmp_path / "music"
    args = build_yt_dlp_args("abc1234", "Low G", "An Thần", music_dir, audio_format="mp3")
    assert "yt-dlp" in args[0]
    assert "--extract-audio" in args
    assert "--audio-format" in args
    assert "mp3" in args
    assert str(music_dir / "Low G - An Thần.%(ext)s") in args
    assert "https://www.youtube.com/watch?v=abc1234" == args[-1]
    assert "--cookies" not in args


def test_build_yt_dlp_args_with_cookies(tmp_path):
    music_dir = tmp_path / "music"
    cookie_file = tmp_path / "cookies.txt"
    cookie_file.write_text("# Netscape HTTP Cookie File", encoding="utf-8")
    args = build_yt_dlp_args(
        "xyz789", "Artist", "Title", music_dir, audio_format="opus", cookie_file=str(cookie_file)
    )
    assert "--audio-format" in args
    assert "opus" in args
    assert "--cookies" in args
    assert str(cookie_file) in args


def test_download_youtube_audio_success(tmp_path):
    music_dir = tmp_path / "music"
    calls = []

    def fake_runner(args, **kwargs):
        calls.append((args, kwargs))
        return subprocess.CompletedProcess(args, 0, stdout="[ExtractAudio] Destination: song.mp3", stderr="")

    res = download_youtube_audio("abc123", "Artist", "Song", music_dir, runner=fake_runner)
    assert res.ok is True
    assert "[ExtractAudio]" in res.output
    assert len(calls) == 1


def test_download_youtube_audio_failure(tmp_path):
    music_dir = tmp_path / "music"

    def fake_runner(args, **kwargs):
        return subprocess.CompletedProcess(args, 1, stdout="", stderr="ERROR: Video unavailable")

    res = download_youtube_audio("abc123", "Artist", "Song", music_dir, runner=fake_runner)
    assert res.ok is False
    assert "Video unavailable" in res.output


def test_download_youtube_audio_timeout(tmp_path):
    music_dir = tmp_path / "music"

    def fake_runner(args, **kwargs):
        raise subprocess.TimeoutExpired(args, 10)

    res = download_youtube_audio("abc123", "Artist", "Song", music_dir, runner=fake_runner)
    assert res.ok is False
    assert "timed out" in res.output


def test_build_yt_dlp_args_uses_output_template_and_max_length(tmp_path):
    music_dir = tmp_path / "music"
    args = build_yt_dlp_args(
        "abc1234",
        "Low G",
        "An Thần",
        music_dir,
        audio_format="mp3",
        output_template="{title} [{artists}].{output-ext}",
        max_filename_length=12,
    )
    out_arg = args[args.index("-o") + 1]
    assert out_arg.startswith(str(music_dir) + "/")
    assert out_arg.endswith(".%(ext)s")
    stem = Path(out_arg).stem
    assert stem == "An Thần [Low"


def test_write_tags_sets_artist_and_title_and_ignores_bad_files(tmp_path, monkeypatch):
    bad = tmp_path / "bad.mp3"
    bad.write_bytes(b"not-audio")
    write_tags(bad, artist="Đen", title="Lối Nhỏ")  # must not raise

    fake_tags = {}

    class FakeAudio:
        tags = fake_tags
        def save(self):
            fake_tags["_saved"] = True

    import mutagen
    monkeypatch.setattr(mutagen, "File", lambda p, easy=True: FakeAudio())
    write_tags(bad, artist="Đen", title="Lối Nhỏ")
    assert fake_tags["artist"] == ["Đen"]
    assert fake_tags["title"] == ["Lối Nhỏ"]
    assert fake_tags["_saved"] is True


def test_build_yt_dlp_args_supports_artist_subfolder_template(tmp_path):
    music_dir = tmp_path / "music"
    args = build_yt_dlp_args(
        "abc1234",
        "Low G",
        "An Thần",
        music_dir,
        audio_format="mp3",
        output_template="{artist}/{title}.{output-ext}",
    )
    out_arg = args[args.index("-o") + 1]
    assert out_arg == str(music_dir / "Low G" / "An Thần.%(ext)s")

