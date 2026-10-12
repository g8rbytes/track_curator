"""Pull a numbered tracklist out of free text (a SoundCloud "Behind this track" description).

Deterministic rules:
  1. If a header line fuzzy-matches "tracklist"/"track list"/"playlist"/"tl", parsing starts
     after it.
  2. A track line is "[number|timestamp] Artist - Title". Numbered/timestamped lines are
     always accepted; un-numbered "Artist - Title" lines are only accepted after a header.
  3. "w/" lines are layers on the previous track. Lines whose title or artist is "ID" are IDs.
  4. We need at least MIN_TRACKS track lines to call it a tracklist.
  5. Cue times ("00:03:04", "[1:02:03]", "(03:04)") are taken out of the line wherever they
     sit and kept in TrackEntry.timestamp, so they never reach a title or a search.
"""
from __future__ import annotations

import re
from typing import NamedTuple

from rapidfuzz import fuzz

from .models import TrackEntry
from .textmatch import norm_dashes

MIN_TRACKS = 3
HEADER_WORDS = ("tracklist", "track list", "tracklisting", "playlist", "setlist", "tl")

_TS = r"\d{1,2}:\d{2}(?::\d{2})?"
_PREFIX = re.compile(
    rf"""^\s*
    (?:
        [\[\(]?(?P<ts>{_TS})[\]\)]?\s*[-.:|)]?\s*    # 00:00  [1:02:03]  (03:04) -
      | (?P<num>\d{{1,3}})\s*(?:[.)\]:\-]|\s)\s*     # 01.  1)  [01]  1 -  01
      | \[(?P<bnum>\d{{1,3}})\]\s*
      | [•*●▪►>]\s*                   # bullets
    )
    """,
    re.X,
)
# A cue time right after the track number: "1. [00:00] Artist - Title"
_TS_LEAD = re.compile(rf"^[\[\(\{{]?(?P<ts>{_TS})[\]\)\}}]?\s*[-.:|)]?\s*")
# ... at the end of the line: "Artist - Title 00:03:04", "Title - 03:04", "Title | 3:04"
_TS_TRAIL = re.compile(rf"\s*(?:[-|@~]\s*)?[\[\(\{{]?(?P<ts>{_TS})[\]\)\}}]?\s*$")
# ... or in brackets anywhere: "Artist - Title [03:04] [LABEL]"
_TS_WRAPPED = re.compile(rf"\s*[\[\(\{{]\s*(?P<ts>{_TS})\s*[\]\)\}}]")
_LAYER = re.compile(r"^\s*(?:\+|w/|w\\)\s*", re.I)
_SEP = re.compile(r"\s+-\s+|\s*-\s+|\s+-\s*")
_ID = re.compile(r"^(?:id|\?+|tba|unknown|unreleased id)$", re.I)
_URL = re.compile(r"https?://\S+")
_TRAILING_LABEL = re.compile(r"\s*\[[^\]]*\]\s*$")  # "Title [LABEL]"
_EMOJI = re.compile("[\U0001F300-\U0001FAFF☀-➿️]")


def _is_header(line: str) -> bool:
    s = re.sub(r"[^a-z ]", " ", line.lower()).strip()
    if not s or len(s) > 40:
        return False
    for w in HEADER_WORDS:
        if s == w or s.startswith(w + " ") or s.endswith(" " + w):
            return True
        if len(w) > 3 and fuzz.ratio(s.split(" ")[0], w) >= 85:
            return True
    return False


def is_id_text(s: str) -> bool:
    s = s.strip().strip("\"'").strip()
    s = re.sub(r"[\(\[].*?[\)\]]", "", s).strip()  # "ID (ID Remix)" -> "ID"
    return bool(_ID.match(s)) or s == ""


def split_artist_title(text: str) -> tuple[str, str] | None:
    text = norm_dashes(text)
    parts = _SEP.split(text, maxsplit=1)
    if len(parts) != 2:
        return None
    artist, title = parts[0].strip(" \"'"), parts[1].strip(" \"'")
    if not artist or not title:
        return None
    title = _TRAILING_LABEL.sub("", title).strip()
    return artist, title


class ParsedLine(NamedTuple):
    artist: str
    title: str
    numbered: bool
    layer: bool
    timestamp: str = ""


def _usable(s: str) -> bool:
    return bool(s) and (_is_id_line(s) or split_artist_title(s) is not None)


def strip_timestamps(s: str) -> tuple[str, str]:
    """Take cue times out of a track line. Returns (line without them, first cue time).

    A trailing or bracketed time is only removed when what is left still reads as
    "Artist - Title", so a track that is really called "03:00" keeps its name.
    """
    ts = ""
    m = _TS_LEAD.match(s)
    if m and _usable(s[m.end():].strip()):
        ts, s = m.group("ts"), s[m.end():].strip()
    while True:
        m = _TS_WRAPPED.search(s)
        if not m:
            break
        rest = (s[: m.start()] + s[m.end():]).strip()
        if not _usable(rest):
            break
        ts, s = ts or m.group("ts"), rest
    m = _TS_TRAIL.search(s)
    if m and m.start() > 0:
        rest = s[: m.start()].strip()
        if _usable(rest):
            ts, s = ts or m.group("ts"), rest
    return s, ts


def parse_line(line: str) -> ParsedLine | None:
    s = _URL.sub("", _EMOJI.sub("", line)).strip()
    if not s:
        return None
    numbered = False
    ts = ""
    m = _PREFIX.match(s)
    if m and m.end() > 0 and (m.group("num") or m.group("ts") or m.group("bnum")):
        numbered = True
        ts = m.group("ts") or ""
        s = s[m.end():].strip()
    elif m and m.end() > 0:
        s = s[m.end():].strip()
    layer = False
    lm = _LAYER.match(s)
    if lm:
        layer = True
        s = s[lm.end():].strip()
    s, ts2 = strip_timestamps(s)
    ts = ts or ts2
    if _is_id_line(s):
        return ParsedLine("ID", "ID", numbered, layer, ts)
    at = split_artist_title(s)
    if not at:
        return None
    return ParsedLine(at[0], at[1], numbered, layer, ts)


def _is_id_line(s: str) -> bool:
    return bool(re.fullmatch(r"(?i)\s*id\s*(?:-\s*id\s*)?", norm_dashes(s)))


def parse_tracklist(text: str, source: str = "soundcloud") -> list[TrackEntry]:
    if not text:
        return []
    lines = text.replace("\r", "").split("\n")
    header_idx = next((i for i, l in enumerate(lines) if _is_header(l)), None)
    start = header_idx + 1 if header_idx is not None else 0
    entries: list[TrackEntry] = []
    misses = 0
    for line in lines[start:]:
        if not line.strip():
            continue
        parsed = parse_line(line)
        if parsed is None:
            if entries:
                misses += 1
                if misses >= 3 and header_idx is None:
                    break
            continue
        artist, title, numbered, layer, ts = parsed
        if not numbered and not layer and header_idx is None:
            continue
        misses = 0
        is_id = is_id_text(title) or (is_id_text(artist) and is_id_text(title))
        entries.append(
            TrackEntry(
                position=len([e for e in entries if not e.is_layer]) + (0 if layer else 1),
                artist=artist,
                title=title,
                raw=line.strip(),
                is_id=is_id,
                is_layer=layer,
                source=source,
                timestamp=ts,
            )
        )
    if len(entries) < MIN_TRACKS:
        return []
    return entries


def id_ratio(entries: list[TrackEntry]) -> float:
    main = [e for e in entries if not e.is_layer]
    if not main:
        return 1.0
    return sum(e.is_id for e in main) / len(main)
