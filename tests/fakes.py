"""Test doubles for YouTube Music and spotDL."""

from keo.spotdl_cmd import Candidate, DownloadResult, Lookup

TOKEN = "test-token"
QUERY = "Sơn Tùng M-TP - Lạc Trôi"


def candidate(name="Lạc Trôi", artist="Sơn Tùng M-TP", song_id="sp1"):
    return Candidate(
        name=name,
        artist=artist,
        artists=(artist,),
        url=f"https://open.spotify.com/track/{song_id}",
        song_id=song_id,
    )


class FakeSpotdl:
    def __init__(self):
        self.candidates = {}
        self.fail_queries = set()
        self.download_ok = True
        self.download_error = None
        self.lookups = []
        self.downloads = []

    def lookup(self, query):
        self.lookups.append(query)
        if query in self.fail_queries:
            raise RuntimeError(f"lookup crashed for {query}")
        return Lookup(self.candidates.get(query), "fake lookup output")

    def download(self, url, spotify_id):
        self.downloads.append(url)
        if self.download_error is not None:
            raise self.download_error
        return DownloadResult(self.download_ok, "fake download output")
