import json
from urllib.error import URLError

from keo.notify import send_notification, trigger_media_refresh


def test_send_notification_posts_json_and_swallows_errors():
    calls = []

    class FakeResp:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    def fake_open(req, timeout=5):
        calls.append((req.full_url, req.get_method(), json.loads(req.data.decode("utf-8")), dict(req.headers)))
        return FakeResp()

    assert send_notification("", "Title", "Body", opener=fake_open) is False
    assert send_notification("https://ntfy.sh/keo-test", "Kéo", "2 songs added", opener=fake_open) is True
    assert calls[0][0] == "https://ntfy.sh/keo-test"
    assert calls[0][1] == "POST"
    assert calls[0][2]["title"] == "Kéo"
    assert calls[0][2]["message"] == "2 songs added"

    assert send_notification("https://discord.com/api/webhooks/1/abc", "Kéo", "Failed", opener=fake_open) is True
    assert calls[1][2] == {"content": "**Kéo**\nFailed"}

    def failing_open(req, timeout=5):
        raise URLError("offline")

    assert send_notification("https://ntfy.sh/keo-test", "Kéo", "Test", opener=failing_open) is False


def test_trigger_media_refresh_gets_url_and_swallows_errors():
    calls = []

    class FakeResp:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    def fake_open(req, timeout=5):
        calls.append((req.full_url, req.get_method()))
        return FakeResp()

    assert trigger_media_refresh("", opener=fake_open) is False
    assert trigger_media_refresh("http://navidrome:4533/rest/startScan", opener=fake_open) is True
    assert calls == [("http://navidrome:4533/rest/startScan", "GET")]

    def failing_open(req, timeout=5):
        raise URLError("offline")

    assert trigger_media_refresh("http://navidrome:4533/rest/startScan", opener=failing_open) is False

