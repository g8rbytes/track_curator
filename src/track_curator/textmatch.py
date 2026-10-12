"""Normalisation helpers and the loose name matching used for shows, sets and tracklist pages."""
from __future__ import annotations

import re
import unicodedata

from rapidfuzz import fuzz

DASHES = "‐‑‒–—―−"
_FEAT = re.compile(r"\b(feat\.?|ft\.?|featuring)\b", re.I)
_NOISE = re.compile(
    r"\b(original mix|extended mix|radio edit|free download|free dl|out now|premiere|"
    r"buy|download)\b",
    re.I,
)


def norm_dashes(s: str) -> str:
    for d in DASHES:
        s = s.replace(d, "-")
    return s


def normalize(s: str) -> str:
    """Lowercase, strip accents/punctuation/noise words for comparison."""
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = norm_dashes(s).lower()
    s = s.replace("&", " and ").replace(" x ", " and ")
    s = _FEAT.sub(" ", s)
    s = _NOISE.sub(" ", s)
    s = re.sub(r"[\[\]\(\)\{\}]", " ", s)
    s = re.sub(r"[^\w\s]", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def similarity(a: str, b: str) -> float:
    """0..100, order-insensitive and tolerant of extra words. For show and set names;
    tracks go through matching.judge, which is strict."""
    na, nb = normalize(a), normalize(b)
    if not na or not nb:
        return 0.0
    return max(fuzz.token_set_ratio(na, nb) * 0.9, fuzz.token_sort_ratio(na, nb))


def slugify(s: str) -> str:
    s = normalize(s)
    return re.sub(r"\s+", "-", s)[:80] or "set"
