import time

from tests.fakes import QUERY, candidate

URL = "https://open.spotify.com/track/sp1"


def submit(worker, **kwargs):
    return worker.submit("vid1", "Sơn Tùng M-TP", "Lạc Trôi", **kwargs)


def test_submit_records_mp3_as_not_premium(worker):
    job = submit(worker)
    assert (job.format, job.premium) == ("mp3", 0)


def test_tap_creates_one_job_and_a_second_tap_reuses_it(worker, library):
    first = submit(worker)
    second = submit(worker)
    assert first.status == "queued"
    assert second.id == first.id
    assert len(library.list_jobs()) == 1


def test_owned_song_is_not_queued(worker, library):
    library.mark_owned("vid1", "Sơn Tùng M-TP", "Lạc Trôi", "sp1")
    assert submit(worker) is None
    assert library.list_jobs() == []


def test_finished_download_marks_the_song_owned(worker, library, spotdl):
    spotdl.candidates[QUERY] = candidate()
    job = submit(worker)
    assert worker.run_once()
    done = library.get_job(job.id)
    assert (done.status, done.spotify_url) == ("succeeded", URL)
    assert spotdl.lookups == [QUERY]
    assert spotdl.downloads == [URL]
    assert library.is_owned("vid1", "x", "y")


def test_weak_match_falls_back_to_youtube_download(worker, library, spotdl, fake_youtube_runner):
    spotdl.candidates[QUERY] = candidate(name="Different Song", artist="Other Artist")
    job = submit(worker)
    worker.run_once()
    assert library.get_job(job.id).status == "succeeded"
    assert len(fake_youtube_runner.calls) == 1
    assert library.is_owned("vid1", "Sơn Tùng M-TP", "Lạc Trôi")


def test_no_metadata_falls_back_to_youtube_download(worker, library, spotdl, fake_youtube_runner):
    job = submit(worker)
    worker.run_once()
    stored = library.get_job(job.id)
    assert stored.status == "succeeded"
    assert len(fake_youtube_runner.calls) == 1
    assert library.is_owned("vid1", "Sơn Tùng M-TP", "Lạc Trôi")


def test_youtube_fallback_failure_marks_job_failed(worker, library, spotdl):
    worker._youtube_runner = lambda args, **kwargs: subprocess.CompletedProcess(args, 1, stdout="", stderr="yt-dlp error")
    job = submit(worker)
    worker.run_once()
    stored = library.get_job(job.id)
    assert stored.status == "failed"
    assert "yt-dlp error" in stored.error
    assert not library.is_owned("vid1", "Sơn Tùng M-TP", "Lạc Trôi")


def test_song_already_in_the_archive_is_not_downloaded_again(worker, library, spotdl, tmp_path):
    spotdl.candidates[QUERY] = candidate()
    (tmp_path / "archive.txt").write_text(URL + "\n", encoding="utf-8")
    job = submit(worker)
    worker.run_once()
    assert library.get_job(job.id).status == "succeeded"
    assert spotdl.downloads == []
    assert library.is_owned("vid1", "", "")


def test_retry_requeues_the_same_url(worker, library, spotdl):
    spotdl.candidates[QUERY] = candidate()
    spotdl.download_ok = False
    job = submit(worker)
    worker.run_once()
    spotdl.download_ok = True
    retried = worker.retry(job.id)
    assert (retried.id, retried.status, retried.spotify_url) == (job.id, "queued", URL)
    worker.run_once()
    assert spotdl.lookups == [QUERY]
    assert spotdl.downloads == [URL, URL]
    assert library.get_job(job.id).status == "succeeded"


def test_job_with_a_spotify_url_skips_the_lookup(worker, library, spotdl):
    job = submit(worker, source="history", spotify_url=URL, spotify_id="sp1")
    worker.run_once()
    assert spotdl.lookups == []
    assert library.get_job(job.id).status == "succeeded"


def test_crash_fails_the_job_instead_of_the_worker(worker, library, spotdl):
    spotdl.candidates[QUERY] = candidate()
    spotdl.download_error = OSError("disk full")
    job = submit(worker)
    assert worker.run_once()
    stored = library.get_job(job.id)
    assert stored.status == "failed"
    assert "disk full" in stored.error


def test_started_worker_runs_the_queue(worker, library, spotdl):
    spotdl.candidates[QUERY] = candidate()
    worker.start()
    try:
        job = submit(worker)
        deadline = time.monotonic() + 5
        while library.get_job(job.id).status != "succeeded" and time.monotonic() < deadline:
            time.sleep(0.05)
    finally:
        worker.stop()
    assert library.get_job(job.id).status == "succeeded"


def test_queue_error_does_not_kill_the_worker(worker, library, monkeypatch):
    monkeypatch.setattr("keo.jobs.IDLE_WAIT_SECONDS", 0.01)
    claims = []

    def claim_next():
        claims.append(1)
        if len(claims) == 1:
            raise RuntimeError("queue error")
        return None

    library.claim_next = claim_next
    worker.start()
    try:
        deadline = time.monotonic() + 2
        while len(claims) < 2 and time.monotonic() < deadline:
            time.sleep(0.05)
        assert len(claims) >= 2
        assert worker._thread.is_alive()
    finally:
        worker.stop()
    assert not worker._thread.is_alive()


from keo.config import SpotdlSettings
from keo.cookies import MISSING_LINE


def test_bitrate_disable_with_a_cookie_file_is_stored_as_premium(worker, spotdl, tmp_path):
    path = tmp_path / "cookies.txt"
    path.write_text("a", encoding="utf-8")
    spotdl.settings = SpotdlSettings(format="mp3", bitrate="disable", cookie_file=str(path))
    job = submit(worker)
    assert (job.format, job.premium) == ("mp3", 1)


def test_a_premium_job_fails_before_lookup_when_the_cookie_is_missing(worker, library, spotdl):
    spotdl.settings = SpotdlSettings(format="opus")
    job = submit(worker)
    assert job.premium == 0
    assert worker.run_once()
    stored = library.get_job(job.id)
    assert (stored.status, stored.error) == ("failed", MISSING_LINE)
    assert spotdl.lookups == []
    assert spotdl.downloads == []


import subprocess
from keo.jobs import Worker


def test_youtube_source_job_downloads_via_youtube_and_marks_owned(library, spotdl, cookies, tmp_path):
    music = tmp_path / "music"
    music.mkdir()
    calls = []

    def fake_youtube_runner(args, **kwargs):
        calls.append(args)
        # Simulate creating an audio file in music
        (music / "Low G - An Thần.mp3").write_bytes(b"dummy audio")
        return subprocess.CompletedProcess(args, 0, stdout="Downloaded", stderr="")

    yt_worker = Worker(library, spotdl, music_dir=music, cookies=cookies, youtube_runner=fake_youtube_runner)
    library.add_review("vid_yt", "Low G", "An Thần", reason="no_metadata")
    job = yt_worker.submit("vid_yt", "Low G", "An Thần", source="youtube")
    assert job.source == "youtube"
    assert yt_worker.run_once()
    stored = library.get_job(job.id)
    assert stored.status == "succeeded"
    assert len(calls) == 1
    assert library.is_owned("vid_yt", "Low G", "An Thần")
    assert library.list_review() == []
    assert library.audio_path("vid_yt") == str(music / "Low G - An Thần.mp3")


def test_youtube_source_job_records_failure(library, spotdl, cookies, tmp_path):
    def failing_runner(args, **kwargs):
        return subprocess.CompletedProcess(args, 1, stdout="", stderr="yt-dlp error")

    yt_worker = Worker(library, spotdl, music_dir=tmp_path, cookies=cookies, youtube_runner=failing_runner)
    job = yt_worker.submit("vid_yt_fail", "Artist", "Fail", source="youtube")
    assert yt_worker.run_once()
    stored = library.get_job(job.id)
    assert stored.status == "failed"
    assert "yt-dlp error" in stored.error
    assert not library.is_owned("vid_yt_fail", "Artist", "Fail")


def test_worker_passes_video_id_to_spotdl_download(library, spotdl, cookies):
    spotdl.candidates["Sơn Tùng M-TP - Lạc Trôi"] = candidate()
    w = Worker(library, spotdl, cookies=cookies)
    w.submit("vid123", "Sơn Tùng M-TP", "Lạc Trôi")
    assert w.run_once()
    assert spotdl.download_video_ids == ["vid123"]

