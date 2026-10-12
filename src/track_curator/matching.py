"""Decide whether a search result is the exact track a tracklist line names.

Beatport and SoundCloud results both go through `judge`, which accepts a result only when
all of these hold:

  1. Title: the titles without versions, features and tags are at least TITLE_MIN alike by
     plain edit distance (so "Thicc" is not "THICC ASL"), and any numbers in them agree
     ("Pt. 2" is not "Pt. 1").
  2. Artist: at least one artist on the line (or the line's remixer) is credited on the
     result, as an artist, a remixer or (on SoundCloud) the uploader.
  3. Version:
     - a remix, flip, bootleg, edit... needs its remixer on the result: named in the title or
       mix name (preferred), otherwise credited as an artist. Someone else's remix is
       rejected, so "Temperature (LUMBERJVCK FLIP)" never matches the original.
     - a line with no version (or Original/Extended/Radio...) rejects other people's remixes
       and VIP, instrumental, live... versions.
     - VIP, instrumental and acapella must agree both ways.

Only the first MAX_RESULTS results of each search are looked at; when several pass, the
best title/artist/version fit wins (Extended Mix over Original Mix when the line doesn't say).
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any, Iterable, Optional

from rapidfuzz import fuzz

from .textmatch import norm_dashes

MAX_RESULTS = 10
TITLE_MIN = 85
MAX_TRACK_MS = 20 * 60 * 1000  # longer SoundCloud uploads are mixes, not tracks

# -- text ----------------------------------------------------------------------------

_SPECIAL = str.maketrans({"ø": "o", "Ø": "O", "æ": "ae", "Æ": "AE", "œ": "oe", "Œ": "OE", "ß": "ss",
                          "đ": "d", "Đ": "D", "ł": "l", "Ł": "L", "ı": "i", "$": "s"})
_RE_JOIN = re.compile(
    r"\bre (mix|work|fix|edit|think|make|rub|mode|boot|vamp|touch|shape|construction|imagined|"
    r"imagination|interpretation|build|wire|model)\b"
)


def fold(s: str) -> str:
    """Comparison form: lowercase, accents and apostrophes dropped, & -> and, punctuation -> space."""
    s = unicodedata.normalize("NFKD", (s or "").translate(_SPECIAL))
    s = "".join(c for c in s if not unicodedata.combining(c)).lower()
    s = re.sub(r"['’‘`´]", "", s)
    s = s.replace("&", " and ")
    s = re.sub(r"[\W_]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def compact(s: str) -> str:
    return fold(s).replace(" ", "")


# -- artists -------------------------------------------------------------------------

_ARTIST_SEP = re.compile(
    r"\s*(?:,|&|\+|/|;|\band\b|\bx\b|\bvs\b\.?|\bb2b\b|\bwith\b|\bfeat\b\.?|\bft\b\.?|\bfeaturing\b|"
    r"\bpres(?:ents)?\b\.?|\bmeets\b)\s*",
    re.I,
)
_LIST_SEP = re.compile(r"\s*(?:,|&|\+|/|;|\s+x\s+|\bvs\b\.?|\bb2b\b)\s*", re.I)
_FEAT_SPLIT = re.compile(r"\s+(?:feat\b\.?|ft\b\.?|featuring\b|with\b)\s*", re.I)
_COUNTRY = re.compile(r"\s*\([A-Za-z]{2,3}\)\s*$")  # Beatport's "WINK (US)", "Aflo (UK)"
_STOP = {"", "the", "and", "va", "variousartists", "unknown", "unknownartist", "id"}


def split_artists(text: str) -> list[str]:
    """'A, B & C' -> ['A', 'B', 'C'] (names as written, for display and search)."""
    return [p.strip() for p in _LIST_SEP.split(norm_dashes(text or "")) if p and p.strip()]


def artist_keys(names: Iterable[str]) -> set[str]:
    """Every way a credit can be compared: the whole name and each collaborator in it."""
    keys: set[str] = set()
    for n in names:
        n = _COUNTRY.sub("", norm_dashes(n or "")).strip()
        if not n:
            continue
        keys.add(compact(n))
        for part in _ARTIST_SEP.split(n):
            keys.add(compact(_COUNTRY.sub("", part or "")))
    return keys - _STOP


def _same_name(a: str, b: str) -> bool:
    if a == b:
        return True
    return min(len(a), len(b)) >= 5 and fuzz.ratio(a, b) >= 90


def keys_match(want: set[str], have: set[str]) -> set[str]:
    """The wanted keys that are found among `have`."""
    return {w for w in want if any(_same_name(w, h) for h in have)}


# -- versions ------------------------------------------------------------------------

REMIX_WORDS = {
    "remix", "rmx", "flip", "bootleg", "boot", "rework", "refix", "reedit", "mashup", "rethink",
    "reimagined", "reimagination", "reinterpretation", "remake", "redux", "revision", "rerub", "remode",
    "reboot", "revamp", "retouch", "reshape", "reconstruction", "rebuild", "rewire", "remodel",
}
NEUTRAL_WORDS = {
    "original", "extended", "radio", "club", "main", "album", "single", "clean", "dirty", "explicit",
    "short", "full", "vocal", "dj", "intro", "outro", "remaster", "remastered", "mix", "edit", "version",
    "length", "cut", "pro", "the", "a", "an", "mixed", "unmixed", "uncut", "uncensored", "censored",
}
OTHER_WORDS = {
    "live", "acoustic", "slowed", "spedup", "speedup", "sped", "reverb", "nightcore", "8d", "dub",
    "unplugged", "orchestral", "piano", "demo", "loop", "cover", "karaoke", "clip", "preview", "snippet",
    "teaser", "bassboosted",
}
TRAILING_OK = {"mix", "edit", "version", "extended", "radio", "club", "vip", "dub", "original"}
_NOISE = re.compile(
    r"\b(?:free\s*(?:download|dl|d/l)|free|out\s*now|premiere|exclusive|official\s*(?:audio|video)|"
    r"lyrics?|visuali[sz]er|prod\.?\s*by|prod\.|buy\s*=\s*free|download)\b",
    re.I,
)
_NOISE_OUTSIDE = re.compile(r"\b(?:free\s*(?:download|dl|d/l)|out\s*now)\b|\*+", re.I)


@dataclass
class Version:
    kind: str = ""  # "" (none given) | neutral | remix | vip | instrumental | acapella | other
    text: str = ""  # as written, e.g. "LUMBERJVCK FLIP", "Extended Mix"
    remixer: str = ""  # who made a remix/flip/edit (or a "Getter VIP"), as written


def _version_words(text: str) -> list[str]:
    f = fold(text)
    f = _RE_JOIN.sub(r"re\1", f)
    f = f.replace("mash up", "mashup").replace("sped up", "spedup").replace("speed up", "speedup")
    f = f.replace("bass boosted", "bassboosted")
    f = re.sub(r"\ba ?cc?app?ell?a\b", "acapella", f)
    return f.split()


def _keyword_pattern(word: str) -> str:
    """Regex for a folded keyword as it may be written: 'reedit' -> 'Re-Edit', 'mashup' -> 'Mash Up'."""
    if word.startswith("re") and len(word) > 4:
        return "re[\\s-]?" + re.escape(word[2:])
    if word == "mashup":
        return "mash[\\s-]?up"
    return re.escape(word)


def _before_keyword(raw: str, words: set[str]) -> str:
    """The text in `raw` before the last version keyword: 'Zeds Dead Remix' -> 'Zeds Dead'."""
    pat = "|".join(sorted((_keyword_pattern(w) for w in words), key=len, reverse=True))
    found = list(re.finditer(rf"(?i)\b(?:{pat})\b", raw))
    if not found:
        return ""
    who = raw[: found[-1].start()]
    who = re.sub(r"(?i)['’]s\s*$", "", who.strip()).strip(" -–'’\"")
    # drop trailing neutral words: "LUMBERJVCK Extended" -> "LUMBERJVCK"
    toks = who.split()
    while toks and fold(toks[-1]) in NEUTRAL_WORDS | {"vip"}:
        toks.pop()
    return " ".join(toks)


def classify_version(text: str) -> Optional[Version]:
    """What kind of version a bracket or mix name describes, or None if it isn't one."""
    raw = (text or "").strip()
    words = _version_words(raw)
    if not words:
        return None
    ws = set(words)
    plain = [w for w in words if not w.isdigit()]
    if "vip" in ws:
        return Version("vip", raw, _before_keyword(raw, {"vip"}))
    if ws & {"instrumental", "inst"}:
        return Version("instrumental", raw)
    if "acapella" in ws:
        return Version("acapella", raw)
    hits = [i for i, w in enumerate(words) if w in REMIX_WORDS]
    if hits and all(w in TRAILING_OK for w in words[hits[-1] + 1:]):
        return Version("remix", raw, _before_keyword(raw, REMIX_WORDS))
    if ws & OTHER_WORDS:
        return Version("other", raw)
    if "edit" in ws:
        if all(w in NEUTRAL_WORDS for w in plain):
            return Version("neutral", raw)
        if words[-1] in ("edit", "mix", "version"):
            return Version("remix", raw, _before_keyword(raw, {"edit"}))
        return None
    if plain and all(w in NEUTRAL_WORDS for w in plain):
        return Version("neutral", raw)
    if words[-1] in ("mix", "version"):
        return Version("other", raw)
    return None


# -- titles --------------------------------------------------------------------------

_BRACKET = re.compile(r"\s*([\(\[\{])([^\(\)\[\]\{\}]*)[\)\]\}]")
_FEAT_INNER = re.compile(r"^(?:feat\b\.?|ft\b\.?|featuring\b|with\b)\s*(.*)$", re.I)
_FEAT_TAIL = re.compile(r"\s+(?:feat\b\.?|ft\b\.?|featuring\b)\s*(.+)$", re.I)
_DASH = re.compile(r"\s+-\s+")
_ROMAN = {"ii", "iii", "iv", "vi", "vii", "viii", "ix"}


@dataclass
class Title:
    base: str  # without versions, features, tags or other brackets: "Eyes On Fire"
    full: str  # base plus other bracketed words: "Griztronics II Another Level"
    version: Version  # the version that decides what this recording is
    versions: list[Version] = field(default_factory=list)  # every version written, in order
    featured: list[str] = field(default_factory=list)
    extras: list[str] = field(default_factory=list)


def parse_title(text: str) -> Title:
    s = norm_dashes(text or "").strip()
    versions: list[Version] = []
    featured: list[str] = []
    extras: list[str] = []

    def take(m: re.Match) -> str:
        bracket, inner = m.group(1), m.group(2).strip()
        if not inner:
            return " "
        f = _FEAT_INNER.match(inner)
        if f:
            featured.extend(split_artists(f.group(1)))
            return " "
        v = classify_version(inner)
        if v:
            versions.append(v)
            return " "
        if bracket == "[" or _NOISE.search(inner) or re.fullmatch(r"(?:19|20)\d\d", inner):
            return " "  # [LABEL], [FREE DOWNLOAD], (Out Now), (2024)
        extras.append(inner)
        return " "

    prev = None
    while prev != s:  # innermost brackets first, so nesting unwraps
        prev, s = s, _BRACKET.sub(take, s)
    m = _FEAT_TAIL.search(s)
    if m:
        featured.extend(split_artists(m.group(1)))
        s = s[: m.start()]
    parts = _DASH.split(s.strip())
    if len(parts) > 1:
        v = classify_version(parts[-1])
        if v:
            versions.append(v)
            s = " - ".join(parts[:-1])
    m = re.search(r"(?i)\s+vip\s*$", s)
    if m:
        versions.append(Version("vip", "VIP"))
        s = s[: m.start()]
    s = _NOISE_OUTSIDE.sub(" ", s)
    base = re.sub(r"\s+", " ", s).strip(" -–|")
    eff = next((v for v in reversed(versions) if v.kind != "neutral"), versions[-1] if versions else Version())
    return Title(base=base, full=" ".join([base] + extras).strip(), version=eff, versions=versions,
                 featured=featured, extras=extras)


def _numbers(s: str) -> list[str]:
    return sorted(str(int(w)) if w.isdigit() else w for w in fold(s).split() if w.isdigit() or w in _ROMAN)


def _sim(a: str, b: str) -> float:
    a, b = fold(a), fold(b)
    if not a or not b:
        return 0.0
    return max(fuzz.ratio(a, b), fuzz.token_sort_ratio(a, b))


def title_similarity(want: Title, got: Title, strict_extras: bool = False) -> float:
    """How alike two titles are (0..100). With strict_extras, words the result has in
    unrecognised brackets must also be on the tracklist line (SoundCloud uploads hide
    bootleg names there)."""
    got_forms = [got.full] if strict_extras else [got.base, got.full]
    return max(_sim(w, g) for w in (want.base, want.full) for g in got_forms)


# -- what we want --------------------------------------------------------------------


@dataclass
class Spec:
    artists: list[str]  # main artists as written
    featured: list[str]
    title: Title

    @property
    def version(self) -> Version:
        return self.title.version

    @property
    def own_keys(self) -> set[str]:
        return artist_keys(self.artists + self.featured + self.title.featured)

    @property
    def remixer_keys(self) -> set[str]:
        v = self.version
        return artist_keys([v.remixer]) if v.kind in ("remix", "vip") and v.remixer else set()


def parse_spec(artist: str, title: str) -> Spec:
    a = norm_dashes(artist or "").strip()
    m = _FEAT_SPLIT.search(a)
    main, feat = (a[: m.start()], a[m.end():]) if m else (a, "")
    t = parse_title(title)
    return Spec(artists=split_artists(main), featured=split_artists(feat), title=t)


def _join(parts: Iterable[str]) -> str:
    return re.sub(r"\s+", " ", " ".join(p for p in parts if p)).strip()


def search_queries(spec: Spec, source: str) -> list[str]:
    """What to type into the store's search box: artists, title and remixer. Never the cue
    time, label, featured artists or tags."""
    arts = spec.artists[:2]
    base = spec.title.base
    v = spec.version
    rmx = v.remixer if v.kind in ("remix", "vip") else ""
    vt = v.text if v.kind not in ("", "neutral") else ""
    if rmx and keys_match(artist_keys([rmx]), artist_keys(arts)):
        # "LUMBERJVCK - X (LUMBERJVCK VIP)": the remixer is already in the query
        vt = re.sub(re.escape(rmx), " ", vt, flags=re.I).strip()
        rmx = ""
    if source == "beatport":
        qs = [_join(arts + [base, rmx]), _join([base, rmx or (arts[0] if arts else "")])]
    else:
        qs = [_join(arts + [base, vt]), _join([base, vt]) if vt else ""]
    out: list[str] = []
    for q in qs:
        if q and q.lower() not in (o.lower() for o in out):
            out.append(q)
    return out


# -- what we found -------------------------------------------------------------------


@dataclass
class Candidate:
    artists: list[str]
    title: Title
    version: Version
    remixers: list[str] = field(default_factory=list)
    uploader: str = ""
    version_texts: list[str] = field(default_factory=list)  # mix name and bracketed versions
    source: str = ""  # "beatport" | "soundcloud"
    rank: int = 0  # position in the search results
    raw: Any = None

    @property
    def display(self) -> str:
        if self.source == "soundcloud":
            return f"{self.uploader}: {(self.raw or {}).get('title', '')}"
        t = self.raw or {}
        return f"{', '.join(self.artists)} - {t.get('name', self.title.base)}" + (f" ({t['mix']})" if t.get("mix") else "")


def beatport_candidate(t: dict, rank: int = 0) -> Candidate:
    """`t` is a track from beatport.tracks_from_next_data."""
    title = parse_title(t.get("name", ""))
    mix = (t.get("mix") or "").strip()
    remixers = list(t.get("remixers") or [])
    mv = (classify_version(mix) or Version("other", mix)) if mix else None
    if mv and mv.kind == "other" and remixers:  # "Ryan Davis Rethink", "John's Quest"
        mv = Version("remix", mix, ", ".join(remixers))
    eff = mv or title.version
    if (mv is None or mv.kind in ("", "neutral")) and title.version.kind not in ("", "neutral"):
        eff = title.version  # name says "(X Remix)" though the mix says Original Mix
    return Candidate(
        artists=list(t.get("artists") or []), remixers=remixers, title=title, version=eff,
        version_texts=[mix] + [v.text for v in title.versions], source="beatport", rank=rank, raw=t,
    )


def _split_dash(title: str) -> Optional[tuple[str, str]]:
    """Split 'Artist - Title' at the first ' - ' that is outside brackets."""
    depth = 0
    for i, ch in enumerate(title):
        if ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth = max(0, depth - 1)
        elif depth == 0 and title.startswith(" - ", i):
            left, right = title[:i].strip(), title[i + 3:].strip()
            if left and right:
                return left, right
    return None


_SC_TAG = re.compile(
    r"^\s*(?:\[[^\]]*\]|\((?:free|premiere|exclusive)[^)]*\)|(?:free\s*(?:download|dl|d/l)?|premiere|exclusive|out\s*now)\s*[:|])\s*",
    re.I,
)


def soundcloud_candidates(t: dict, rank: int = 0) -> list[Candidate]:
    """Every reading of a SoundCloud upload: 'Artist - Title', 'Title - Artist', or a bare
    title under the uploader's or publisher's name."""
    raw = norm_dashes(t.get("title") or "")
    while _SC_TAG.match(raw):  # "[FREE] ", "PREMIERE: ", "FREE DL | " in front of "Artist - Title"
        raw = _SC_TAG.sub("", raw, count=1)
    uploader = (t.get("user") or {}).get("username", "")
    meta = (t.get("publisher_metadata") or {}).get("artist") or ""
    extra = [meta] if meta.strip() else []
    readings: list[tuple[list[str], str]] = []
    parts = _split_dash(raw)
    if parts:
        readings += [([parts[0]] + extra, parts[1]), ([parts[1]] + extra, parts[0])]
    readings.append((extra, raw))
    out = []
    for artists, text in readings:
        title = parse_title(text)
        out.append(Candidate(artists=artists, title=title, version=title.version, uploader=uploader,
                             version_texts=[v.text for v in title.versions], source="soundcloud",
                             rank=rank, raw=t))
    return out


def is_mix_upload(t: dict) -> bool:
    return (t.get("full_duration") or t.get("duration") or 0) > MAX_TRACK_MS


# -- the rules -----------------------------------------------------------------------


@dataclass
class Verdict:
    ok: bool
    score: float  # for ranking accepted results
    reason: str  # why it was accepted or rejected
    title_score: float = 0.0
    artist_hit: bool = False


def _remixer_named(spec: Spec, c: Candidate) -> bool:
    """Is the wanted remixer named in the result's title or mix name?"""
    want = spec.remixer_keys
    if not want:
        return False
    found = (classify_version(t) for t in c.version_texts if t)
    named = artist_keys(v.remixer for v in found if v and v.remixer)
    if keys_match(want, named):
        return True
    text = compact(" ".join([c.title.full] + c.version_texts))
    return any(len(k) >= 4 and k in text for k in want)


def _version_check(spec: Spec, c: Candidate, credited: set[str]) -> tuple[bool, float, str]:
    """(accepted, score bonus, reason) for the version rules."""
    w, g = spec.version, c.version
    own, rkeys = spec.own_keys, spec.remixer_keys
    g_by = artist_keys([g.remixer]) if g.kind == "remix" and g.remixer else set()
    by_someone_else = bool(g_by) and not keys_match(g_by, rkeys | own) and not keys_match(rkeys, g_by)
    what = g.text or "a different version"

    if w.kind in ("remix", "vip") and w.remixer and not keys_match(rkeys, own):
        if w.kind == "vip" and g.kind != "vip":
            return False, 0, "not the VIP"
        if w.kind == "remix" and g.kind in ("vip", "instrumental", "acapella"):
            return False, 0, f"it is the {g.kind} version"
        if by_someone_else:
            return False, 0, f"it is {g.remixer}'s version, not {w.remixer}'s"
        if _remixer_named(spec, c):
            return True, 15, f"{w.remixer} named in the {'mix name' if c.source == 'beatport' else 'title'}"
        if keys_match(rkeys, credited):
            return True, 5, f"{w.remixer} credited as an artist"
        return False, 0, f"{w.remixer} is not on it"
    if w.kind == "remix":  # "(Remix)" with no remixer named, or the artist's own remix
        if g.kind != "remix":
            return False, 0, "not a remix"
        if by_someone_else and w.remixer:
            return False, 0, f"it is {g.remixer}'s version, not {w.remixer}'s"
        return True, 5, f"remix ({g.text})"
    if w.kind in ("vip", "instrumental", "acapella"):
        if g.kind != w.kind:
            return False, 0, f"not the {w.kind} version"
        return True, 10, f"{w.kind} version"
    if w.kind == "other":
        if g.kind != "other" or fuzz.token_set_ratio(fold(w.text), fold(g.text)) < 80:
            return False, 0, f"not the {w.text} version"
        return True, 5, f"{g.text} version"
    # The line names no version (or Original/Extended/Radio...).
    if g.kind in ("", "neutral"):
        if w.kind == "neutral" and fold(w.text) == fold(g.text):
            return True, 6, g.text
        gw = set(_version_words(g.text))
        if "extended" in gw:
            return True, 3, g.text
        if not gw or "original" in gw:
            return True, 2, g.text or "no version"
        return True, 0, g.text
    if g.kind == "remix" and g_by and keys_match(g_by, own) and (
        c.source != "soundcloud" or keys_match(g_by, artist_keys([c.uploader]))
    ):  # "LUMBERJVCK - Temperature" for his own flip; on SoundCloud only from his own account
        return True, -8, f"{g.text} (by an artist on the line)"
    if g.kind == "remix":
        return False, 0, f"it is a remix ({what}); the line names the original"
    return False, 0, f"it is the {what} version; the line names the original"


def judge(spec: Spec, c: Candidate) -> Verdict:
    ts = title_similarity(spec.title, c.title, strict_extras=c.source == "soundcloud")
    credited = artist_keys(c.artists + c.remixers + ([c.uploader] if c.uploader else []))
    want = spec.own_keys | spec.remixer_keys
    hits = keys_match(want, credited)
    artist_hit = bool(hits) or not want
    if ts < TITLE_MIN:
        return Verdict(False, 0, f"title is {c.title.base!r}", ts, artist_hit)
    if _numbers(spec.title.full) != _numbers(c.title.full):
        return Verdict(False, 0, "numbers in the title differ", ts, artist_hit)
    if not want and ts < 95:
        return Verdict(False, 0, "no artist to check and the title isn't exact", ts, artist_hit)
    if not artist_hit:
        return Verdict(False, 0, "different artist", ts, artist_hit)
    ok, bonus, why = _version_check(spec, c, credited)
    if not ok:
        return Verdict(False, 0, why, ts, artist_hit)
    main = artist_keys(spec.artists)
    share = len(keys_match(main, credited)) / len(main) if main else 1.0
    n_main = len(spec.artists)
    n_hit = round(share * n_main)
    reason = f"title {ts:.0f} · artists {n_hit}/{n_main}" if n_main else f"title {ts:.0f}"
    return Verdict(True, ts + 10 * share + bonus, f"{reason} · {why}", ts, artist_hit)


def rank(spec: Spec, cands: list[Candidate]) -> tuple[list[tuple[Candidate, Verdict]], Optional[tuple[Candidate, Verdict]]]:
    """Accepted results best-first (one per source item), and the closest rejected one."""
    best_per_item: dict[int, tuple[Candidate, Verdict]] = {}
    rejected: list[tuple[Candidate, Verdict]] = []
    for c in cands:
        v = judge(spec, c)
        if not v.ok:
            rejected.append((c, v))
            continue
        cur = best_per_item.get(id(c.raw))
        if cur is None or v.score > cur[1].score:
            best_per_item[id(c.raw)] = (c, v)
    accepted = sorted(best_per_item.values(), key=lambda cv: (-cv[1].score, cv[0].rank))
    closest = None
    if not accepted and rejected:
        closest = max(rejected, key=lambda cv: (cv[1].artist_hit, cv[1].title_score, -cv[0].rank))
    return accepted, closest
