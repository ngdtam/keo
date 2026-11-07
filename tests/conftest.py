import pytest

from keo.jobs import Worker
from keo.library import Library
from tests.fakes import FakeSpotdl


@pytest.fixture
def library(tmp_path):
    return Library(tmp_path / "keo.db", tmp_path / "archive.txt")


@pytest.fixture
def spotdl():
    return FakeSpotdl()


@pytest.fixture
def worker(library, spotdl):
    return Worker(library, spotdl)
