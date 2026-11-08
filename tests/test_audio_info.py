import sys
from types import SimpleNamespace

from keo import audio_info
from keo.audio_info import load, reset_cache, view_track


def track(**kwargs):
    return SimpleNamespace(**kwargs)


def test_opus_card_and_bitrate_row():
    view = view_track(track(format="Opus", bit_rate=251000, bit_rate_mode="VBR"))
    assert view.card == "Opus · 251 kbps"
    assert ("Bitrate", "251 kbps VBR") in view.rows


def test_mp3_profile_and_missing_bitrate_or_format():
    mp3 = view_track(track(format="MPEG Audio", format_profile="Layer 3", bit_rate="128000"))
    assert mp3.card == "MP3 · 128 kbps"
    assert ("Format", "MP3 Layer 3") in mp3.rows

    codec_only = view_track(track(format="FLAC"))
    assert codec_only.card == "FLAC"
    assert ("Format", "FLAC") in codec_only.rows
    assert not any(label == "Bitrate" for label, _ in codec_only.rows)

    assert view_track(track(bit_rate=251000)).card is None
    assert view_track(track()) is None


def test_rates_channels_depth_and_duration():
    view = view_track(track(
        format="Opus",
        sampling_rate=44100,
        channel_s=2,
        channel_layout="Stereo",
        duration=222000,
    ))
    assert ("Sample rate", "44.1 kHz") in view.rows
    assert ("Channels", "2 · Stereo") in view.rows
    assert not any(label == "Bit depth" for label, _ in view.rows)
    assert ("Duration", "3:42") in view.rows

    assert ("Sample rate", "48 kHz") in view_track(track(format="Opus", sampling_rate=48000)).rows
    assert ("Sample rate", "22.1 kHz") in view_track(track(format="Opus", sampling_rate=22050)).rows
    assert ("Channels", "2") in view_track(track(format="Opus", channel_s="2")).rows
    flac = view_track(track(format="FLAC", bit_depth=16, duration=3785000))
    assert ("Bit depth", "16 bit") in flac.rows
    assert ("Duration", "1:03:05") in flac.rows
    assert ("Duration", "0:01") in view_track(track(format="Opus", duration=500)).rows
    assert ("Duration", "0:05") in view_track(track(format="Opus", duration="4500")).rows


def test_overall_bit_rate_fills_a_missing_audio_bitrate(tmp_path, monkeypatch):
    reset_cache()
    path = tmp_path / "song.opus"
    path.write_bytes(b"song")
    general = track(track_type="General", overall_bit_rate=267090)
    audio = track(
        track_type="Audio", format="Opus", sampling_rate=48000,
        channel_s=2, channel_layout="L R", duration=281528,
    )

    class Media:
        tracks = [general, audio]

    module = type(sys)("pymediainfo")
    module.MediaInfo = track(parse=lambda _path: Media())
    monkeypatch.setitem(sys.modules, "pymediainfo", module)
    view = load(path)
    assert view.card == "Opus · 267 kbps"
    assert ("Bitrate", "267 kbps") in view.rows
    assert ("Channels", "2 · L R") in view.rows
    assert ("Sample rate", "48 kHz") in view.rows
    kept = view_track(track(format="Opus", bit_rate=251000, overall_bit_rate=999000))
    assert kept.card == "Opus · 251 kbps"


def test_import_error_is_not_cached(tmp_path, monkeypatch):
    reset_cache()
    path = tmp_path / "song.opus"
    path.write_bytes(b"song")
    calls = {"n": 0}

    def broken(_path):
        calls["n"] += 1
        raise ImportError("pymediainfo")

    monkeypatch.setattr(audio_info, "_parse", broken)
    assert load(path) is None
    assert load(path) is None
    assert calls["n"] == 2


def test_parse_error_is_cached_and_a_new_size_parses_again(tmp_path, monkeypatch):
    reset_cache()
    path = tmp_path / "song.opus"
    path.write_bytes(b"song")
    calls = {"n": 0}

    def broken(_path):
        calls["n"] += 1
        raise RuntimeError("bad file")

    monkeypatch.setattr(audio_info, "_parse", broken)
    assert load(path) is None
    assert load(path) is None
    assert calls["n"] == 1
    path.write_bytes(b"song!")
    assert load(path) is None
    assert calls["n"] == 2


def test_library_oserror_is_not_cached(tmp_path, monkeypatch):
    reset_cache()
    path = tmp_path / "song.opus"
    path.write_bytes(b"song")
    calls = {"n": 0}

    def broken(_path):
        calls["n"] += 1
        raise OSError("Failed to load library")

    monkeypatch.setattr(audio_info, "_parse", broken)
    assert load(path) is None
    assert load(path) is None
    assert calls["n"] == 2


def test_missing_file_is_not_cached(tmp_path, monkeypatch):
    reset_cache()
    path = tmp_path / "gone.opus"

    def fail(_path):
        raise AssertionError("parsed a missing file")

    monkeypatch.setattr(audio_info, "_parse", fail)
    assert load(path) is None
