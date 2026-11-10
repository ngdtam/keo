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


def test_close_match_played_once_is_queued(catalog, library, worker, spotdl):
    catalog.history_items = plays(count=1)
    spotdl.candidates[QUERY] = candidate(name="Lạc Trôi (feat. Triple D)", artist="SƠN TÙNG M-TP")
    pull(catalog, library, worker, spotdl)
    job = library.active_job("vid1")
    assert (job.source, job.spotify_url) == ("history", URL)
    assert library.get_review("vid1") is None


def test_single_play_today_qualifies_for_lookup(catalog, library, worker, spotdl):
    catalog.history_items = plays(count=1)
    spotdl.candidates[QUERY] = candidate(name="Lạc Trôi (feat. Triple D)", artist="SƠN TÙNG M-TP")
    pull(catalog, library, worker, spotdl)
    assert library.active_job("vid1") is not None


def test_plays_from_other_days_do_not_count(catalog, library, worker, spotdl):
    catalog.history_items = plays(count=5, label="Yesterday")
    pull(catalog, library, worker, spotdl)
    assert spotdl.lookups == []


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
    assert library.active_job("vid1") is None
    assert library.active_job("vid2") is not None


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


def test_close_match_later_clears_review_once_owned(catalog, library, worker, spotdl):
    library.add_review("vid1", "Sơn Tùng M-TP", "Lạc Trôi", reason="weak_match")
    catalog.history_items = plays()
    spotdl.candidates[QUERY] = candidate()
    pull(catalog, library, worker, spotdl)
    worker.run_once()
    assert library.get_review("vid1") is None
    assert library.is_owned("vid1", "", "")


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


import logging

from keo.config import SpotdlSettings
from keo.cookies import MISSING_LINE
from keo.history import decide


def test_a_close_match_is_not_queued_when_the_cookie_is_refused(
    catalog, library, worker, spotdl, caplog
):
    spotdl.candidates[QUERY] = candidate(name="Lạc Trôi (feat. Triple D)", artist="SƠN TÙNG M-TP")
    spotdl.settings = SpotdlSettings(format="opus")
    with caplog.at_level(logging.WARNING):
        result = decide(
            "vid1", "Sơn Tùng M-TP", "Lạc Trôi",
            library=library, worker=worker, spotdl=spotdl, cookies=worker.cookies,
        )
    assert result == "cookie"
    assert MISSING_LINE in caplog.text
    assert library.active_job("vid1") is None
    assert library.get_review("vid1") is None
    assert library.list_jobs() == []
