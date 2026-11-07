import pytest

from keo.library import Library


@pytest.fixture
def library(tmp_path):
    return Library(tmp_path / "keo.db", tmp_path / "archive.txt")
