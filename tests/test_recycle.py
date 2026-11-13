import os
import time

import pytest

from keo.recycle import (
    cleanup_recycle,
    delete_from_recycle,
    empty_recycle,
    list_recycle,
    move_to_recycle,
    restore_from_recycle,
)


def test_move_to_recycle_and_collision(tmp_path):
    music = tmp_path / "music"
    music.mkdir()
    a1 = music / "Artist - Song.mp3"
    a1.write_bytes(b"first")
    a1.with_suffix(".lrc").write_text("lrc1", encoding="utf-8")

    trashed1 = move_to_recycle(music, a1)
    assert not a1.exists()
    assert not a1.with_suffix(".lrc").exists()
    assert trashed1.is_file()
    assert trashed1.with_suffix(".lrc").is_file()

    a2 = music / "Artist - Song.mp3"
    a2.write_bytes(b"second")
    trashed2 = move_to_recycle(music, a2)
    assert trashed2 != trashed1
    assert trashed2.is_file()
    assert len(list_recycle(music)) == 2


def test_restore_and_path_traversal_guard(library, tmp_path):
    music = tmp_path / "music"
    music.mkdir()
    song = music / "Artist - Track.mp3"
    song.write_bytes(b"audio")
    song.with_suffix(".lrc").write_text("words", encoding="utf-8")

    trashed = move_to_recycle(music, song)
    restored = restore_from_recycle(music, trashed.name, library)
    assert restored == song
    assert song.is_file()
    assert song.with_suffix(".lrc").is_file()
    assert len(library.list_songs()) == 1
    assert list_recycle(music) == []

    with pytest.raises(ValueError):
        restore_from_recycle(music, "../escape.mp3", library)


def test_delete_empty_and_cleanup_retention(tmp_path):
    music = tmp_path / "music"
    music.mkdir()
    old_file = music / "Old - Song.mp3"
    new_file = music / "New - Song.mp3"
    old_file.write_bytes(b"old")
    new_file.write_bytes(b"new")

    t_old = move_to_recycle(music, old_file)
    t_new = move_to_recycle(music, new_file)
    now = time.time()
    os.utime(t_old, (now - 10 * 86400, now - 10 * 86400))
    os.utime(t_new, (now - 2 * 86400, now - 2 * 86400))

    assert cleanup_recycle(music, 0, now=now) == 0
    assert cleanup_recycle(music, 7, now=now) == 1
    assert not t_old.exists()
    assert t_new.is_file()

    delete_from_recycle(music, t_new.name)
    assert list_recycle(music) == []

    extra = music / "Extra - Song.mp3"
    extra.write_bytes(b"extra")
    move_to_recycle(music, extra)
    assert empty_recycle(music) == 1
    assert list_recycle(music) == []

