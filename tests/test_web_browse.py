import pytest

from tests.fakes import SONG_JSON

API_PATHS = [
    "/api/charts",
    "/api/moods",
    "/api/moods/ggMchill",
    "/api/playlists/PLvn",
    "/api/songs/vid1/radio",
]


@pytest.mark.parametrize("path", API_PATHS)
def test_browse_api_needs_the_token(client, path):
    assert client.get(path).status_code == 401


def test_charts_api_lists_vietnam_playlists(client, auth):
    assert client.get("/api/charts", headers=auth).json() == [
        {"playlist_id": "PLvn", "title": "Top 100 Songs Vietnam"}
    ]


def test_moods_api(client, auth):
    assert client.get("/api/moods", headers=auth).json() == {
        "Moods & moments": [{"params": "ggMchill", "title": "Chill"}]
    }


def test_mood_playlists_api(client, auth):
    assert client.get("/api/moods/ggMchill", headers=auth).json() == [
        {"playlist_id": "PLchill", "title": "Chill Vibes"}
    ]


def test_playlist_api(client, auth):
    assert client.get("/api/playlists/PLvn", headers=auth).json() == {
        "title": "Top 100 Songs Vietnam",
        "songs": [SONG_JSON],
    }


def test_radio_api(client, auth, catalog):
    assert client.get("/api/songs/vid1/radio", headers=auth).json() == [SONG_JSON]
    assert catalog.radio_calls == ["vid1"]


@pytest.mark.parametrize(
    ("path", "text"),
    [
        ("/charts", "Top 100 Songs Vietnam"),
        ("/moods", "Chill"),
        ("/moods/ggMchill", "Chill Vibes"),
        ("/playlists/PLvn", "Lạc Trôi"),
        ("/songs/vid1/radio", "Lạc Trôi"),
    ],
)
def test_browse_pages(logged_in, path, text):
    response = logged_in.get(path)
    assert response.status_code == 200
    assert text in response.text


def test_chart_page_links_to_the_playlist(logged_in):
    assert "/playlists/PLvn" in logged_in.get("/charts").text


def test_playlist_page_has_download_buttons(logged_in):
    assert "data-download" in logged_in.get("/playlists/PLvn").text
