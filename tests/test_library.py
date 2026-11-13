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


def test_owned_by_job_linked_to_spotify_song(library):
    library.mark_owned("vid_sp", "Artist", "Title", "sp1")
    job = library.enqueue("vid_job", "Artist", "Title", source="manual", spotify_id="sp1")
    assert library.is_owned("vid_job", "Different", "Other")


def test_owned_by_artist_overlap(library):
    library.mark_owned("vid1", "Osad, Hà An Huy", "Như Một Người Rưng", "sp1")
    assert library.is_owned("vid_new1", "OSAD", "Như Một Người Rưng")
    assert library.is_owned("vid_new2", "Hà An Huy", "Như Một Người Rưng")
    assert not library.is_owned("vid_new3", "Trúc Nhân", "Như Một Người Rưng")


def test_owned_by_audio_path_filename_artist(library):
    library.mark_owned("vid1", "Hà An Huy", "Như Một Người Rưng", "sp1")
    library.set_audio_path("sp1", "/music/Osad, Hà An Huy - Như Một Người Rưng.opus")
    assert library.is_owned("vid_new", "OSAD", "Như Một Người Rưng")


def test_same_title_different_artist_not_owned(library):
    library.mark_owned("vid1", "Adele", "Hello", "sp_adele")
    assert not library.is_owned("vid_lionel", "Lionel Richie", "Hello")


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


def test_fresh_db_sets_user_version_and_columns(tmp_path):
    from keo.library import LATEST_VERSION

    db_path = tmp_path / "fresh.db"
    Library(db_path, tmp_path / "archive.txt")
    con = sqlite3.connect(db_path)
    version = con.execute("PRAGMA user_version").fetchone()[0]
    cols = {row[1] for row in con.execute("PRAGMA table_info(songs)")}
    assert version == LATEST_VERSION
    assert {"audio_mtime", "audio_size", "audio_card", "audio_details", "has_cover"} <= cols


def test_migrates_v0_db_preserving_rows(tmp_path):
    db_path = tmp_path / "old.db"
    con = sqlite3.connect(db_path)
    con.executescript(
        """
        CREATE TABLE songs (
            id INTEGER PRIMARY KEY, video_id TEXT UNIQUE, artist_key TEXT NOT NULL,
            title_key TEXT NOT NULL, spotify_id TEXT UNIQUE, artist TEXT NOT NULL,
            title TEXT NOT NULL, UNIQUE (artist_key, title_key)
        );
        INSERT INTO songs (video_id, artist_key, title_key, spotify_id, artist, title)
        VALUES ('v1', 'a', 'b', 'sp1', 'A', 'B');
        """
    )
    con.close()
    lib = Library(db_path, tmp_path / "archive.txt")
    songs = lib.list_songs()
    assert len(songs) == 1
    assert songs[0].spotify_id == "sp1"
    from keo.library import LATEST_VERSION

    assert sqlite3.connect(db_path).execute("PRAGMA user_version").fetchone()[0] == LATEST_VERSION


def test_scanned_song_cache_and_missing_cleanup(library):
    song = library.upsert_scanned_song("Artist", "Track", "/music/Artist - Track.mp3")
    library.set_audio_cache(
        song.id,
        audio_path="/music/Artist - Track.mp3",
        mtime=123.5,
        size=456,
        card="MP3 · 320 kbps",
        details=(("Format", "MPEG Audio"),),
        has_cover=True,
    )
    loaded = library.get_song(song.id)
    assert loaded is not None
    assert loaded.audio_card == "MP3 · 320 kbps"
    assert loaded.audio_details == (("Format", "MPEG Audio"),)
    assert loaded.has_cover is True
    assert library.clear_missing_audio(set()) == 1
    cleared = library.get_song(song.id)
    assert cleared is not None
    assert cleared.audio_path is None
    assert cleared.audio_card is None


def test_housekeep_trims_old_history_and_finished_jobs(library):
    from datetime import datetime, timezone
    library.record_pull("2026-09-01T00:00:00+00:00", "ok", None, [HistoryItem("v1", "A", "B", "Today")])
    library.record_pull("2026-11-10T00:00:00+00:00", "ok", None, [HistoryItem("v2", "A", "C", "Today")])
    for i in range(4):
        job = library.enqueue(f"v{i}", "A", f"T{i}", source="manual")
        if i < 3:
            library.finish(job.id, ok=True)

    pulls_deleted, jobs_deleted = library.housekeep(
        keep_days=30, keep_jobs=1, now=datetime(2026, 11, 12, tzinfo=timezone.utc)
    )
    assert pulls_deleted == 1
    assert jobs_deleted == 2
    assert len(library.list_jobs()) == 2  # 1 kept finished + 1 still queued


def test_record_and_list_events(library):
    library.record_event("Low G - An Thần", "downloaded", "manual")
    library.record_event("Đen - Lối Nhỏ", "deleted", "library")
    events = library.list_events()
    assert len(events) == 2
    assert events[0].song == "Đen - Lối Nhỏ"
    assert events[0].kind == "deleted"
    assert events[0].detail == "library"
    assert events[1].song == "Low G - An Thần"
    assert events[1].kind == "downloaded"

