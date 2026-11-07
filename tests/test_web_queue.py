import pytest

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


def test_no_metadata_review_tap_looks_up_again(client, auth, library, spotdl):
    library.add_review("vid1", "Sơn Tùng M-TP", "Lạc Trôi", reason="no_metadata")
    spotdl.candidates[QUERY] = candidate()
    response = client.post("/api/review/vid1/download", headers=auth)
    assert response.status_code == 202
    assert response.json() == {"status": "checking"}
    assert library.active_job("vid1").source == "review"


def test_no_metadata_review_tap_with_a_weak_result_updates_the_row(client, auth, library, spotdl):
    library.add_review("vid1", "Sơn Tùng M-TP", "Lạc Trôi", reason="no_metadata")
    spotdl.candidates[QUERY] = candidate(name="Một Bài Khác", artist="Ai Đó")
    client.post("/api/review/vid1/download", headers=auth)
    row = library.get_review("vid1")
    assert (row.reason, row.spotify_url) == ("weak_match", URL)
    assert library.active_job("vid1") is None


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


def test_review_and_jobs_pages(logged_in, library, worker):
    add_weak_row(library)
    worker.submit("vid2", "Đen", "Bài Này Chill Phết")
    review = logged_in.get("/review")
    assert review.status_code == 200
    assert "Một Bài Khác" in review.text
    jobs = logged_in.get("/jobs")
    assert jobs.status_code == 200
    assert "Bài Này Chill Phết" in jobs.text
