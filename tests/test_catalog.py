import pytest

from keo.catalog import Catalog, CatalogError
from keo.records import HistoryItem, MoodRef, PlaylistRef, SongRow

TRACK = {
    "videoId": "vid1",
    "title": "Lạc Trôi",
    "artists": [{"name": "Sơn Tùng M-TP", "id": "UC1"}],
    "album": {"name": "Lạc Trôi", "id": "MP1"},
}
NO_VIDEO = {"videoId": None, "title": "Gone", "artists": [], "album": None}
SONG = SongRow("vid1", "Lạc Trôi", ("Sơn Tùng M-TP",), "Lạc Trôi")


class FakeYTMusic:
    def __init__(self):
        self.calls = []

    def search(self, query, filter=None, limit=20):
        self.calls.append(("search", query, filter))
        return [TRACK, NO_VIDEO]

    def get_search_suggestions(self, query):
        return ["lac troi", "lac troi remix"]

    def get_charts(self, country="ZZ"):
        self.calls.append(("charts", country))
        return {
            "countries": {},
            "daily": [{"title": "Daily Top Songs Vietnam", "playlistId": "PLdaily"}],
            "weekly": [{"title": "Weekly Top Songs Vietnam", "playlistId": "PLweekly"}],
            "artists": [],
        }

    def get_mood_categories(self):
        return {"Moods & moments": [{"title": "Chill", "params": "ggMchill"}]}

    def get_mood_playlists(self, params):
        self.calls.append(("mood", params))
        return [{"title": "Chill Vibes", "playlistId": "PLchill"}]

    def get_playlist(self, playlistId, limit=100):
        self.calls.append(("playlist", playlistId))
        return {"title": "Top 100", "tracks": [TRACK, NO_VIDEO]}

    def get_watch_playlist(self, videoId=None, limit=25):
        self.calls.append(("radio", videoId))
        return {"tracks": [TRACK]}

    def get_history(self):
        return [
            {**TRACK, "played": "Today"},
            {**TRACK, "played": "Yesterday"},
            {**NO_VIDEO, "played": "Today"},
        ]


@pytest.fixture
def ytm():
    return FakeYTMusic()


def test_search_returns_songs_with_a_video(ytm):
    assert Catalog(ytm).search("lac troi") == [SONG]
    assert ytm.calls == [("search", "lac troi", "songs")]


def test_suggestions(ytm):
    assert Catalog(ytm).suggestions("lac") == ["lac troi", "lac troi remix"]


def test_charts_are_vietnam_playlists(ytm):
    assert Catalog(ytm).charts() == [
        PlaylistRef("PLdaily", "Daily Top Songs Vietnam"),
        PlaylistRef("PLweekly", "Weekly Top Songs Vietnam"),
    ]
    assert ytm.calls == [("charts", "VN")]


def test_moods(ytm):
    assert Catalog(ytm).moods() == {"Moods & moments": [MoodRef("ggMchill", "Chill")]}


def test_mood_playlists(ytm):
    assert Catalog(ytm).mood_playlists("ggMchill") == [PlaylistRef("PLchill", "Chill Vibes")]


def test_playlist(ytm):
    assert Catalog(ytm).playlist("PLdaily") == ("Top 100", [SONG])


def test_radio(ytm):
    assert Catalog(ytm).radio("vid1") == [SONG]
    assert ytm.calls == [("radio", "vid1")]


def test_history_keeps_each_play_with_its_label(ytm):
    assert Catalog(ytm).history() == [
        HistoryItem("vid1", "Sơn Tùng M-TP", "Lạc Trôi", "Today"),
        HistoryItem("vid1", "Sơn Tùng M-TP", "Lạc Trôi", "Yesterday"),
    ]


def test_ytmusicapi_errors_become_catalog_errors():
    class Broken:
        def search(self, *args, **kwargs):
            raise ConnectionError("network down")

    with pytest.raises(CatalogError, match="network down"):
        Catalog(Broken()).search("x")
