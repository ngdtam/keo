import time

from tests.fakes import QUERY, candidate

URL = "https://open.spotify.com/track/sp1"


def submit(worker, **kwargs):
    return worker.submit("vid1", "Sơn Tùng M-TP", "Lạc Trôi", **kwargs)


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


def test_tap_downloads_the_top_match_even_when_names_differ(worker, library, spotdl):
    spotdl.candidates[QUERY] = candidate(name="Different Song", artist="Other Artist")
    job = submit(worker)
    worker.run_once()
    assert library.get_job(job.id).status == "succeeded"


def test_failed_download_keeps_the_output_and_the_worker_moves_on(worker, library, spotdl):
    spotdl.candidates[QUERY] = candidate()
    spotdl.candidates["Đen - Bài Này Chill Phết"] = candidate(
        name="Bài Này Chill Phết", artist="Đen", song_id="sp2"
    )
    spotdl.download_ok = False
    failed = submit(worker)
    other = worker.submit("vid2", "Đen", "Bài Này Chill Phết")
    assert worker.run_once()
    stored = library.get_job(failed.id)
    assert (stored.status, stored.error) == ("failed", "fake download output")
    assert not library.is_owned("vid1", "Sơn Tùng M-TP", "Lạc Trôi")
    spotdl.download_ok = True
    assert worker.run_once()
    assert library.get_job(other.id).status == "succeeded"
    assert not worker.run_once()


def test_no_metadata_fails_the_job(worker, library, spotdl):
    job = submit(worker)
    worker.run_once()
    stored = library.get_job(job.id)
    assert stored.status == "failed"
    assert stored.error.startswith("No Spotify metadata")
    assert spotdl.downloads == []


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
