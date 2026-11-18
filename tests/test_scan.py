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


def test_read_tags_and_organize_preserve_multiple_artists(library, tmp_path, monkeypatch):
    import types
    import mutagen
    from keo.scan import organize_library, read_tags

    music = tmp_path / "music"
    music.mkdir()
    audio = music / "Artist A, Artist B - Collab.mp3"
    audio.write_bytes(b"audio")

    fake_audio = types.SimpleNamespace(
        tags={"artist": ["Artist A", "Artist B"], "title": ["Collab"]}
    )
    monkeypatch.setattr(mutagen, "File", lambda *a, **kw: fake_audio)

    assert read_tags(audio) == ("Artist A, Artist B", "Collab")
    scan(music, library, audio_loader=lambda p: None, cover_checker=lambda p: False)
    assert library.list_songs()[0].artist == "Artist A, Artist B"
    assert organize_library(music, library, "{artists} - {title}.{output-ext}") == 0
    assert audio.is_file()


def test_plan_organize_prefers_embedded_tags_over_stale_db_artist(library, tmp_path, monkeypatch):
    import types
    import mutagen
    from keo.scan import plan_organize

    music = tmp_path / "music"
    music.mkdir()
    audio = music / "Osad, Hà An Huy - Như Một Người Rưng.mp3"
    audio.write_bytes(b"audio")
    library.mark_owned("vid_osad", "OSAD", "Như Một Người Rưng", "sp_osad")
    library.set_audio_path("vid_osad", str(audio))

    fake_audio = types.SimpleNamespace(
        tags={"artist": ["Osad", "Hà An Huy"], "title": ["Như Một Người Rưng"]}
    )
    monkeypatch.setattr(mutagen, "File", lambda *a, **kw: fake_audio)

    assert plan_organize(music, library, "{artists} - {title}.{output-ext}") == []
    planned = plan_organize(music, library, "{artists}/{title}.{output-ext}")
    assert len(planned) == 1
    assert planned[0][2] == music / "Osad, Hà An Huy" / "Như Một Người Rưng.mp3"

    # When embedded tags are absent, fall back to DB artist/title rather than filename stem.
    untagged = music / "wrong name - track.mp3"
    untagged.write_bytes(b"audio")
    library.mark_owned("vid_db", "Good Artist", "Good Title", "sp_db")
    library.set_audio_path("vid_db", str(untagged))
    monkeypatch.setattr(mutagen, "File", lambda *a, **kw: None)
    planned_fallback = plan_organize(music, library, "{artists} - {title}.{output-ext}")
    assert any(dst == music / "Good Artist - Good Title.mp3" for _, _, dst in planned_fallback)



