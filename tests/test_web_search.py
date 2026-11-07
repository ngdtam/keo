from tests.fakes import SONG_JSON, TOKEN

BODY = {"video_id": "vid1", "title": "Lạc Trôi", "artists": ["Sơn Tùng M-TP"]}


def test_api_needs_the_token(client):
    assert client.get("/api/search", params={"q": "lac"}).status_code == 401
    wrong = {"Authorization": "Bearer wrong"}
    assert client.get("/api/search", params={"q": "lac"}, headers=wrong).status_code == 401
    assert client.post("/api/downloads", json=BODY).status_code == 401


def test_page_without_a_session_redirects_to_login(client):
    response = client.get("/", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/login"


def test_wrong_token_at_login_is_401(client):
    response = client.post("/login", data={"token": "wrong"})
    assert response.status_code == 401
    assert "Wrong token" in response.text


def test_login_sets_a_30_day_session_cookie(client):
    response = client.post("/login", data={"token": TOKEN}, follow_redirects=False)
    assert response.status_code == 303
    cookie = response.headers["set-cookie"]
    assert "keo_session=" in cookie
    assert "HttpOnly" in cookie
    assert "Max-Age=2592000" in cookie
    assert client.get("/api/search", params={"q": "lac"}).status_code == 200


def test_search_api_returns_songs(client, auth):
    response = client.get("/api/search", params={"q": "lac"}, headers=auth)
    assert response.json() == [SONG_JSON]


def test_suggestions_api(client, auth):
    response = client.get("/api/suggestions", params={"q": "lac"}, headers=auth)
    assert response.json() == ["lac remix"]


def test_search_page_lists_songs_with_a_download_button(logged_in):
    response = logged_in.get("/", params={"q": "lac"})
    assert response.status_code == 200
    assert "Lạc Trôi" in response.text
    assert "data-download" in response.text


def test_download_api_queues_once(client, auth):
    first = client.post("/api/downloads", json=BODY, headers=auth).json()
    second = client.post("/api/downloads", json=BODY, headers=auth).json()
    assert first["owned"] is False
    assert first["job"]["status"] == "queued"
    assert first["job"]["source"] == "manual"
    assert second["job"]["id"] == first["job"]["id"]


def test_download_api_reports_an_owned_song(client, auth, library):
    library.mark_owned("vid1", "Sơn Tùng M-TP", "Lạc Trôi", "sp1")
    assert client.post("/api/downloads", json=BODY, headers=auth).json() == {
        "owned": True,
        "job": None,
    }


def test_youtube_music_failure_is_502(client, auth, catalog):
    catalog.fail = True
    response = client.get("/api/search", params={"q": "lac"}, headers=auth)
    assert response.status_code == 502
    assert "offline" in response.json()["detail"]


def test_youtube_music_failure_page_shows_the_reason(logged_in, catalog):
    catalog.fail = True
    response = logged_in.get("/", params={"q": "lac"})
    assert response.status_code == 502
    assert "offline" in response.text
