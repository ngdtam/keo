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

    def download(url, spotify_id, **kwargs):
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
    spotdl.download = lambda url, spotify_id, **kwargs: DownloadResult(True, "")
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


def test_library_shows_more_than_100_songs_and_deletes_oldest(logged_in, library):
    for i in range(105):
        library.mark_owned(f"vid{i}", f"Artist {i}", f"Title {i}", f"sp{i}")
        library.enqueue(f"vid{i}", f"Artist {i}", f"Title {i}", source="manual", spotify_id=f"sp{i}")
    page = logged_in.get("/library")
    assert "Title 0" in page.text
    assert "Title 104" in page.text
    resp = logged_in.post("/library/spotify/sp0/delete", follow_redirects=False)
    assert resp.status_code == 303
    assert not library.has_spotify("sp0")


def test_library_uses_cached_metadata_without_opening_files(logged_in, library, tmp_path, monkeypatch):
    audio = tmp_path / "music" / "A - Cached.mp3"
    audio.write_bytes(b"mp3")
    song = library.upsert_scanned_song("A", "Cached", str(audio))
    library.set_audio_cache(
        song.id, audio_path=str(audio), mtime=audio.stat().st_mtime, size=3,
        card="MP3 · 320 kbps", details=(("Format", "MP3"),), has_cover=True,
    )
    monkeypatch.setattr(
        "keo.web.routes_library.load",
        lambda p: (_ for _ in ()).throw(AssertionError("should not read MediaInfo")),
    )
    monkeypatch.setattr(
        "keo.web.routes_library.cover_bytes",
        lambda p: (_ for _ in ()).throw(AssertionError("should not read cover")),
    )
    page = logged_in.get("/library")
    assert page.status_code == 200
    assert "MP3 · 320 kbps" in page.text
    assert f"/library/song/{song.id}/cover" in page.text


def test_rescan_button_and_manual_song_page_and_delete(logged_in, library, tmp_path):
    audio = tmp_path / "music" / "Manual Artist - Manual Song.mp3"
    audio.write_bytes(b"mp3")
    resp = logged_in.post("/library/rescan", follow_redirects=False)
    assert resp.status_code == 303
    page = logged_in.get("/library")
    assert "Manual Song" in page.text
    song = library.list_songs()[0]
    detail = logged_in.get(f"/library/song/{song.id}")
    assert detail.status_code == 200
    assert "Manual Artist" in detail.text
    delete = logged_in.post(f"/library/song/{song.id}/delete", data={"scope": "library"}, follow_redirects=False)
    assert delete.status_code == 303
    assert not audio.exists()
    assert library.list_songs() == []


def test_delete_moves_to_recycle_and_trash_restore_delete_empty(logged_in, library, tmp_path):
    music = tmp_path / "music"
    audio = music / "Trash Artist - Trash Song.mp3"
    audio.write_bytes(b"mp3")
    audio.with_suffix(".lrc").write_text("words", encoding="utf-8")
    logged_in.post("/library/rescan")
    song = library.list_songs()[0]

    logged_in.post(f"/library/song/{song.id}/delete", data={"scope": "library"}, follow_redirects=False)
    assert not audio.exists()
    assert (music / ".recycle" / "Trash Artist - Trash Song.mp3").is_file()
    assert (music / ".recycle" / "Trash Artist - Trash Song.lrc").is_file()

    trash = logged_in.get("/library/trash")
    assert trash.status_code == 200
    assert "Trash Artist - Trash Song.mp3" in trash.text
    assert "bytes" not in trash.text
    assert "btn-outline-danger" in trash.text

    restore = logged_in.post(
        "/library/trash/restore",
        data={"name": "Trash Artist - Trash Song.mp3"},
        follow_redirects=False,
    )
    assert restore.status_code == 303
    assert audio.is_file()
    assert audio.with_suffix(".lrc").is_file()
    assert len(library.list_songs()) == 1

    song2 = library.list_songs()[0]
    logged_in.post(f"/library/song/{song2.id}/delete", data={"scope": "library"})
    perm = logged_in.post(
        "/library/trash/delete",
        data={"name": "Trash Artist - Trash Song.mp3"},
        follow_redirects=False,
    )
    assert perm.status_code == 303
    assert not (music / ".recycle" / "Trash Artist - Trash Song.mp3").exists()

    extra = music / "Extra - Song.mp3"
    extra.write_bytes(b"mp3")
    logged_in.post("/library/rescan")
    song3 = library.list_songs()[0]
    logged_in.post(f"/library/song/{song3.id}/delete", data={"scope": "library"})
    empty = logged_in.post("/library/trash/empty", follow_redirects=False)
    assert empty.status_code == 303
    assert "No trashed files." in logged_in.get("/library/trash").text


def test_library_organize_route_moves_files_and_redirects(logged_in, library, spotdl, tmp_path):
    from dataclasses import replace

    music = tmp_path / "music"
    music.mkdir(parents=True, exist_ok=True)
    audio = music / "Low G - An Thần.mp3"
    audio.write_bytes(b"mp3")
    audio.with_suffix(".lrc").write_text("[00:00] lyrics", encoding="utf-8")
    spotdl.settings = replace(spotdl.settings, output_template="{artist}/{title}.{output-ext}")

    logged_in.post("/library/rescan")
    res = logged_in.post("/library/organize", follow_redirects=False)
    assert res.status_code == 303
    assert (music / "Low G" / "An Thần.mp3").is_file()
    assert (music / "Low G" / "An Thần.lrc").is_file()
    assert not audio.exists()


def test_library_missing_and_upgradable_filters_and_bulk_actions(logged_in, library, spotdl, probe, tmp_path):
    from dataclasses import replace
    from keo.cookies import ProbeResult

    music = tmp_path / "music"
    music.mkdir(parents=True, exist_ok=True)
    cookie = tmp_path / "cookies.txt"
    cookie.write_text("# Netscape HTTP Cookie File\n", encoding="utf-8")
    probe.results.append(ProbeResult([{"format_id": "141"}], ""))
    spotdl.settings = replace(spotdl.settings, format="opus", bitrate="256k", cookie_file=str(cookie))

    # 1. Missing song (job succeeded, no file)
    library.mark_owned("v_miss", "A", "Missing Song", "sp_miss")
    j_miss = library.enqueue("v_miss", "A", "Missing Song", source="manual", spotify_id="sp_miss", spotify_url=URL)
    library.finish(j_miss.id, ok=True)

    # 2. Upgradable song (128 kbps mp3 on disk)
    low_file = music / "B - Low Quality.mp3"
    low_file.write_bytes(b"mp3")
    library.mark_owned("v_low", "B", "Low Quality", "sp_low")
    j_low = library.enqueue("v_low", "B", "Low Quality", source="manual", spotify_id="sp_low", spotify_url=URL)
    library.finish(j_low.id, ok=True)
    library.set_audio_path("sp_low", str(low_file))
    low_song = library.get_song_by_identifier("spotify", "sp_low")
    library.set_audio_cache(
        low_song.id, audio_path=str(low_file), mtime=1.0, size=3,
        card="MP3 · 128 kbps", details=(), has_cover=False,
    )

    # 3. Up-to-date song (256 kbps opus on disk)
    ok_file = music / "C - Good Quality.opus"
    ok_file.write_bytes(b"opus")
    library.mark_owned("v_ok", "C", "Good Quality", "sp_ok")
    j_ok = library.enqueue("v_ok", "C", "Good Quality", source="manual", spotify_id="sp_ok", spotify_url=URL)
    library.finish(j_ok.id, ok=True)
    library.set_audio_path("sp_ok", str(ok_file))
    ok_song = library.get_song_by_identifier("spotify", "sp_ok")
    library.set_audio_cache(
        ok_song.id, audio_path=str(ok_file), mtime=1.0, size=4,
        card="OPUS · 256 kbps", details=(), has_cover=False,
    )

    miss_page = logged_in.get("/library?filter=missing")
    assert miss_page.status_code == 200
    assert "Missing Song" in miss_page.text
    assert "Low Quality" not in miss_page.text
    assert "Good Quality" not in miss_page.text

    upg_page = logged_in.get("/library?filter=upgradable")
    assert upg_page.status_code == 200
    assert "Low Quality" in upg_page.text
    assert "Missing Song" not in upg_page.text
    assert "Good Quality" not in upg_page.text

    res_miss = logged_in.post("/library/redownload-missing", follow_redirects=False)
    assert res_miss.status_code == 303
    assert library.active_job("v_miss") is not None

    res_upg = logged_in.post("/library/upgrade-all", follow_redirects=False)
    assert res_upg.status_code == 303
    assert not low_file.exists()
    assert (music / ".recycle" / "B - Low Quality.mp3").is_file()
    assert library.active_job("v_low") is not None
    assert library.active_job("v_ok") is None


def test_library_stats_search_and_sort(logged_in, library, tmp_path):
    music = tmp_path / "music"
    music.mkdir(parents=True, exist_ok=True)
    for idx, (artist, title, ext, card) in enumerate(
        (
            ("Zed", "Beta", "opus", "OPUS · 256 kbps"),
            ("Alpha", "Gamma", "mp3", "MP3 · 320 kbps"),
        ),
        start=1,
    ):
        f = music / f"{artist} - {title}.{ext}"
        f.write_bytes(b"x" * (1024 * idx))
        library.mark_owned(f"v{idx}", artist, title, f"sp{idx}")
        song = library.get_song_by_identifier("spotify", f"sp{idx}")
        library.set_audio_cache(
            song.id, audio_path=str(f), mtime=1.0, size=1024 * idx,
            card=card, details=(), has_cover=False,
        )

    res = logged_in.get("/library?q=Alpha&sort=title")
    assert res.status_code == 200
    assert "2 songs" in res.text
    assert "OPUS: 1" in res.text
    assert "MP3: 1" in res.text
    assert "Gamma" in res.text
    assert "Beta" not in res.text


def test_library_preview_rename_shows_planned_moves_without_moving(logged_in, library, tmp_path):
    music = tmp_path / "music"
    music.mkdir(parents=True, exist_ok=True)
    src = music / "messy_name.mp3"
    src.write_bytes(b"audio")
    library.mark_owned("v_ren", "Son Tung", "Lac Troi", "sp_ren")
    song = library.get_song_by_identifier("spotify", "sp_ren")
    library.set_audio_cache(
        song.id, audio_path=str(src), mtime=1.0, size=5, card="MP3", details=(), has_cover=False
    )

    res = logged_in.get("/library/organize/preview")
    assert res.status_code == 200
    assert "Path change" in res.text
    assert "- " in res.text
    assert "+ " in res.text
    assert "messy_name.mp3" in res.text
    assert "Son Tung - Lac Troi.mp3" in res.text
    assert src.is_file()


def test_library_legend_uses_status_dots(logged_in):
    text = logged_in.get("/library").text
    assert "position-sticky top-0" in text
    assert "status-dot bg-green" in text
    assert "status-dot bg-red" in text
    assert "status-dot bg-yellow" in text


def test_library_bulk_redownload_and_delete(logged_in, library, tmp_path):
    music = tmp_path / "music"
    music.mkdir(parents=True, exist_ok=True)
    f1 = music / "A - One.mp3"
    f2 = music / "B - Two.mp3"
    f1.write_bytes(b"1")
    f2.write_bytes(b"2")
    library.mark_owned("v1", "A", "One", "sp1")
    library.mark_owned("v2", "B", "Two", "sp2")
    s1 = library.get_song_by_identifier("spotify", "sp1")
    s2 = library.get_song_by_identifier("spotify", "sp2")
    library.set_audio_cache(s1.id, audio_path=str(f1), mtime=1.0, size=1, card="MP3", details=(), has_cover=False)
    library.set_audio_cache(s2.id, audio_path=str(f2), mtime=1.0, size=1, card="MP3", details=(), has_cover=False)

    res_del = logged_in.post(
        "/library/bulk",
        data={"action": "delete", "items": ["spotify:sp1"]},
        follow_redirects=False,
    )
    assert res_del.status_code == 303
    assert not f1.exists()
    assert (music / ".recycle" / "A - One.mp3").is_file()
    assert library.get_song_by_identifier("spotify", "sp1") is None

    res_redl = logged_in.post(
        "/library/bulk",
        data={"action": "redownload", "items": ["spotify:sp2"]},
        follow_redirects=False,
    )
    assert res_redl.status_code == 303
    assert library.active_job("v2") is not None


def test_library_unmatched_filter_and_link_song(logged_in, library, tmp_path):
    music = tmp_path / "music"
    music.mkdir(parents=True, exist_ok=True)
    f = music / "unknown_track.mp3"
    f.write_bytes(b"audio")
    song = library.upsert_scanned_song("", "unknown_track", str(f))
    library.set_audio_cache(song.id, audio_path=str(f), mtime=1.0, size=5, card="MP3", details=(), has_cover=False)

    res = logged_in.get("/library?filter=unmatched")
    assert res.status_code == 200
    assert "unknown_track" in res.text

    link_res = logged_in.post(
        f"/library/song/{song.id}/link",
        data={"video_id": "ytm123", "artist": "Den", "title": "Bai Nay Chill Phet"},
        follow_redirects=False,
    )
    assert link_res.status_code == 303
    linked = library.get_song_by_identifier("video", "ytm123")
    assert linked is not None
    assert linked.artist == "Den"
    assert linked.title == "Bai Nay Chill Phet"
    assert linked.audio_path == str(f)


def test_library_layout_hierarchy_and_overlay_checkbox(logged_in, library):
    library.upsert_scanned_song("A", "Song", "/music/A - Song.mp3")
    html = logged_in.get("/library").text
    assert 'class="form-check-input library-select position-absolute top-0 start-0 m-2 z-1"' in html
    assert 'style="width: var(--cover, 160px)"' not in html
    assert 'id="bulk-form"' in html
    assert "d-none align-items-center" in html
    assert 'id="bulk-count"' in html

    css = logged_in.get("/static/keo.css").text
    assert ".btn-primary,\n.btn.active" in css
    assert ".library-select" in css
    assert "repeat(auto-fill, minmax(var(--cover, 160px), 1fr))" in css


def test_song_page_uses_datagrid_and_human_size(logged_in, library, tmp_path, monkeypatch):
    audio = _file(library, tmp_path)
    monkeypatch.setattr(
        "keo.web.routes_library.load",
        lambda path: AudioView("Opus · 251 kbps", (("Format", "Opus"), ("Bitrate", "251 kbps"))),
    )
    html = logged_in.get("/library/spotify/sp1").text
    assert 'class="datagrid"' in html
    assert "0 KB" in html
    assert "bytes" not in html
    assert "btn-outline-danger" in html
