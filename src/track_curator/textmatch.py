"""Normalisation and fuzzy matching helpers shared by every source."""
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
    """0..100. Order-insensitive token match, robust to extra words."""
    na, nb = normalize(a), normalize(b)
    if not na or not nb:
        return 0.0
    return max(fuzz.token_set_ratio(na, nb) * 0.9, fuzz.token_sort_ratio(na, nb))


def track_score(want_artist: str, want_title: str, got_artist: str, got_title: str) -> float:
    """Score a candidate track against a tracklist entry (0..100).

    Title matters most; the artist is checked against both the artist field and the
    candidate title, because SoundCloud uploads often read "Artist - Title" under a
    label's account.
    """
    title_s = similarity(want_title, got_title)
    # Remix info must agree: "X (Y Remix)" should not match plain "X".
    rw, rg = _remixer(want_title), _remixer(got_title)
    if rw != rg and (rw or rg):
        title_s -= 25
    if want_artist:
        artist_s = max(
            similarity(want_artist, got_artist),
            fuzz.partial_ratio(normalize(want_artist), normalize(got_title)) if got_title else 0,
        )
    else:
        artist_s = 50
    return round(0.65 * title_s + 0.35 * artist_s, 1)


_REMIX = re.compile(r"[\(\[]([^\)\]]*?)\s+(remix|flip|bootleg|edit|vip|rework|refix)[\)\]]", re.I)


def _remixer(title: str) -> str:
    m = _REMIX.search(norm_dashes(title))
    if not m:
        if re.search(r"\bvip\b", title, re.I):
            return "vip"
        return ""
    return normalize(m.group(1) + " " + m.group(2))


def slugify(s: str) -> str:
    s = normalize(s)
    return re.sub(r"\s+", "-", s)[:80] or "set"
