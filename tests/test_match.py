import pytest

from keo.match import Name, is_close, normalize


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Lạc Trôi", "lạc trôi"),
        ("  Hello   World ", "hello world"),
        ("Don't Stop!", "dont stop"),
        ("Song (feat. Someone)", "song"),
        ("Song [ft. Someone]", "song"),
        ("Song feat. Someone Else", "song"),
        ("Song ft Someone", "song"),
        ("Sơn Tùng M-TP", "sơn tùng mtp"),
        ("ＡＢＣ", "abc"),
        ("Left Right", "left right"),
    ],
)
def test_normalize(raw, expected):
    assert normalize(raw) == expected


def test_same_title_and_artist_after_normalizing_is_close():
    heard = Name("Sơn Tùng M-TP", "Lạc Trôi")
    candidate = Name("SƠN TÙNG M-TP", "Lạc Trôi (feat. Triple D)")
    assert is_close(heard, candidate)


def test_artist_inside_a_longer_name_as_whole_words_is_close():
    assert is_close(Name("Jimin", "Like Crazy"), Name("Park Jimin", "Like Crazy"))
    assert is_close(Name("Park Jimin", "Like Crazy"), Name("Jimin", "Like Crazy"))


def test_part_of_a_word_is_not_close():
    assert not is_close(Name("min", "Like Crazy"), Name("Jimin", "Like Crazy"))


def test_different_title_is_not_close():
    assert not is_close(Name("Đen", "Bài Này Chill Phết"), Name("Đen", "Đưa Nhau Đi Trốn"))


def test_empty_artist_is_not_close():
    assert not is_close(Name("", "Lạc Trôi"), Name("Sơn Tùng M-TP", "Lạc Trôi"))
