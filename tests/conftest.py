import pytest
from fastapi.testclient import TestClient

from keo.jobs import Worker
from keo.library import Library
from keo.web import Services, create_app
from tests.fakes import TOKEN, FakeCatalog, FakeSpotdl


@pytest.fixture
def library(tmp_path):
    return Library(tmp_path / "keo.db", tmp_path / "archive.txt")


@pytest.fixture
def spotdl():
    return FakeSpotdl()


@pytest.fixture
def worker(library, spotdl):
    return Worker(library, spotdl)


@pytest.fixture
def catalog():
    return FakeCatalog()


@pytest.fixture
def client(catalog, library, worker, spotdl, tmp_path):
    music = tmp_path / "music"
    music.mkdir(exist_ok=True)
    services = Services(
        token=TOKEN,
        catalog=catalog,
        library=library,
        worker=worker,
        spotdl=spotdl,
        run_later=lambda fn: fn(),
        music_dir=music,
    )
    with TestClient(create_app(services)) as test_client:
        yield test_client


@pytest.fixture
def auth():
    return {"Authorization": f"Bearer {TOKEN}"}


@pytest.fixture
def logged_in(client):
    client.post("/login", data={"token": TOKEN})
    return client
