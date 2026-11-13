import sqlite3
from datetime import date
from types import SimpleNamespace

from keo.cookies import CookieGate
from keo.jobs import Worker
from keo.library import Library
from keo.records import HistoryItem
from keo.system import (
    backup_data,
    check_package_updates,
    last_backup_date,
    run_health_checks,
)
from tests.fakes import FakeCatalog, FakeSpotdl


def test_backup_data_copies_db_archive_config_and_rotates(tmp_path):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    lib = Library(data_dir / "keo.db", data_dir / "archive.txt")
    lib.mark_owned("v1", "Artist", "Song", "sp1")
    (data_dir / "archive.txt").write_text("https://open.spotify.com/track/sp1\n", encoding="utf-8")
    cfg = tmp_path / "keo.toml"
    cfg.write_text('format = "opus"\n', encoding="utf-8")

    for day in (1, 2, 3, 4, 5):
        out = backup_data(data_dir, cfg, keep=4, today=date(2026, 11, day))
        assert (out / "keo.db").is_file()
        assert (out / "archive.txt").is_file()
        assert (out / "keo.toml").is_file()

    backups = sorted((data_dir / "backups").iterdir())
    assert [b.name for b in backups] == ["2026-11-02", "2026-11-03", "2026-11-04", "2026-11-05"]
    assert last_backup_date(data_dir) == date(2026, 11, 5)
    con = sqlite3.connect(backups[-1] / "keo.db")
    assert con.execute("SELECT COUNT(*) FROM songs").fetchone()[0] == 1
    con.close()


def test_run_health_checks_reports_music_tools_and_pull(tmp_path):
    data_dir = tmp_path / "data"
    music_dir = tmp_path / "music"
    data_dir.mkdir()
    music_dir.mkdir()
    lib = Library(data_dir / "keo.db", data_dir / "archive.txt")
    lib.record_pull("2026-11-13T00:00:00+00:00", "ok", None, [HistoryItem("v1", "A", "B", "Today")])
    spotdl = FakeSpotdl()
    cookies = CookieGate(probe=lambda _: None, clock=lambda: 0.0)
    worker = Worker(lib, spotdl, music_dir=music_dir, cookies=cookies)
    services = SimpleNamespace(
        ytm_auth=None,
        ytm_client_id="",
        ytm_client_secret="",
        spotdl=spotdl,
        cookies=cookies,
        music_dir=music_dir,
        data_dir=data_dir,
        library=lib,
        worker=worker,
    )
    checks = run_health_checks(services)
    text = "\n".join(msg for _, msg in checks)
    assert "Music folder" in text
    assert "spotdl" in text.lower()
    assert "yt-dlp" in text.lower()
    assert "ytmusicapi" in text.lower()
    assert "mediainfo" in text.lower()
    assert "Last history pull" in text


def test_check_package_updates_compares_pypi_versions():
    versions = {
        "yt-dlp": "2026.10.21",
        "ytmusicapi": "1.12.4",
        "spotdl": "4.6.0",
        "pymediainfo": "7.0.1",
    }
    latest = {
        "yt-dlp": "2026.11.01",
        "ytmusicapi": "1.12.4",
        "spotdl": "4.6.0",
        "pymediainfo": "7.0.2",
    }
    result = check_package_updates(
        version_getter=lambda name: versions[name],
        latest_getter=lambda name: latest[name],
    )
    assert result["yt-dlp"] == ("2026.10.21", "2026.11.01", True)
    assert result["ytmusicapi"] == ("1.12.4", "1.12.4", False)
    assert result["spotdl"] == ("4.6.0", "4.6.0", False)
    assert result["pymediainfo"] == ("7.0.1", "7.0.2", True)

