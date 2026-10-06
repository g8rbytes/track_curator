"""Find the SoundCloud uploads behind a request (a series range or a single set)."""
from __future__ import annotations

import re
from collections import Counter
from typing import Optional

from rapidfuzz import fuzz

from .inputs import Request
from .models import Episode
from .soundcloud import SoundCloud, describe
from .textmatch import normalize, similarity

SHOW_MATCH = 80  # how closely "user + title" must contain the show name
SET_MATCH = 70
MIN_MIX_MS = 15 * 60 * 1000

_MARKED = re.compile(
    r"(?:#|\bep(?:isode)?\.?\s*|\bvol(?:ume)?\.?\s*|\bno\.?\s*|\bpart\s*|\bpt\.?\s*|\bchapter\s*)0*(\d{1,4})\b",
    re.I,
)
_BARE = re.compile(r"(?<![\d.:])0*(\d{1,4})(?![\d.:])")


def episode_number(title: str, show: str = "") -> Optional[int]:
    """Episode number from an upload title like "Deep Cuts #012" or "Deep Cuts Vol. 3"."""
    t = title
    m = _MARKED.search(t)
    if m:
        return int(m.group(1))
    # Drop numbers that are part of the show name itself (e.g. "Monstercat 2.0").
    for n in re.findall(r"\d+", show):
        t = re.sub(rf"\b{n}\b", " ", t, count=1)
    for m in _BARE.finditer(t):
        n = int(m.group(1))
        raw = m.group(0)
        if len(raw) == 4 and 1990 <= n <= 2099:
            continue  # a year
        return n
    return None


def show_score(show: str, t: dict) -> float:
    return fuzz.token_set_ratio(normalize(show), normalize(describe(t)))


def find_series(sc: SoundCloud, req: Request, log=print) -> list[Episode]:
    wanted = set(req.episodes)
    found: dict[int, Episode] = {}

    def consider(t: dict) -> None:
        if show_score(req.query, t) < SHOW_MATCH:
            return
        n = episode_number(t.get("title", ""), req.query)
        if n in wanted and n not in found:
            found[n] = _episode(req.query, n, t)

    hits = sc.search_tracks(req.query, limit=50)
    for t in hits:
        consider(t)
    # The uploader that owns most matching hits is almost certainly the show's account:
    # walking its uploads finds every episode, not just the ones search ranks highly.
    owners = Counter(
        t["user"]["id"] for t in hits if t.get("user") and show_score(req.query, t) >= SHOW_MATCH
    )
    for user_id, _ in owners.most_common(2):
        if wanted <= set(found):
            break
        for t in sc.user_tracks(user_id):
            consider(t)
    for n in sorted(wanted - set(found)):
        for t in sc.search_tracks(f"{req.query} {n}", limit=10):
            consider(t)
            if n in found:
                break
    out = []
    for n in sorted(wanted):
        if n in found:
            out.append(found[n])
        else:
            log(f"  ! episode {n} not found on SoundCloud; will try 1001Tracklists")
            out.append(Episode(label=f"{req.query} #{n}", number=n))
    return out


def find_set(sc: SoundCloud, req: Request) -> Episode:
    if req.url:
        t = sc.resolve(req.url)
        return _episode(t.get("title", req.url), None, t)
    best, best_s = None, 0.0
    for t in sc.search_tracks(req.query, limit=30):
        s = similarity(req.query, describe(t))
        if (t.get("full_duration") or t.get("duration") or 0) >= MIN_MIX_MS:
            s += 5
        if s > best_s:
            best, best_s = t, s
    if best is None or best_s < SET_MATCH:
        return Episode(label=req.query, notes=["not found on SoundCloud"])
    ep = _episode(req.query, None, best)
    ep.notes.append(f"SoundCloud match score {best_s:.0f}")
    return ep


def _episode(label: str, n: Optional[int], t: dict) -> Episode:
    return Episode(
        label=f"{label} #{n}" if n is not None else label,
        number=n,
        soundcloud_url=t.get("permalink_url", ""),
        soundcloud_title=t.get("title", ""),
        description=t.get("description") or "",
    )
