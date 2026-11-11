from datetime import datetime
from zoneinfo import ZoneInfo

from keo.history import Scheduler, is_due, run_pull
from keo.records import HistoryItem
from tests.fakes import QUERY, candidate

ZONE = ZoneInfo("Asia/Ho_Chi_Minh")
NIGHT = datetime(2026, 11, 7, 23, 0, tzinfo=ZONE)
URL = "https://open.spotify.com/track/sp1"


def plays(video_id="vid1", artist="Sơn Tùng M-TP", title="Lạc Trôi", count=3, label="Today"):
    return [HistoryItem(video_id, artist, title, label)] * count


def pull(catalog, library, worker, spotdl, now=NIGHT):
    return run_pull(
        catalog=catalog, library=library, worker=worker, spotdl=spotdl,
        cookies=worker.cookies, now=now,
    )


def test_close_match_played_once_goes_to_review(catalog, library, worker, spotdl):
    catalog.history_items = plays(count=1)
    spotdl.candidates[QUERY] = candidate(name="Lạc Trôi (feat. Triple D)", artist="SƠN TÙNG M-TP")
    pull(catalog, library, worker, spotdl)
    assert library.active_job("vid1") is None
    row = library.get_review("vid1")
    assert row is not None
    assert (row.reason, row.spotify_url) == ("close_match", URL)


def test_single_play_today_qualifies_for_lookup(catalog, library, worker, spotdl):
    catalog.history_items = plays(count=1)
    spotdl.candidates[QUERY] = candidate(name="Lạc Trôi (feat. Triple D)", artist="SƠN TÙNG M-TP")
    pull(catalog, library, worker, spotdl)
    assert library.get_review("vid1") is not None


def test_plays_from_older_shelves_do_not_count(catalog, library, worker, spotdl):
    catalog.history_items = plays(count=5, label="Last week")
    pull(catalog, library, worker, spotdl)
    assert spotdl.lookups == []


def test_plays_from_yesterday_and_this_week_qualify(catalog, library, worker, spotdl):
    catalog.history_items = [
        HistoryItem("vid1", "Sơn Tùng M-TP", "Lạc Trôi", "Yesterday"),
        HistoryItem("vid2", "Đen Vâu", "Trốn Tìm", "This week"),
    ]
    pull(catalog, library, worker, spotdl)
    assert spotdl.lookups == ["Sơn Tùng M-TP - Lạc Trôi", "Đen Vâu - Trốn Tìm"]


def test_run_pull_logs_activity(catalog, library, worker, spotdl, caplog):
    import logging
    caplog.set_level(logging.INFO)
    catalog.history_items = [
        HistoryItem("vid1", "Sơn Tùng M-TP", "Lạc Trôi", "Yesterday"),
    ]
    pull(catalog, library, worker, spotdl)
    assert "Starting history pull" in caplog.text
    assert "Fetched 1 history items" in caplog.text
    assert "Looking up 'Sơn Tùng M-TP - Lạc Trôi' (vid1) on Spotify" in caplog.text
    assert "History pull completed" in caplog.text


def test_weak_match_goes_to_review(catalog, library, worker, spotdl):
    catalog.history_items = plays()
    spotdl.candidates[QUERY] = candidate(name="Một Bài Khác", artist="Ai Đó")
    pull(catalog, library, worker, spotdl)
    row = library.get_review("vid1")
    assert (row.reason, row.candidate_title, row.spotify_id) == ("weak_match", "Một Bài Khác", "sp1")
    assert library.active_job("vid1") is None


def test_no_metadata_goes_to_review(catalog, library, worker, spotdl):
    catalog.history_items = plays()
    pull(catalog, library, worker, spotdl)
    row = library.get_review("vid1")
    assert (row.reason, row.spotify_url) == ("no_metadata", None)


def test_owned_song_is_skipped(catalog, library, worker, spotdl):
    library.mark_owned("vid1", "Sơn Tùng M-TP", "Lạc Trôi", "sp1")
    catalog.history_items = plays()
    pull(catalog, library, worker, spotdl)
    assert spotdl.lookups == []


def test_song_with_an_active_job_is_skipped(catalog, library, worker, spotdl):
    worker.submit("vid1", "Sơn Tùng M-TP", "Lạc Trôi")
    catalog.history_items = plays()
    pull(catalog, library, worker, spotdl)
    assert spotdl.lookups == []


def test_one_song_failing_does_not_stop_the_rest(catalog, library, worker, spotdl):
    catalog.history_items = plays() + plays("vid2", "Đen", "Bài Này Chill Phết")
    spotdl.fail_queries.add(QUERY)
    spotdl.candidates["Đen - Bài Này Chill Phết"] = candidate(
        name="Bài Này Chill Phết", artist="Đen", song_id="sp2"
    )
    pull(catalog, library, worker, spotdl)
    assert library.get_review("vid1") is None
    assert library.get_review("vid2") is not None


def test_failed_history_call_changes_nothing(catalog, library, worker, spotdl):
    library.add_review("vid9", "A", "B", reason="no_metadata")
    catalog.fail = True
    assert pull(catalog, library, worker, spotdl) is None
    assert [row.video_id for row in library.list_review()] == ["vid9"]
    assert library.list_jobs() == []
    assert not library.has_ok_pull_on("2026-11-07")


def test_existing_review_row_is_kept_on_another_weak_match(catalog, library, worker, spotdl):
    library.add_review("vid1", "Sơn Tùng M-TP", "Lạc Trôi", reason="no_metadata")
    catalog.history_items = plays()
    spotdl.candidates[QUERY] = candidate(name="Một Bài Khác", artist="Ai Đó")
    pull(catalog, library, worker, spotdl)
    assert library.get_review("vid1").reason == "no_metadata"


def test_close_match_review_downloads_and_clears_review(catalog, library, worker, spotdl):
    library.add_review("vid1", "Sơn Tùng M-TP", "Lạc Trôi", reason="weak_match")
    worker.submit("vid1", "Sơn Tùng M-TP", "Lạc Trôi", spotify_url=URL, spotify_id="sp1")
    worker.run_once()
    assert library.get_review("vid1") is None
    assert library.is_owned("vid1", "", "")


def test_dismissed_song_is_skipped(catalog, library, worker, spotdl):
    library.dismiss("vid1")
    catalog.history_items = plays(count=1)
    pull(catalog, library, worker, spotdl)
    assert spotdl.lookups == []
    assert library.get_review("vid1") is None


def test_not_due_before_the_hour(library):
    assert not is_due(NIGHT.replace(hour=22, minute=59), 23, library, None)


def test_due_at_the_hour_and_as_a_catch_up_later_that_night(library):
    assert is_due(NIGHT, 23, library, None)
    assert is_due(NIGHT.replace(minute=45), 23, library, None)


def test_not_due_after_an_ok_pull_today(library):
    library.record_pull(NIGHT.isoformat(), "ok", None, [])
    assert not is_due(NIGHT.replace(minute=30), 23, library, None)


def test_not_retried_the_same_day_after_an_attempt(library):
    assert not is_due(NIGHT, 23, library, NIGHT.date())


def test_scheduler_runs_once_per_day(catalog, library, worker, spotdl):
    clock = [NIGHT]
    scheduler = Scheduler(
        catalog=catalog, library=library, worker=worker, spotdl=spotdl,
        cookies=worker.cookies,
        timezone="Asia/Ho_Chi_Minh", hour=23, clock=lambda: clock[0],
    )
    assert scheduler.tick()
    assert not scheduler.tick()
    clock[0] = datetime(2026, 11, 8, 23, 5, tzinfo=ZONE)
    assert scheduler.tick()


def test_scheduler_waits_for_the_next_day_after_a_failed_pull(catalog, library, worker, spotdl):
    scheduler = Scheduler(
        catalog=catalog, library=library, worker=worker, spotdl=spotdl,
        cookies=worker.cookies,
        timezone="Asia/Ho_Chi_Minh", hour=23, clock=lambda: NIGHT,
    )
    catalog.fail = True
    assert scheduler.tick()
    catalog.fail = False
    assert not scheduler.tick()
