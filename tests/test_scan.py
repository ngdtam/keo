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

