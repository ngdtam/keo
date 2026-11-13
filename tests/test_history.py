from datetime import datetime
from zoneinfo import ZoneInfo

from keo.history import Scheduler, decide, is_due, run_pull
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


def test_unowned_played_song_goes_directly_to_review(catalog, library, worker, spotdl):
    catalog.history_items = plays(count=1)
    pull(catalog, library, worker, spotdl)
    assert library.active_job("vid1") is None
    row = library.get_review("vid1")
    assert row is not None
    assert row.heard_title == "Lạc Trôi"
    assert row.reason == "pending"
    assert spotdl.lookups == []


def test_single_play_today_qualifies_for_review(catalog, library, worker, spotdl):
    catalog.history_items = plays(count=1)
    pull(catalog, library, worker, spotdl)
    assert library.get_review("vid1") is not None


def test_plays_from_older_shelves_do_not_count(catalog, library, worker, spotdl):
    catalog.history_items = plays(count=5, label="Last week")
    pull(catalog, library, worker, spotdl)
    assert library.get_review("vid1") is None
    assert spotdl.lookups == []


def test_plays_from_yesterday_and_this_week_qualify(catalog, library, worker, spotdl):
    catalog.history_items = [
        HistoryItem("vid1", "Sơn Tùng M-TP", "Lạc Trôi", "Yesterday"),
        HistoryItem("vid2", "Đen Vâu", "Trốn Tìm", "This week"),
    ]
    pull(catalog, library, worker, spotdl)
    assert library.get_review("vid1") is not None
    assert library.get_review("vid2") is not None
    assert spotdl.lookups == []


def test_run_pull_logs_activity(catalog, library, worker, spotdl, caplog):
    import logging
    caplog.set_level(logging.INFO)
    catalog.history_items = [
        HistoryItem("vid1", "Sơn Tùng M-TP", "Lạc Trôi", "Yesterday"),
    ]
    pull(catalog, library, worker, spotdl)
    assert "Starting history pull" in caplog.text
    assert "Fetched 1 history items" in caplog.text
    assert "Added 'Sơn Tùng M-TP - Lạc Trôi' (vid1) to review" in caplog.text
    assert "History pull completed" in caplog.text


def test_history_progress_transitions():
    from keo.history import HistoryProgress
    prog = HistoryProgress()
    assert prog.snapshot() == {"active": False, "stage": "idle", "current": 0, "total": 0, "percent": 0}
    prog.start_fetching()
    assert prog.snapshot() == {"active": True, "stage": "fetching", "current": 0, "total": 0, "percent": 0}
    prog.start_processing(2)
    assert prog.snapshot() == {"active": True, "stage": "processing", "current": 0, "total": 2, "percent": 0}
    prog.step()
    assert prog.snapshot() == {"active": True, "stage": "processing", "current": 1, "total": 2, "percent": 50}
    prog.finish()
    assert prog.snapshot()["active"] is False


def test_run_pull_updates_progress(catalog, library, worker, spotdl):
    from keo.history import HistoryProgress
    prog = HistoryProgress()
    snapshots = []
    orig_step = prog.step
    def observe_step():
        snapshots.append(prog.snapshot())
        orig_step()
    prog.step = observe_step

    catalog.history_items = [
        HistoryItem("vid1", "Sơn Tùng M-TP", "Lạc Trôi", "Yesterday"),
    ]
    run_pull(
        catalog=catalog, library=library, worker=worker, spotdl=spotdl,
        cookies=worker.cookies, now=NIGHT, progress=prog,
    )
    assert len(snapshots) == 1
    assert snapshots[0]["current"] == 0
    assert snapshots[0]["total"] == 1
    assert prog.snapshot()["active"] is False


def test_decide_weak_match_goes_to_review(library, worker, spotdl):
    spotdl.candidates[QUERY] = candidate(name="Một Bài Khác", artist="Ai Đó")
    assert decide("vid1", "Sơn Tùng M-TP", "Lạc Trôi", library=library, worker=worker, spotdl=spotdl, cookies=worker.cookies) == "review"
    row = library.get_review("vid1")
    assert (row.reason, row.candidate_title, row.spotify_id) == ("weak_match", "Một Bài Khác", "sp1")
    assert library.active_job("vid1") is None


def test_decide_no_metadata_goes_to_review(library, worker, spotdl):
    assert decide("vid1", "Sơn Tùng M-TP", "Lạc Trôi", library=library, worker=worker, spotdl=spotdl, cookies=worker.cookies) == "review"
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


def test_multiple_unowned_songs_are_all_added_to_review(catalog, library, worker, spotdl):
    catalog.history_items = plays() + plays("vid2", "Đen", "Bài Này Chill Phết")
    pull(catalog, library, worker, spotdl)
    assert library.get_review("vid1") is not None
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


def test_dismissed_song_can_be_checked_again(catalog, library, worker, spotdl):
    library.add_review("vid1", "Sơn Tùng M-TP", "Lạc Trôi", reason="weak_match")
    library.dismiss("vid1")
    assert library.get_review("vid1") is None
    catalog.history_items = plays(count=1)
    pull(catalog, library, worker, spotdl)
    assert library.get_review("vid1") is not None


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


def test_scheduler_runs_daily_scan_once_per_day(catalog, library, worker, spotdl, cookies, tmp_path):
    music = tmp_path / "music"
    music.mkdir()
    scanned = []
    now = datetime(2026, 11, 12, 22, 5, tzinfo=ZONE)
    sched = Scheduler(
        catalog=catalog,
        library=library,
        worker=worker,
        spotdl=spotdl,
        cookies=cookies,
        timezone="Asia/Ho_Chi_Minh",
        hour=22,
        clock=lambda: now,
        music_dir=music,
        scanner=lambda d, lib: scanned.append(d),
    )
    sched.tick()
    sched.tick()
    assert scanned == [music]


def test_scheduler_sweeps_expired_recycle_files(catalog, library, worker, spotdl, cookies, tmp_path):
    import os, time
    music = tmp_path / "music"
    music.mkdir()
    recycle = music / ".recycle"
    recycle.mkdir()
    old_file = recycle / "Old - Song.mp3"
    old_file.write_bytes(b"old")
    old_time = time.time() - 10 * 86400
    os.utime(old_file, (old_time, old_time))

    now = datetime(2026, 11, 12, 22, 5, tzinfo=ZONE)
    sched = Scheduler(
        catalog=catalog,
        library=library,
        worker=worker,
        spotdl=spotdl,
        cookies=cookies,
        timezone="Asia/Ho_Chi_Minh",
        hour=22,
        clock=lambda: now,
        music_dir=music,
        scanner=lambda d, lib: None,
        recycle_days=7,
    )
    sched.tick()
    assert not old_file.exists()


def test_dismiss_permanent_skips_future_history_pulls(catalog, library, worker, spotdl):
    catalog.history_items = plays(video_id="vid_never", count=1)
    library.dismiss("vid_never", permanent=True)
    assert library.is_dismissed("vid_never")
    pull(catalog, library, worker, spotdl)
    assert library.get_review("vid_never") is None


def test_sync_watched_playlists_adds_to_review_or_auto_queues(catalog, library, worker):
    from keo.history import sync_watched_playlists
    from keo.records import SongRow

    catalog.songs = [
        SongRow("vid_owned", "Owned", ("A",), "Album"),
        SongRow("vid_dismissed", "Dismissed", ("B",), "Album"),
        SongRow("vid_new", "New Song", ("C",), "Album"),
    ]
    library.mark_owned("vid_owned", "A", "Owned", "sp_owned")
    library.dismiss("vid_dismissed", permanent=True)
    library.watch_playlist("PL_review", "Review List", auto_queue=False)

    added = sync_watched_playlists(catalog=catalog, library=library, worker=worker)
    assert added == 1
    assert library.get_review("vid_new") is not None
    assert library.get_review("vid_owned") is None
    assert library.get_review("vid_dismissed") is None

    library.unwatch_playlist("PL_review")
    library.dismiss("vid_new")
    library.watch_playlist("LM", "Liked Music", auto_queue=True)
    added2 = sync_watched_playlists(catalog=catalog, library=library, worker=worker)
    assert added2 == 1
    assert library.active_job("vid_new") is not None


def test_sync_followed_artists_adds_new_releases_to_review(catalog, library):
    from keo.history import sync_followed_artists
    from keo.records import SongRow

    catalog.songs = [
        SongRow("vid_owned", "Owned", ("Sơn Tùng M-TP",), "Album"),
        SongRow("vid_dismissed", "Dismissed", ("Sơn Tùng M-TP",), "Album"),
        SongRow("vid_release", "Come My Way", ("Sơn Tùng M-TP",), "Come My Way"),
    ]
    library.mark_owned("vid_owned", "Sơn Tùng M-TP", "Owned", "sp1")
    library.dismiss("vid_dismissed", permanent=True)
    library.follow_artist("UC3muIvzjhubNpJ4Pn_0kCQw", "Sơn Tùng M-TP")

    added = sync_followed_artists(catalog=catalog, library=library)
    assert added == 1
    assert library.get_review("vid_release") is not None
    assert library.get_review("vid_owned") is None
    assert library.get_review("vid_dismissed") is None
