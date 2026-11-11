import sqlite3

from keo.library import Library
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


def test_enqueue_stores_the_pull_and_keeps_it_on_the_active_job(library):
    first = library.enqueue("vid1", "A", "B", source="manual", format="opus", premium=1)
    second = library.enqueue("vid1", "A", "B", source="manual", format="mp3", premium=0)
    assert (first.format, first.premium) == ("opus", 1)
    assert (second.format, second.premium) == ("opus", 1)


def test_old_database_leaves_pull_facts_null(tmp_path):
    import sqlite3

    db = tmp_path / "keo.db"
    connection = sqlite3.connect(db)
    connection.execute(
        "CREATE TABLE jobs (id INTEGER PRIMARY KEY, video_id TEXT, artist TEXT, title TEXT,"
        " spotify_url TEXT, spotify_id TEXT, status TEXT, source TEXT, error TEXT,"
        " created_at TEXT, updated_at TEXT)"
    )
    connection.execute(
        "INSERT INTO jobs (video_id, artist, title, status, source, created_at, updated_at)"
        " VALUES ('vid1', 'A', 'Old', 'queued', 'manual', '2026-11-07T15:00:00+00:00',"
        " '2026-11-07T15:00:00+00:00')"
    )
    connection.commit()
    connection.close()
    job = Library(db, tmp_path / "archive.txt").get_job(1)
    assert (job.format, job.premium) == (None, None)


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


def test_count_recent_counts_today_yesterday_and_this_week(library):
    items = [
        HistoryItem("vid1", "A", "B", "Today"),
        HistoryItem("vid2", "C", "D", "Yesterday"),
        HistoryItem("vid3", "E", "F", "This week"),
        HistoryItem("vid4", "G", "H", "Last week"),
        HistoryItem("vid5", "I", "J", "May 2025"),
    ]
    pull_id = library.record_pull("2026-11-07T23:00:00+07:00", "ok", None, items)
    counts = {count.video_id: count.plays for count in library.count_recent(pull_id)}
    assert counts == {"vid1": 1, "vid2": 1, "vid3": 1}
    assert "vid4" not in counts
    assert "vid5" not in counts


def test_failed_pull_is_not_an_ok_pull(library):
    library.record_pull("2026-11-07T23:00:00+07:00", "failed", "offline", [])
    assert not library.has_ok_pull_on("2026-11-07")


def test_dismiss_removes_from_review_and_does_not_blacklist(library):
    library.add_review("vid1", "Artist", "Title", reason="weak_match")
    assert library.get_review("vid1") is not None
    assert not library.is_dismissed("vid1")

    library.dismiss("vid1")
    assert library.get_review("vid1") is None
    assert not library.is_dismissed("vid1")


def test_add_review_supports_close_match_reason_and_migrates_old_table(tmp_path):
    import sqlite3

    db = tmp_path / "keo.db"
    conn = sqlite3.connect(db)
    conn.execute(
        "CREATE TABLE review (video_id TEXT PRIMARY KEY, heard_artist TEXT NOT NULL,"
        " heard_title TEXT NOT NULL, candidate_artist TEXT, candidate_title TEXT,"
        " spotify_url TEXT, spotify_id TEXT,"
        " reason TEXT NOT NULL CHECK (reason IN ('weak_match', 'no_metadata')),"
        " created_at TEXT NOT NULL)"
    )
    conn.execute(
        "INSERT INTO review VALUES ('vid1', 'A', 'B', NULL, NULL, NULL, NULL, 'no_metadata', '2026-11-10T00:00:00+00:00')"
    )
    conn.commit()
    conn.close()

    library = Library(db, tmp_path / "archive.txt")
    assert library.get_review("vid1").reason == "no_metadata"
    library.add_review(
        "vid2", "Artist", "Title",
        reason="close_match", candidate_artist="Artist", candidate_title="Title",
        spotify_url="https://open.spotify.com/track/sp2", spotify_id="sp2",
    )
    row = library.get_review("vid2")
    assert row.reason == "close_match"
    assert row.candidate_title == "Title"


def test_add_review_supports_pending_reason(library):
    library.add_review("vid_pending", "Artist", "Title", reason="pending")
    row = library.get_review("vid_pending")
    assert row is not None
    assert row.reason == "pending"


def test_jobs_supports_youtube_source_and_migrates_old_table(tmp_path):
    db = tmp_path / "legacy.db"
    conn = sqlite3.connect(db)
    conn.execute(
        "CREATE TABLE jobs ("
        " id INTEGER PRIMARY KEY, video_id TEXT NOT NULL, artist TEXT NOT NULL, title TEXT NOT NULL,"
        " spotify_url TEXT, spotify_id TEXT,"
        " status TEXT NOT NULL CHECK (status IN ('queued', 'running', 'succeeded', 'failed')),"
        " source TEXT NOT NULL CHECK (source IN ('manual', 'history', 'review', 'library')),"
        " error TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL)"
    )
    conn.execute(
        "INSERT INTO jobs VALUES (1, 'v1', 'A', 'B', NULL, NULL, 'queued', 'manual', NULL, '2026-11-10T00:00:00', '2026-11-10T00:00:00')"
    )
    conn.commit()
    conn.close()

    library = Library(db, tmp_path / "archive.txt")
    job = library.enqueue("v2", "A2", "B2", source="youtube")
    assert job.source == "youtube"
    assert library.get_job(1).source == "manual"

