from keo.audio_info import AudioView
from keo.scan import ScanResult, scan


def test_scan_missing_dir_returns_zero(library, tmp_path):
    assert scan(tmp_path / "nope", library) == ScanResult(0, 0, 0)


def test_scan_imports_manual_file_and_skips_unchanged_mtime(library, tmp_path):
    music = tmp_path / "music"
    music.mkdir()
    track = music / "Sơn Tùng M-TP - Lạc Trôi.mp3"
    track.write_bytes(b"audio")

    calls = []

    def fake_loader(path):
        calls.append(path)
        return AudioView("MP3 · 320 kbps", (("Format", "MP3"),))

    first = scan(music, library, audio_loader=fake_loader, cover_checker=lambda p: True)
    assert first == ScanResult(added=1, updated=0, missing=0)
    assert len(calls) == 1
    songs = library.list_songs()
    assert len(songs) == 1
    assert songs[0].artist == "Sơn Tùng M-TP"
    assert songs[0].title == "Lạc Trôi"
    assert songs[0].audio_card == "MP3 · 320 kbps"
    assert songs[0].has_cover is True

    second = scan(music, library, audio_loader=fake_loader, cover_checker=lambda p: True)
    assert second == ScanResult(added=0, updated=0, missing=0)
    assert len(calls) == 1


def test_scan_clears_deleted_files_and_handles_corrupt_tags(library, tmp_path):
    music = tmp_path / "music"
    music.mkdir()
    track = music / "Artist - BadFile.opus"
    track.write_bytes(b"not-valid-audio")

    res = scan(music, library, audio_loader=lambda p: None, cover_checker=lambda p: False)
    assert res == ScanResult(added=1, updated=0, missing=0)

    track.unlink()
    res2 = scan(music, library, audio_loader=lambda p: None, cover_checker=lambda p: False)
    assert res2 == ScanResult(added=0, updated=0, missing=1)
    assert library.list_songs()[0].audio_path is None


def test_list_audio_finds_nested_artist_folders_and_skips_recycle(tmp_path):
    from keo.library import list_audio

    music = tmp_path / "music"
    (music / "Low G").mkdir(parents=True)
    (music / ".recycle").mkdir(parents=True)
    nested = music / "Low G" / "An Thần.mp3"
    nested.write_bytes(b"audio")
    trashed = music / ".recycle" / "Old.mp3"
    trashed.write_bytes(b"old")

    found = list_audio(music)
    assert nested in found
    assert trashed not in found


def test_organize_library_renames_audio_and_lrc_sidecar(library, tmp_path):
    from keo.scan import organize_library

    music = tmp_path / "music"
    music.mkdir()
    audio = music / "Low G - An Thần.mp3"
    audio.write_bytes(b"audio")
    lrc = music / "Low G - An Thần.lrc"
    lrc.write_text("[00:01.00] hi", encoding="utf-8")

    scan(music, library, audio_loader=lambda p: None, cover_checker=lambda p: False)
    moved = organize_library(music, library, "{artist}/{title}.{output-ext}")
    assert moved == 1

    target = music / "Low G" / "An Thần.mp3"
    assert target.is_file()
    assert target.with_suffix(".lrc").is_file()
    assert not audio.exists()
    assert not lrc.exists()
    assert library.list_songs()[0].audio_path == str(target)

