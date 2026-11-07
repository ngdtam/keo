from keo.records import HistoryItem

URL = "https://open.spotify.com/track/sp1"


def test_owned_by_video_id(library):
    library.mark_owned("vid1", "Sơn Tùng M-TP", "Lạc Trôi", "sp1")
    assert library.is_owned("vid1", "Someone", "Else")


def test_owned_by_normalized_name(library):
    library.mark_owned("vid1", "Sơn Tùng M-TP", "Lạc Trôi", "sp1")
    assert library.is_owned("vid2", "SƠN TÙNG M-TP", "Lạc Trôi (feat. Triple D)")
    assert not library.is_owned("vid3", "Sơn Tùng M-TP", "Chúng Ta Của Hiện Tại")


def test_owned_by_spotify_id_in_songs_or_archive(library, tmp_path):
    library.mark_owned("vid1", "A", "B", "sp1")
    assert library.is_owned_spotify("sp1")
    assert not library.is_owned_spotify("sp2")
    (tmp_path / "archive.txt").write_text(
        "https://open.spotify.com/track/sp2\n", encoding="utf-8"
    )
    assert library.is_owned_spotify("sp2")


def test_mark_owned_clears_the_review_row(library):
    library.add_review("vid1", "A", "B", reason="no_metadata")
    library.mark_owned("vid1", "A", "B", "sp1")
    assert library.get_review("vid1") is None


def test_enqueue_reuses_the_active_job(library):
    first = library.enqueue("vid1", "A", "B", source="manual")
    second = library.enqueue("vid1", "A", "B", source="history")
    assert first.status == "queued"
    assert second.id == first.id
    assert [job.id for job in library.list_jobs()] == [first.id]


def test_enqueue_after_finish_creates_a_new_job(library):
    first = library.enqueue("vid1", "A", "B", source="manual")
    library.finish(first.id, ok=False, error="boom")
    second = library.enqueue("vid1", "A", "B", source="manual")
    assert second.id != first.id
    assert [job.id for job in library.list_jobs()] == [second.id, first.id]


def test_claim_next_takes_the_oldest_queued_job(library):
    older = library.enqueue("vid1", "A", "B", source="manual")
    newer = library.enqueue("vid2", "C", "D", source="manual")
    claimed = library.claim_next()
    assert (claimed.id, claimed.status) == (older.id, "running")
    assert library.claim_next().id == newer.id
    assert library.claim_next() is None


def test_finish_records_the_outcome(library):
    job = library.enqueue("vid1", "A", "B", source="manual")
    library.claim_next()
    library.set_candidate(job.id, URL, "sp1")
    library.finish(job.id, ok=False, error="spotDL said no")
    stored = library.get_job(job.id)
    assert (stored.status, stored.error, stored.spotify_url, stored.spotify_id) == (
        "failed", "spotDL said no", URL, "sp1"
    )
    assert not library.is_owned("vid1", "A", "B")


def test_retry_requeues_a_failed_job(library):
    job = library.enqueue("vid1", "A", "B", source="manual", spotify_url=URL, spotify_id="sp1")
    library.claim_next()
    library.finish(job.id, ok=False, error="boom")
    retried = library.retry(job.id)
    assert (retried.id, retried.status, retried.error, retried.spotify_url) == (
        job.id, "queued", None, URL
    )
    assert library.retry(999) is None


def test_requeue_running_after_a_restart(library):
    job = library.enqueue("vid1", "A", "B", source="manual")
    library.claim_next()
    library.requeue_running()
    assert library.get_job(job.id).status == "queued"


def test_add_review_keeps_or_replaces(library):
    library.add_review("vid1", "A", "B", reason="no_metadata")
    weak = dict(
        reason="weak_match", candidate_artist="A2", candidate_title="B2",
        spotify_url=URL, spotify_id="sp1",
    )
    library.add_review("vid1", "A", "B", **weak)
    assert library.get_review("vid1").reason == "no_metadata"
    library.add_review("vid1", "A", "B", replace=True, **weak)
    row = library.get_review("vid1")
    assert (row.reason, row.candidate_title, row.spotify_url) == ("weak_match", "B2", URL)
    assert [row.video_id for row in library.list_review()] == ["vid1"]


def test_count_today_counts_only_today(library):
    items = [HistoryItem("vid1", "A", "B", "Today")] * 3 + [
        HistoryItem("vid2", "C", "D", "Today"),
        HistoryItem("vid2", "C", "D", "Yesterday"),
    ]
    pull_id = library.record_pull("2026-11-07T23:00:00+07:00", "ok", None, items)
    counts = {count.video_id: count.plays for count in library.count_today(pull_id)}
    assert counts == {"vid1": 3, "vid2": 1}
    assert library.has_ok_pull_on("2026-11-07")
    assert not library.has_ok_pull_on("2026-11-08")


def test_failed_pull_is_not_an_ok_pull(library):
    library.record_pull("2026-11-07T23:00:00+07:00", "failed", "offline", [])
    assert not library.has_ok_pull_on("2026-11-07")
