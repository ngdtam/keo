from pathlib import Path

from keo.audio_info import AudioView
from keo.spotdl_cmd import DownloadResult
from keo.jobs import Worker

URL = "https://open.spotify.com/track/sp1"


def test_library_download_script_updates_the_card():
    script = (Path(__file__).parents[1] / "src" / "keo" / "web" / "static" / "keo.js").read_text(
        encoding="utf-8",
    )
    assert "button[data-library-download]" in script
    assert "button[data-download], button[data-post]" in script
    assert 'button.textContent = "Queued"' in script
    assert 'classList.add("bg-yellow")' in script


def test_two_jobs_are_one_library_song(logged_in, library):
    library.enqueue("vid1", "Hà An Huy", "Như Một Người Rưng", source="manual", spotify_id="sp1", spotify_url=URL)
    library.enqueue("vid2", "OSAD", "Như Một Người Rưng", source="manual", spotify_id="sp1", spotify_url=URL)
    page = logged_in.get("/library")
    assert page.text.count("Như Một Người Rưng") == 1
    song = logged_in.get("/library/spotify/sp1")
    assert "Hà An Huy" in song.text
    assert "OSAD" in song.text


def test_job_without_spotify_has_delete_buttons(logged_in, library):
    library.enqueue("vid1", "Đen", "No Match", source="manual")
    page = logged_in.get("/library/video/vid1")
    assert page.status_code == 200
    assert "Remove file" in page.text
    assert "Remove from library" in page.text
    assert 'action="/library/video/vid1/delete"' in page.text


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


def test_library_slider_starts_at_160(logged_in):
    text = logged_in.get("/library").text
    assert "Size" in text
    assert 'min="96"' in text
    assert 'max="320"' in text
    assert 'step="8"' in text
    assert 'value="160"' in text


def test_remove_file_scope_keeps_the_job(logged_in, library, tmp_path):
    library.mark_owned("vid1", "A", "Song", "sp1")
    job = library.enqueue("vid1", "A", "Song", source="manual", spotify_id="sp1", spotify_url=URL)
    library.claim_next()
    library.finish(job.id, ok=True)
    audio = tmp_path / "music" / "A - Song.opus"
    audio.write_bytes(b"song")
    library.set_audio_path("sp1", str(audio))
    response = logged_in.post(
        "/library/spotify/sp1/delete", data={"scope": "file"}, follow_redirects=False,
    )
    assert response.status_code == 303
    assert not audio.exists()
    assert library.get_job(job.id) is not None
    assert "Missing" in logged_in.get("/library").text


def test_remove_from_library_drops_the_card(logged_in, library, tmp_path):
    library.mark_owned("vid1", "A", "Only This Title", "sp1")
    job = library.enqueue(
        "vid1", "A", "Only This Title", source="manual", spotify_id="sp1", spotify_url=URL,
    )
    library.claim_next()
    library.finish(job.id, ok=True)
    audio = tmp_path / "music" / "A - Only This Title.opus"
    audio.write_bytes(b"song")
    audio.with_suffix(".lrc").write_text("words", encoding="utf-8")
    library.set_audio_path("sp1", str(audio))
    library._archive_path.write_text(URL + "\n", encoding="utf-8")
    other = library.enqueue("vid2", "B", "Other Song", source="manual", spotify_id="sp2", spotify_url=URL)
    response = logged_in.post(
        "/library/spotify/sp1/delete", data={"scope": "library"}, follow_redirects=False,
    )
    assert response.status_code == 303
    assert not audio.exists()
    assert not audio.with_suffix(".lrc").exists()
    assert URL not in library._archive_path.read_text(encoding="utf-8")
    assert library.get_job(job.id) is None
    assert library.get_job(other.id) is not None
    text = logged_in.get("/library").text
    assert "Only This Title" not in text
    assert "Other Song" in text


def test_remove_from_library_without_a_file(logged_in, library):
    library.mark_owned("vid1", "A", "Only This Title", "sp1")
    job = library.enqueue(
        "vid1", "A", "Only This Title", source="manual", spotify_id="sp1", spotify_url=URL,
    )
    library.claim_next()
    library.finish(job.id, ok=True)
    response = logged_in.post(
        "/library/spotify/sp1/delete", data={"scope": "library"}, follow_redirects=False,
    )
    assert response.status_code == 303
    assert library.get_job(job.id) is None
    assert "Only This Title" not in logged_in.get("/library").text


def test_remove_from_library_refuses_a_queued_job(logged_in, library, tmp_path):
    library.mark_owned("vid1", "A", "Song", "sp1")
    job = library.enqueue("vid1", "A", "Song", source="manual", spotify_id="sp1", spotify_url=URL)
    audio = tmp_path / "music" / "A - Song.opus"
    audio.write_bytes(b"song")
    library.set_audio_path("sp1", str(audio))
    library._archive_path.write_text(URL + "\n", encoding="utf-8")
    response = logged_in.post("/library/spotify/sp1/delete", data={"scope": "library"})
    assert response.status_code == 409
    assert "This song is still downloading." in response.text
    assert audio.read_bytes() == b"song"
    assert URL in library._archive_path.read_text(encoding="utf-8")
    assert library.get_job(job.id).status == "queued"
    assert library.has_spotify("sp1")


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


def test_download_stores_one_new_file(library, spotdl, tmp_path, cookies):
    music = tmp_path / "music"
    music.mkdir()

    def download(url, spotify_id):
        (music / "A - Song.opus").write_bytes(b"song")
        return DownloadResult(True, "")

    spotdl.download = download
    worker = Worker(library, spotdl, music, cookies=cookies)
    worker.submit("vid1", "A", "Song", spotify_url=URL, spotify_id="sp1")
    assert worker.run_once()
    assert library.audio_path("sp1") == str(music / "A - Song.opus")


def _file(library, tmp_path):
    library.mark_owned("vid1", "A", "Song", "sp1")
    library.enqueue("vid1", "A", "Song", source="manual", spotify_id="sp1", spotify_url=URL)
    audio = tmp_path / "music" / "A - Song.opus"
    audio.write_bytes(b"song")
    library.set_audio_path("sp1", str(audio))


def test_card_and_song_show_probed_audio(logged_in, library, tmp_path, monkeypatch):
    _file(library, tmp_path)
    monkeypatch.setattr(
        "keo.web.routes_library.load",
        lambda path: AudioView(
            "Opus · 251 kbps",
            (
                ("Format", "Opus"),
                ("Bitrate", "251 kbps VBR"),
                ("Sample rate", "48 kHz"),
                ("Channels", "2 · Stereo"),
                ("Duration", "3:42"),
            ),
        ),
    )
    page = logged_in.get("/library").text
    assert "Opus · 251 kbps" in page
    assert "bg-yellow" in page
    song = logged_in.get("/library/spotify/sp1").text
    for label in ("Format", "Bitrate", "Sample rate", "Channels", "Duration"):
        assert label in song
    assert "251 kbps VBR" in song
    assert "Bit depth" not in song


def test_song_page_shows_bit_depth_for_flac(logged_in, library, tmp_path, monkeypatch):
    _file(library, tmp_path)
    monkeypatch.setattr(
        "keo.web.routes_library.load",
        lambda path: AudioView("FLAC", (("Format", "FLAC"), ("Bit depth", "16 bit"))),
    )
    assert "16 bit" in logged_in.get("/library/spotify/sp1").text


def test_missing_probe_leaves_the_labels_out(logged_in, library, tmp_path, monkeypatch):
    _file(library, tmp_path)
    monkeypatch.setattr("keo.web.routes_library.load", lambda path: None)
    assert "kbps" not in logged_in.get("/library").text
    song = logged_in.get("/library/spotify/sp1").text
    assert "Sample rate" not in song
    assert "Bit depth" not in song


def test_download_stores_nothing_when_no_file_appears(library, spotdl, tmp_path, cookies):
    music = tmp_path / "music"
    music.mkdir()
    spotdl.download = lambda url, spotify_id: DownloadResult(True, "")
    worker = Worker(library, spotdl, music, cookies=cookies)
    worker.submit("vid1", "A", "Song", spotify_url=URL, spotify_id="sp1")
    assert worker.run_once()
    assert library.audio_path("sp1") is None


def test_red_spotify_card_has_a_download_button(logged_in, library):
    job = library.enqueue(
        "vid1", "A", "Song", source="manual", spotify_id="sp1", spotify_url=URL,
    )
    library.finish(job.id, ok=False, error="boom")
    text = logged_in.get("/library").text
    assert 'class="bg-red" data-status' in text
    assert 'data-library-download="/api/library/spotify/sp1/download"' in text
    assert ">Download</button>" in text
    chunk = text.split('href="/library/spotify/sp1"', 1)[1]
    assert chunk.index("</a>") < chunk.index("data-library-download")


def test_red_video_card_has_a_download_button(logged_in, library):
    job = library.enqueue("vid1", "Đen", "No Match", source="manual")
    library.finish(job.id, ok=False, error="boom")
    text = logged_in.get("/library").text
    assert 'data-library-download="/api/library/video/vid1/download"' in text
    chunk = text.split('href="/library/video/vid1"', 1)[1]
    assert chunk.index("</a>") < chunk.index("data-library-download")


def test_green_card_has_no_download_button(logged_in, library, tmp_path):
    library.mark_owned("vid1", "A", "Song", "sp1")
    job = library.enqueue(
        "vid1", "A", "Song", source="manual", spotify_id="sp1", spotify_url=URL,
    )
    library.finish(job.id, ok=True)
    audio = tmp_path / "music" / "A - Song.opus"
    audio.write_bytes(b"song")
    library.set_audio_path("sp1", str(audio))
    text = logged_in.get("/library").text
    assert 'class="bg-green" data-status' in text
    assert "data-library-download" not in text
    assert ">Download</button>" not in text


def test_yellow_card_has_no_download_button(logged_in, library, tmp_path):
    library.mark_owned("vid1", "A", "Song", "sp1")
    library.enqueue("vid1", "A", "Song", source="manual", spotify_id="sp1", spotify_url=URL)
    audio = tmp_path / "music" / "A - Song.opus"
    audio.write_bytes(b"song")
    library.set_audio_path("sp1", str(audio))
    text = logged_in.get("/library").text
    assert 'class="bg-yellow" data-status' in text
    assert "data-library-download" not in text


def test_download_retries_a_failed_job_and_clears_the_lock(client, auth, library):
    library.mark_owned("vid1", "A", "Song", "sp1")
    job = library.enqueue(
        "vid1", "A", "Song", source="manual", spotify_id="sp1", spotify_url=URL,
    )
    library.finish(job.id, ok=False, error="boom")
    library._archive_path.write_text(URL + "\n", encoding="utf-8")
    response = client.post("/api/library/spotify/sp1/download", headers=auth)
    assert response.status_code == 200
    body = response.json()
    assert body["job"]["id"] == job.id
    assert body["job"]["status"] == "queued"
    assert len(library.list_jobs()) == 1
    assert library.has_spotify("sp1") is False
    assert library.is_owned_spotify("sp1") is False


def test_download_of_a_queued_song_returns_that_job(client, auth, library):
    library.mark_owned("vid1", "A", "Song", "sp1")
    job = library.enqueue(
        "vid1", "A", "Song", source="manual", spotify_id="sp1", spotify_url=URL,
    )
    library._archive_path.write_text(URL + "\n", encoding="utf-8")
    response = client.post("/api/library/spotify/sp1/download", headers=auth)
    assert response.status_code == 200
    assert response.json()["job"]["id"] == job.id
    assert len(library.list_jobs()) == 1
    assert library.has_spotify("sp1") is True
    assert library.is_owned_spotify("sp1") is True


def test_download_of_a_song_on_disk_is_409(client, auth, library, tmp_path):
    library.mark_owned("vid1", "A", "Song", "sp1")
    job = library.enqueue(
        "vid1", "A", "Song", source="manual", spotify_id="sp1", spotify_url=URL,
    )
    library.finish(job.id, ok=True)
    audio = tmp_path / "music" / "A - Song.opus"
    audio.write_bytes(b"song")
    library.set_audio_path("sp1", str(audio))
    response = client.post("/api/library/spotify/sp1/download", headers=auth)
    assert response.status_code == 409
    assert response.json()["detail"] == "The file is already on disk."
    assert len(library.list_jobs()) == 1
    assert library.get_job(job.id).status == "succeeded"


def test_download_queues_a_succeeded_song_whose_file_is_gone(client, auth, library):
    library.mark_owned("vid1", "A", "Song", "sp1")
    job = library.enqueue(
        "vid1", "A", "Song", source="manual", spotify_id="sp1", spotify_url=URL,
    )
    library.finish(job.id, ok=True)
    library._archive_path.write_text(URL + "\n", encoding="utf-8")
    response = client.post("/api/library/spotify/sp1/download", headers=auth)
    assert response.status_code == 200
    body = response.json()["job"]
    assert body["id"] != job.id
    assert body["status"] == "queued"
    assert body["video_id"] == "vid1"
    assert body["spotify_id"] == "sp1"
    assert body["source"] == "library"
    assert library.has_spotify("sp1") is False
    assert library.is_owned_spotify("sp1") is False


def test_download_refuses_when_another_song_owns_the_title(client, auth, library):
    library.mark_owned("vid-other", "A", "Song", "sp-other")
    job = library.enqueue(
        "vid1", "A", "Song", source="manual", spotify_id="sp1", spotify_url=URL,
    )
    library.finish(job.id, ok=True)
    response = client.post("/api/library/spotify/sp1/download", headers=auth)
    assert response.status_code == 409
    assert response.json()["detail"] == "Already in the library."
    assert library.get_job(job.id).status == "succeeded"
    assert len(library.list_jobs()) == 1


def test_unknown_library_song_is_404(client, auth):
    response = client.post("/api/library/spotify/missing/download", headers=auth)
    assert response.status_code == 404
    assert response.json()["detail"] == "Unknown song"


def test_video_download_retries_and_drops_the_songs_row(client, auth, library):
    library.mark_owned("vid1", "Đen", "No Match", None)
    job = library.enqueue("vid1", "Đen", "No Match", source="manual")
    library.finish(job.id, ok=False, error="boom")
    response = client.post("/api/library/video/vid1/download", headers=auth)
    assert response.status_code == 200
    assert response.json()["job"]["id"] == job.id
    assert response.json()["job"]["status"] == "queued"
    assert library.is_owned("vid1", "Đen", "No Match") is False


def test_old_jobs_table_can_queue_a_library_download(tmp_path):
    import sqlite3

    from keo.library import Library

    db = tmp_path / "keo.db"
    connection = sqlite3.connect(db)
    connection.execute(
        "CREATE TABLE jobs ("
        " id INTEGER PRIMARY KEY, video_id TEXT NOT NULL, artist TEXT NOT NULL,"
        " title TEXT NOT NULL, spotify_url TEXT, spotify_id TEXT,"
        " status TEXT NOT NULL CHECK (status IN ('queued', 'running', 'succeeded', 'failed')),"
        " source TEXT NOT NULL CHECK (source IN ('manual', 'history', 'review')),"
        " error TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL)"
    )
    connection.execute(
        "INSERT INTO jobs (video_id, artist, title, status, source, created_at, updated_at)"
        " VALUES ('vid-old', 'A', 'Kept', 'succeeded', 'manual', 't', 't')"
    )
    connection.commit()
    connection.close()
    library = Library(db, tmp_path / "archive.txt")
    job = library.enqueue(
        "vid1", "A", "Song", source="library", spotify_url=URL, spotify_id="sp1",
    )
    assert job.source == "library"
    kept = library.get_job(1)
    assert (kept.title, kept.status, kept.source) == ("Kept", "succeeded", "manual")


def test_song_page_shows_the_cover_and_no_download(logged_in, library, tmp_path, monkeypatch):
    library.mark_owned("vid1", "A", "Song", "sp1")
    job = library.enqueue(
        "vid1", "A", "Song", source="manual", spotify_id="sp1", spotify_url=URL,
    )
    library.finish(job.id, ok=True)
    audio = tmp_path / "music" / "A - Song.opus"
    audio.write_bytes(b"song")
    library.set_audio_path("sp1", str(audio))
    monkeypatch.setattr(
        "keo.web.routes_library.cover_bytes", lambda path: (b"img", "image/jpeg"),
    )
    text = logged_in.get("/library/spotify/sp1").text
    assert 'src="/library/spotify/sp1/cover"' in text
    assert 'style="width: 160px; height: 160px"' in text
    assert "Remove file" in text
    assert "Remove from library" in text
    assert "data-library-download" not in text


def test_library_nav_item_is_active(logged_in):
    text = logged_in.get("/library").text
    assert 'class="nav-link active" href="/library"' in text


def test_video_song_shows_cover_on_disk_and_no_download(logged_in, library, tmp_path, monkeypatch):
    library.mark_owned("vid_yt", "Đen", "Lối Nhỏ", None)
    job = library.enqueue("vid_yt", "Đen", "Lối Nhỏ", source="youtube")
    library.finish(job.id, ok=True)
    audio = tmp_path / "music" / "Đen - Lối Nhỏ.opus"
    audio.write_bytes(b"song")
    library.set_audio_path("vid_yt", str(audio))
    monkeypatch.setattr(
        "keo.web.routes_library.cover_bytes", lambda path: (b"img", "image/jpeg"),
    )
    # Check library grid card
    lib_text = logged_in.get("/library").text
    assert 'src="/library/video/vid_yt/cover"' in lib_text
    assert "bg-green" in lib_text
    assert 'data-library-download="/api/library/video/vid_yt/download"' not in lib_text

    # Check song page
    song_text = logged_in.get("/library/video/vid_yt").text
    assert 'src="/library/video/vid_yt/cover"' in song_text
    assert "On disk" in song_text
    assert "Remove file" in song_text
    assert "Remove from library" in song_text
    assert 'action="/library/video/vid_yt/delete"' in song_text


def test_video_song_cover_serves_image_bytes(logged_in, library, tmp_path, monkeypatch):
    library.mark_owned("vid_yt", "Đen", "Lối Nhỏ", None)
    job = library.enqueue("vid_yt", "Đen", "Lối Nhỏ", source="youtube")
    library.finish(job.id, ok=True)
    audio = tmp_path / "music" / "Đen - Lối Nhỏ.opus"
    audio.write_bytes(b"song")
    library.set_audio_path("vid_yt", str(audio))
    monkeypatch.setattr(
        "keo.web.routes_library.cover_bytes", lambda path: (b"jpeg_data", "image/jpeg"),
    )
    resp = logged_in.get("/library/video/vid_yt/cover")
    assert resp.status_code == 200
    assert resp.content == b"jpeg_data"
    assert resp.headers["content-type"] == "image/jpeg"


def test_delete_video_song_scope_file(logged_in, library, tmp_path):
    library.mark_owned("vid_yt", "Đen", "Lối Nhỏ", None)
    job = library.enqueue("vid_yt", "Đen", "Lối Nhỏ", source="youtube")
    library.finish(job.id, ok=True)
    audio = tmp_path / "music" / "Đen - Lối Nhỏ.opus"
    audio.write_bytes(b"song")
    audio.with_suffix(".lrc").write_text("lyrics", encoding="utf-8")
    library.set_audio_path("vid_yt", str(audio))

    resp = logged_in.post("/library/video/vid_yt/delete", data={"scope": "file"}, follow_redirects=False)
    assert resp.status_code == 303
    assert not audio.exists()
    assert not audio.with_suffix(".lrc").exists()
    assert library.get_job(job.id) is not None
    assert "Missing" in logged_in.get("/library").text


def test_delete_video_song_scope_library(logged_in, library, tmp_path):
    library.mark_owned("vid_yt", "Đen", "Lối Nhỏ", None)
    job = library.enqueue("vid_yt", "Đen", "Lối Nhỏ", source="youtube")
    library.finish(job.id, ok=True)
    audio = tmp_path / "music" / "Đen - Lối Nhỏ.opus"
    audio.write_bytes(b"song")
    audio.with_suffix(".lrc").write_text("lyrics", encoding="utf-8")
    library.set_audio_path("vid_yt", str(audio))

    resp = logged_in.post("/library/video/vid_yt/delete", data={"scope": "library"}, follow_redirects=False)
    assert resp.status_code == 303
    assert not audio.exists()
    assert not audio.with_suffix(".lrc").exists()
    assert library.get_job(job.id) is None
    assert "Lối Nhỏ" not in logged_in.get("/library").text
