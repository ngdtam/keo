import pytest

from keo.records import HistoryItem
from keo.web.routes_queue import format_job_time
from tests.fakes import QUERY, candidate

URL = "https://open.spotify.com/track/sp1"


def add_weak_row(library):
    library.add_review(
        "vid1", "Sơn Tùng M-TP", "Lạc Trôi",
        reason="weak_match", candidate_artist="Ai Đó", candidate_title="Một Bài Khác",
        spotify_url=URL, spotify_id="sp1",
    )


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("get", "/api/review"),
        ("get", "/api/jobs"),
        ("post", "/api/review/vid1/download"),
        ("post", "/api/jobs/1/retry"),
    ],
)
def test_queue_api_needs_the_token(client, method, path):
    assert getattr(client, method)(path).status_code == 401


def test_review_api_lists_rows(client, auth, library):
    add_weak_row(library)
    rows = client.get("/api/review", headers=auth).json()
    assert [(r["video_id"], r["reason"], r["spotify_url"]) for r in rows] == [
        ("vid1", "weak_match", URL)
    ]


def test_review_tap_downloads_the_stored_candidate_without_a_new_lookup(client, auth, library, spotdl):
    add_weak_row(library)
    response = client.post("/api/review/vid1/download", headers=auth)
    assert response.status_code == 200
    job = response.json()["job"]
    assert (job["source"], job["spotify_url"]) == ("review", URL)
    assert spotdl.lookups == []


def test_review_tap_without_spotify_url_enqueues_review_job(client, auth, library):
    library.add_review("vid1", "Sơn Tùng M-TP", "Lạc Trôi", reason="pending")
    response = client.post("/api/review/vid1/download", headers=auth)
    assert response.status_code == 200
    job = response.json()["job"]
    assert job["source"] == "review"
    assert job["video_id"] == "vid1"


def test_unknown_review_row_is_404(client, auth):
    assert client.post("/api/review/nope/download", headers=auth).status_code == 404


def test_jobs_api_lists_newest_first(client, auth, worker):
    worker.submit("vid1", "Sơn Tùng M-TP", "Lạc Trôi")
    worker.submit("vid2", "Đen", "Bài Này Chill Phết")
    jobs = client.get("/api/jobs", headers=auth).json()
    assert [job["video_id"] for job in jobs] == ["vid2", "vid1"]


def test_retry_a_failed_job(client, auth, worker, library):
    job = worker.submit("vid1", "Sơn Tùng M-TP", "Lạc Trôi")
    library.claim_next()
    library.finish(job.id, ok=False, error="boom")
    response = client.post(f"/api/jobs/{job.id}/retry", headers=auth)
    assert response.status_code == 200
    assert response.json()["job"]["status"] == "queued"


def test_retry_an_unknown_job_is_404(client, auth):
    assert client.post("/api/jobs/999/retry", headers=auth).status_code == 404


def test_job_time_uses_the_configured_zone():
    assert format_job_time("2026-11-07T15:00:00+00:00", "Asia/Ho_Chi_Minh") == "2026-11-07, 22:00"


def test_jobs_page_shows_a_new_pull(logged_in, library):
    job = library.enqueue("vid9", "Đen", "Pulled", source="manual", format="opus", premium=1)
    text = logged_in.get(f"/library/video/{job.video_id}").text
    assert format_job_time(job.updated_at, "Asia/Ho_Chi_Minh") in text
    assert "opus · .opus · Premium" in text


def test_jobs_page_hides_pull_facts_when_they_were_not_stored(logged_in, library):
    job = library.enqueue("vid8", "Đen", "Old Song", source="manual")
    text = logged_in.get(f"/library/video/{job.video_id}").text
    assert format_job_time(job.updated_at, "Asia/Ho_Chi_Minh") in text
    assert "Premium" not in text
    assert ".mp3" not in text


def test_review_and_jobs_pages(logged_in, library, worker):
    add_weak_row(library)
    worker.submit("vid2", "Đen", "Bài Này Chill Phết")
    review = logged_in.get("/review")
    assert review.status_code == 200
    assert "Lạc Trôi" in review.text
    jobs = logged_in.get("/jobs", follow_redirects=False)
    assert jobs.status_code == 303
    assert jobs.headers["location"] == "/library"
    assert "Bài Này Chill Phết" in logged_in.get("/library").text


from keo.config import SpotdlSettings
from keo.cookies import EXPIRED_LINE, FAILED_LINE, FREE_LINE, MISSING_LINE, ProbeResult

BODY = {"video_id": "vid1", "title": "Lạc Trôi", "artists": ["Sơn Tùng M-TP"]}


def _cookie(tmp_path):
    path = tmp_path / "cookies.txt"
    path.write_text("a", encoding="utf-8")
    return path


def test_a_missing_cookie_is_409(client, auth, library, spotdl, probe):
    spotdl.settings = SpotdlSettings(format="opus")
    response = client.post("/api/downloads", json=BODY, headers=auth)
    assert response.status_code == 409
    assert response.json()["detail"] == MISSING_LINE
    assert library.list_jobs() == []
    assert probe.calls == []


def test_an_expired_cookie_is_409_and_the_next_tap_does_not_probe(
    client, auth, library, spotdl, probe, tmp_path
):
    path = _cookie(tmp_path)
    spotdl.settings = SpotdlSettings(format="opus", cookie_file=str(path))
    probe.results.append(ProbeResult([{"format_id": "141"}], "cookies are no longer valid"))
    first = client.post("/api/downloads", json=BODY, headers=auth)
    second = client.post("/api/downloads", json=BODY, headers=auth)
    assert first.status_code == 409
    assert second.status_code == 409
    assert first.json()["detail"] == EXPIRED_LINE
    assert second.json()["detail"] == EXPIRED_LINE
    assert library.list_jobs() == []
    assert probe.calls == [str(path)]


def test_a_free_account_is_409(client, auth, library, spotdl, probe, tmp_path):
    path = _cookie(tmp_path)
    spotdl.settings = SpotdlSettings(format="m4a", cookie_file=str(path))
    probe.results.append(ProbeResult([{"format_id": "251"}], ""))
    response = client.post("/api/downloads", json=BODY, headers=auth)
    assert response.status_code == 409
    assert response.json()["detail"] == FREE_LINE
    assert library.list_jobs() == []
    assert probe.calls == [str(path)]


def test_an_unknown_probe_is_409(client, auth, library, spotdl, probe, tmp_path):
    path = _cookie(tmp_path)
    spotdl.settings = SpotdlSettings(format="opus", bitrate="disable", cookie_file=str(path))
    probe.results.append(ProbeResult(None, "timed out"))
    response = client.post("/api/downloads", json=BODY, headers=auth)
    assert response.status_code == 409
    assert response.json()["detail"] == FAILED_LINE
    assert library.list_jobs() == []
    assert probe.calls == [str(path)]


def test_a_free_download_queues_without_a_probe(client, auth, library, spotdl, probe, tmp_path):
    path = tmp_path / "cookies.txt"
    path.write_text("a", encoding="utf-8")
    spotdl.settings = SpotdlSettings(format="ogg", cookie_file=str(path))
    response = client.post("/api/downloads", json=BODY, headers=auth)
    assert response.status_code == 200
    assert response.json()["job"]["status"] == "queued"
    assert probe.calls == []
    assert len(library.list_jobs()) == 1


def test_review_download_refuses_before_it_queues(client, auth, library, spotdl):
    add_weak_row(library)
    spotdl.settings = SpotdlSettings(format="opus")
    response = client.post("/api/review/vid1/download", headers=auth)
    assert response.status_code == 409
    assert response.json()["detail"] == MISSING_LINE
    assert library.active_job("vid1") is None
    assert library.get_review("vid1") is not None


def test_library_and_job_retry_refuse_and_leave_the_failed_job(client, auth, library, spotdl):
    spotdl.settings = SpotdlSettings(format="opus")
    job = library.enqueue(
        "vid1", "A", "Song", source="manual", spotify_id="sp1", spotify_url=URL,
    )
    library.finish(job.id, ok=False, error="boom")
    library.mark_owned("vid1", "A", "Song", "sp1")
    library._archive_path.write_text(URL + "\n")
    spotify = client.post("/api/library/spotify/sp1/download", headers=auth)
    assert spotify.status_code == 409
    assert spotify.json()["detail"] == MISSING_LINE
    assert library.has_spotify("sp1")
    assert library.is_owned_spotify("sp1")
    assert URL in library._archive_path.read_text()
    assert library.get_job(job.id).status == "failed"
    video = library.enqueue("vid2", "Đen", "No Match", source="manual")
    library.finish(video.id, ok=False, error="boom")
    library.mark_owned("vid2", "Đen", "No Match", None)
    video_response = client.post("/api/library/video/vid2/download", headers=auth)
    assert video_response.status_code == 409
    assert video_response.json()["detail"] == MISSING_LINE
    assert library.is_owned("vid2", "Đen", "No Match")
    assert library.get_job(video.id).status == "failed"
    retry = client.post(f"/api/jobs/{job.id}/retry", headers=auth)
    assert retry.status_code == 409
    assert retry.json()["detail"] == MISSING_LINE
    assert library.get_job(job.id).status == "failed"
    assert library.get_job(video.id).status == "failed"


def test_history_pull_requires_token(client):
    assert client.post("/api/history/pull").status_code == 401


def test_history_status_requires_token(client):
    assert client.get("/api/history/status").status_code == 401


def test_history_status_returns_progress(client, auth):
    services = client.app.state.services
    services.history_progress.start_processing(10)
    services.history_progress.step()
    response = client.get("/api/history/status", headers=auth)
    assert response.status_code == 200
    data = response.json()
    assert data["active"] is True
    assert data["stage"] == "processing"
    assert data["current"] == 1
    assert data["total"] == 10
    assert data["percent"] == 10
    services.history_progress.finish()


def test_history_pull_triggers_background_pull(client, auth, catalog, library, worker, spotdl, caplog):
    import logging
    caplog.set_level(logging.INFO)
    catalog.history_items = [HistoryItem("vid1", "Sơn Tùng M-TP", "Lạc Trôi", "Today")]
    spotdl.candidates[QUERY] = candidate()
    response = client.post("/api/history/pull", headers=auth)
    assert response.status_code == 202
    assert response.json() == {"status": "checking"}
    assert library.get_review("vid1") is not None
    assert "Manual history pull requested via API" in caplog.text


def test_review_page_renders_check_history_button(logged_in):
    response = logged_in.get("/review")
    assert response.status_code == 200
    assert 'data-post="/api/history/pull"' in response.text
    assert "Check history now" in response.text


def test_keo_js_contains_history_and_job_polling(client):
    response = client.get("/static/keo.js")
    assert response.status_code == 200
    assert "pollHistoryStatus" in response.text
    assert "pollJobs" in response.text
    assert "/api/history/status" in response.text


def test_dismiss_review_requires_token(client):
    assert client.post("/api/review/vid1/dismiss").status_code == 401


def test_dismiss_review_removes_item(client, auth, library):
    library.add_review("vid1", "Artist", "Title", reason="weak_match")
    response = client.post("/api/review/vid1/dismiss", headers=auth)
    assert response.status_code == 200
    assert response.json() == {"status": "dismissed"}
    assert library.get_review("vid1") is None


def test_review_page_renders_dismiss_button_and_status(logged_in, library):
    library.add_review("vid1", "Sơn Tùng M-TP", "Lạc Trôi", reason="close_match", spotify_url=URL)
    response = logged_in.get("/review")
    assert response.status_code == 200
    assert 'data-dismiss="/api/review/vid1/dismiss"' in response.text
    assert "Dismiss" in response.text
    assert "Not in library" in response.text
    assert "status-dot bg-red" in response.text


def test_review_page_renders_download_and_dismiss_buttons(logged_in, library):
    library.add_review("vid_pending", "Low G", "Simp Gái 808", reason="pending")
    response = logged_in.get("/review")
    assert response.status_code == 200
    assert 'data-post="/api/review/vid_pending/download"' in response.text
    assert 'data-dismiss="/api/review/vid_pending/dismiss"' in response.text
    assert "Download" in response.text
    assert "Dismiss" in response.text


def test_review_download_youtube_enqueues_job(client, auth, library):
    library.add_review("vid_yt1", "Artist", "Song", reason="no_metadata")
    res = client.post("/api/review/vid_yt1/download-youtube", headers=auth)
    assert res.status_code == 200
    data = res.json()
    assert data["owned"] is False
    assert data["job"]["source"] == "youtube"
    assert data["job"]["video_id"] == "vid_yt1"


def test_review_download_youtube_404_when_not_in_review(client, auth):
    res = client.post("/api/review/nonexistent/download-youtube", headers=auth)
    assert res.status_code == 404


def test_review_dismiss_permanent_records_dismissed(client, auth, library):
    library.add_review("vid_perm", "Artist", "Song", reason="pending")
    res = client.post("/api/review/vid_perm/dismiss?permanent=1", headers=auth)
    assert res.status_code == 200
    assert library.get_review("vid_perm") is None
    assert library.is_dismissed("vid_perm")

