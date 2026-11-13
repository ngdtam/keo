"""Test doubles for YouTube Music and spotDL."""

from keo.catalog import CatalogError
from keo.config import SpotdlSettings
from keo.records import MoodRef, PlaylistRef, SongRow
from keo.spotdl_cmd import Candidate, DownloadResult, Lookup

TOKEN = "test-token"
QUERY = "Sơn Tùng M-TP - Lạc Trôi"
SONG = SongRow("vid1", "Lạc Trôi", ("Sơn Tùng M-TP",), "Lạc Trôi")
SONG_JSON = {
    "video_id": "vid1",
    "title": "Lạc Trôi",
    "artists": ["Sơn Tùng M-TP"],
    "album": "Lạc Trôi",
}


def candidate(name="Lạc Trôi", artist="Sơn Tùng M-TP", song_id="sp1"):
    return Candidate(
        name=name,
        artist=artist,
        artists=(artist,),
        url=f"https://open.spotify.com/track/{song_id}",
        song_id=song_id,
    )


class FakeCatalog:
    def __init__(self):
        self.songs = [SONG]
        self.history_items = []
        self.radio_calls = []
        self.fail = False

    def _check(self):
        if self.fail:
            raise CatalogError("YouTube Music request failed: offline")

    def search(self, query):
        self._check()
        return self.songs

    def suggestions(self, query):
        self._check()
        return [f"{query} remix"]

    def charts(self):
        self._check()
        return [PlaylistRef("PLvn", "Top 100 Songs Vietnam")]

    def moods(self):
        self._check()
        return {"Moods & moments": [MoodRef("ggMchill", "Chill")]}

    def mood_playlists(self, params):
        self._check()
        return [PlaylistRef("PLchill", "Chill Vibes")]

    def playlist(self, playlist_id):
        self._check()
        return "Top 100 Songs Vietnam", self.songs

    def find_artist(self, query):
        self._check()
        return "UC3muIvzjhubNpJ4Pn_0kCQw", query

    def artist_releases(self, channel_id):
        self._check()
        return "Sơn Tùng M-TP", self.songs

    def radio(self, video_id):
        self._check()
        self.radio_calls.append(video_id)
        return self.songs

    def history(self):
        self._check()
        return self.history_items


class FakeSpotdl:
    def __init__(self):
        self.candidates = {}
        self.fail_queries = set()
        self.download_ok = True
        self.download_error = None
        self.download_output = "fake download output"
        self.lookups = []
        self.downloads = []
        self.download_video_ids = []
        self.settings = SpotdlSettings()

    def use(self, settings):
        self.settings = settings

    def lookup(self, query):
        self.lookups.append(query)
        if query in self.fail_queries:
            raise RuntimeError(f"lookup crashed for {query}")
        return Lookup(self.candidates.get(query), "fake lookup output")

    def download(self, url, spotify_id, *, video_id=None):
        self.downloads.append(url)
        self.download_video_ids.append(video_id)
        if self.download_error is not None:
            raise self.download_error
        return DownloadResult(self.download_ok, self.download_output)
