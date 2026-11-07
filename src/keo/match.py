"""Decide whether a YouTube Music song and a Spotify result are the same song."""

from __future__ import annotations

import re
import unicodedata
from typing import NamedTuple

_FEAT_GROUP = re.compile(r"[(\[]\s*(?:feat|ft)\b[^)\]]*[)\]]")
_FEAT_TAIL = re.compile(r"\s(?:feat|ft)\b.*$")


class Name(NamedTuple):
    artist: str
    title: str


def normalize(text: str) -> str:
    text = unicodedata.normalize("NFKC", text).casefold()
    text = _FEAT_GROUP.sub(" ", text)
    text = _FEAT_TAIL.sub("", text)
    text = "".join(ch for ch in text if ch.isalnum() or ch.isspace())
    return " ".join(text.split())


def is_close(heard: Name, candidate: Name) -> bool:
    return normalize(heard.title) == normalize(candidate.title) and _artists_close(
        normalize(heard.artist), normalize(candidate.artist)
    )


def _artists_close(left: str, right: str) -> bool:
    if not left or not right:
        return False
    shorter, longer = sorted((left, right), key=len)
    return f" {shorter} " in f" {longer} "
