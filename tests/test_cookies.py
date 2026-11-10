from keo.cookies import EXPIRED, FREE, PREMIUM, UNKNOWN, classify


def test_warning_beats_a_premium_format():
    text = (
        "The provided YouTube account cookies are no longer valid. "
        "They have likely been rotated in the browser."
    )
    assert classify(text, [{"format_id": "141"}]) == EXPIRED


def test_format_141_or_774_is_premium():
    assert classify("", [{"format_id": "251"}, {"format_id": "141"}]) == PREMIUM
    assert classify("", [{"format_id": 774}]) == PREMIUM


def test_any_other_format_list_is_free():
    assert classify("", [{"format_id": "251"}]) == FREE
    assert classify("", []) == FREE


def test_a_broken_probe_is_unknown():
    assert classify("Video unavailable", None) == UNKNOWN
    assert classify("", "not-a-list") == UNKNOWN


from keo.config import SpotdlSettings
from keo.cookies import (
    EXPIRED_LINE,
    FAILED_LINE,
    FREE_LINE,
    MISSING_LINE,
    PREMIUM_LINE,
    CookieGate,
    ProbeResult,
)


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


class Script:
    def __init__(self, results):
        self.results = list(results)
        self.calls = []

    def __call__(self, cookie_file):
        self.calls.append(cookie_file)
        return self.results.pop(0)


def cookie(tmp_path):
    path = tmp_path / "cookies.txt"
    path.write_text("a", encoding="utf-8")
    return str(path)


def premium_info():
    return ProbeResult([{"format_id": "141"}], "")


def gate(tmp_path, results):
    script = Script(results)
    box = Clock()
    return CookieGate(probe=script, clock=box), script, box


def test_fresh_premium_skips_the_second_probe(tmp_path):
    path = cookie(tmp_path)
    cookies, script, clock = gate(tmp_path, [premium_info(), premium_info()])
    settings = SpotdlSettings(format="opus", cookie_file=path)
    assert cookies.allow(settings) is None
    clock.now = 1799.0
    assert cookies.allow(settings) is None
    assert script.calls == [path]
    assert cookies.line(settings) == PREMIUM_LINE


def test_stale_premium_probes_again(tmp_path):
    path = cookie(tmp_path)
    cookies, script, clock = gate(
        tmp_path, [premium_info(), ProbeResult([{"format_id": "251"}], "")]
    )
    settings = SpotdlSettings(format="opus", cookie_file=path)
    assert cookies.allow(settings) is None
    clock.now = 1800.0
    assert cookies.allow(settings) == FREE_LINE
    assert script.calls == [path, path]


def test_expired_sticks_until_check(tmp_path):
    path = cookie(tmp_path)
    cookies, script, _clock = gate(
        tmp_path,
        [
            ProbeResult([{"format_id": "141"}], "cookies are no longer valid"),
            premium_info(),
        ],
    )
    settings = SpotdlSettings(format="m4a", cookie_file=path)
    assert cookies.allow(settings) == EXPIRED_LINE
    assert cookies.allow(settings) == EXPIRED_LINE
    assert script.calls == [path]
    assert cookies.check(path) == PREMIUM
    assert cookies.allow(settings) is None
    assert cookies.line(settings) == PREMIUM_LINE


def test_free_sticks_until_check(tmp_path):
    path = cookie(tmp_path)
    cookies, script, _clock = gate(tmp_path, [ProbeResult([{"format_id": "251"}], ""), premium_info()])
    settings = SpotdlSettings(format="opus", cookie_file=path)
    assert cookies.allow(settings) == FREE_LINE
    assert cookies.allow(settings) == FREE_LINE
    assert script.calls == [path]
    assert cookies.check(path) == PREMIUM
    assert cookies.allow(settings) is None


def test_unknown_keeps_a_fresh_premium_verdict(tmp_path):
    path = cookie(tmp_path)
    cookies, script, _clock = gate(
        tmp_path, [premium_info(), ProbeResult(None, "timed out")]
    )
    settings = SpotdlSettings(format="opus", bitrate="disable", cookie_file=path)
    assert cookies.allow(settings) is None
    assert cookies.check(path) == UNKNOWN
    assert cookies.line(settings) == FAILED_LINE
    assert cookies.allow(settings) is None
    assert script.calls == [path, path]


def test_missing_file_refuses_without_a_probe(tmp_path):
    cookies, script, _clock = gate(tmp_path, [])
    settings = SpotdlSettings(format="opus", cookie_file=str(tmp_path / "absent.txt"))
    assert cookies.allow(settings) == MISSING_LINE
    assert script.calls == []
    assert cookies.line(settings) == MISSING_LINE


def test_free_settings_skip_the_probe(tmp_path):
    path = cookie(tmp_path)
    cookies, script, _clock = gate(tmp_path, [])
    settings = SpotdlSettings(format="mp3", bitrate="128k", cookie_file=path)
    assert cookies.allow(settings) is None
    assert script.calls == []


def test_bitrate_disable_is_premium(tmp_path):
    path = cookie(tmp_path)
    cookies, script, _clock = gate(tmp_path, [ProbeResult([{"format_id": "251"}], "")])
    settings = SpotdlSettings(format="mp3", bitrate="disable", cookie_file=path)
    assert cookies.allow(settings) == FREE_LINE
    assert script.calls == [path]


def test_a_raised_probe_is_unknown_and_keeps_the_old_verdict(tmp_path):
    path = cookie(tmp_path)

    class Boom:
        def __init__(self):
            self.calls = 0

        def __call__(self, cookie_file):
            self.calls += 1
            if self.calls == 1:
                return premium_info()
            raise RuntimeError("socket down")

    clock = Clock()
    probe = Boom()
    cookies = CookieGate(probe=probe, clock=clock)
    settings = SpotdlSettings(format="opus", cookie_file=path)
    assert cookies.allow(settings) is None
    clock.now = 1800.0
    assert cookies.allow(settings) == FAILED_LINE
    assert cookies.line(settings) == FAILED_LINE
    clock.now = 1800.0
    assert cookies.allow(settings) == FAILED_LINE
    assert probe.calls == 3


def test_missing_yt_dlp_is_unknown(monkeypatch, tmp_path):
    monkeypatch.setattr("keo.cookies._youtube_dl", lambda: None)
    path = cookie(tmp_path)
    cookies = CookieGate(clock=lambda: 0.0)
    assert cookies.check(path) == UNKNOWN
    assert cookies.line(SpotdlSettings(cookie_file=path)) == FAILED_LINE
