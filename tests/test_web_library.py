from keo.spotdl_cmd import DownloadResult
from keo.jobs import Worker

URL = "https://open.spotify.com/track/sp1"


def test_two_jobs_are_one_library_song(logged_in, library):
    library.enqueue("vid1", "Hà An Huy", "Như Một Người Rưng", source="manual", spotify_id="sp1", spotify_url=URL)
    library.enqueue("vid2", "OSAD", "Như Một Người Rưng", source="manual", spotify_id="sp1", spotify_url=URL)
    page = logged_in.get("/library")
    assert page.text.count("Như Một Người Rưng") == 1
    song = logged_in.get("/library/spotify/sp1")
    assert "Hà An Huy" in song.text
    assert "OSAD" in song.text


def test_job_without_spotify_has_no_delete_button(logged_in, library):
    library.enqueue("vid1", "Đen", "No Match", source="manual")
    page = logged_in.get("/library/video/vid1")
    assert page.status_code == 200
    assert "Delete" not in page.text


def test_unique_title_file_is_saved(logged_in, library, tmp_path):
    library.mark_owned("vid1", "Sơn Tùng M-TP", "Đừng Làm Trái Tim Anh Đau", "sp1")
    job = library.enqueue(
        "vid1", "Sơn Tùng M-TP", "Đừng Làm Trái Tim Anh Đau",
        source="manual", spotify_id="sp1", spotify_url=URL,
    )
    library.claim_next()
    library.finish(job.id, ok=True)
    audio = tmp_path / "music" / "Sơn Tùng M-TP - Đừng Làm Trái Tim Anh Đau.opus"
    audio.write_bytes(b"song")
    text = logged_in.get("/library/spotify/sp1").text
    assert "On disk" in text
    assert "bg-green" in text
    assert str(audio) in text
    assert library.audio_path("sp1") == str(audio)


def test_two_title_matches_are_not_saved(logged_in, library, tmp_path):
    library.mark_owned("vid1", "A", "Same", "sp1")
    job = library.enqueue("vid1", "A", "Same", source="manual", spotify_id="sp1", spotify_url=URL)
    library.claim_next()
    library.finish(job.id, ok=True)
    (tmp_path / "music" / "A - Same.opus").write_bytes(b"a")
    (tmp_path / "music" / "B - Same.opus").write_bytes(b"b")
    text = logged_in.get("/library/spotify/sp1").text
    assert "Missing" in text
    assert library.audio_path("sp1") is None


def test_queued_song_is_yellow_even_with_a_file(logged_in, library, tmp_path):
    library.mark_owned("vid1", "A", "Song", "sp1")
    audio = tmp_path / "music" / "A - Song.opus"
    audio.write_bytes(b"song")
    library.set_audio_path("sp1", str(audio))
    library.enqueue("vid1", "A", "Song", source="manual", spotify_id="sp1", spotify_url=URL)
    text = logged_in.get("/library").text
    assert "bg-yellow" in text
    assert "Queued or running" in text


def test_delete_clears_the_file_and_the_lock(logged_in, library, worker, tmp_path):
    library.mark_owned("vid1", "A", "Song", "sp1")
    job = library.enqueue("vid1", "A", "Song", source="manual", spotify_id="sp1", spotify_url=URL)
    library.claim_next()
    library.finish(job.id, ok=True)
    audio = tmp_path / "music" / "A - Song.opus"
    audio.write_bytes(b"song")
    audio.with_suffix(".lrc").write_text("words", encoding="utf-8")
    library.set_audio_path("sp1", str(audio))
    library._archive_path.write_text(URL + "\n", encoding="utf-8")
    response = logged_in.post("/library/spotify/sp1/delete", follow_redirects=False)
    assert response.status_code == 303
    assert not audio.exists()
    assert not audio.with_suffix(".lrc").exists()
    assert URL not in library._archive_path.read_text(encoding="utf-8")
    assert library.get_job(1) is not None
    assert "Missing" in logged_in.get("/library").text
    assert worker.submit("vid1", "A", "Song", spotify_url=URL, spotify_id="sp1") is not None


def test_delete_refuses_a_path_outside_the_music_folder(logged_in, library, tmp_path):
    library.mark_owned("vid1", "A", "Song", "sp1")
    library.enqueue("vid1", "A", "Song", source="manual", spotify_id="sp1", spotify_url=URL)
    outside = tmp_path / "outside.opus"
    outside.write_bytes(b"keep")
    library.set_audio_path("sp1", str(outside))
    library._archive_path.write_text(URL + "\n", encoding="utf-8")
    response = logged_in.post("/library/spotify/sp1/delete")
    assert response.status_code == 400
    assert outside.read_bytes() == b"keep"
    assert URL in library._archive_path.read_text(encoding="utf-8")
    assert library.has_spotify("sp1")


def test_dashboard_links_a_failed_song(logged_in, library):
    job = library.enqueue("vid1", "Đen", "Broken", source="manual", spotify_id="sp9", spotify_url=URL)
    library.finish(job.id, ok=False, error="boom")
    text = logged_in.get("/").text
    assert 'href="/library/spotify/sp9"' in text
    assert "Broken" in text


def test_download_stores_one_new_file(library, spotdl, tmp_path):
    music = tmp_path / "music"
    music.mkdir()

    def download(url, spotify_id):
        (music / "A - Song.opus").write_bytes(b"song")
        return DownloadResult(True, "")

    spotdl.download = download
    worker = Worker(library, spotdl, music)
    worker.submit("vid1", "A", "Song", spotify_url=URL, spotify_id="sp1")
    assert worker.run_once()
    assert library.audio_path("sp1") == str(music / "A - Song.opus")


def test_download_stores_nothing_when_no_file_appears(library, spotdl, tmp_path):
    music = tmp_path / "music"
    music.mkdir()
    spotdl.download = lambda url, spotify_id: DownloadResult(True, "")
    worker = Worker(library, spotdl, music)
    worker.submit("vid1", "A", "Song", spotify_url=URL, spotify_id="sp1")
    assert worker.run_once()
    assert library.audio_path("sp1") is None
