"""Parse the user's request strings.

Accepted forms (brackets optional):
  [LUMBERJVCK's Deep Cuts, 1-20]   -> series with an episode range
  [LUMBERJVCK's Deep Cuts, 3, 7, 9-11]
  [Dirtyphonics @ Rampage Open Air 2026] -> a single set
  https://soundcloud.com/artist/some-mix  -> a specific upload
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional

from .models import TrackEntry

_RANGE_PART = re.compile(r"^\s*(\d+)\s*(?:-|–|—|\.\.|to)\s*(\d+)\s*$")
_NUM_PART = re.compile(r"^\s*#?(\d+)\s*$")


@dataclass
class Request:
    query: str  # show / set name to search for
    episodes: list[int] = field(default_factory=list)  # empty => single set
    url: Optional[str] = None  # explicit SoundCloud URL
    url_1001: Optional[str] = None  # explicit 1001Tracklists URL
    setlist: list[TrackEntry] = field(default_factory=list)  # the user's own tracklist (--setlist)

    @property
    def is_series(self) -> bool:
        return bool(self.episodes)


def _parse_numbers(parts: list[str]) -> Optional[list[int]]:
    nums: list[int] = []
    for p in parts:
        m = _RANGE_PART.match(p)
        if m:
            a, b = int(m.group(1)), int(m.group(2))
            if a > b:
                a, b = b, a
            nums.extend(range(a, b + 1))
            continue
        m = _NUM_PART.match(p)
        if m:
            nums.append(int(m.group(1)))
            continue
        return None
    return sorted(set(nums))


def parse_request(text: str) -> Request:
    s = text.strip()
    if s.startswith("[") and s.endswith("]"):
        s = s[1:-1].strip()
    s = s.strip("\"'“”")

    if re.match(r"https?://", s):
        if "1001tracklists.com" in s:
            return Request(query="", url_1001=s)
        return Request(query="", url=s)

    # Split off trailing comma-separated numbers / ranges.
    if "," in s:
        # Allow commas inside the title: take the longest suffix that is all numbers.
        pieces = s.split(",")
        for i in range(1, len(pieces)):
            nums = _parse_numbers(pieces[i:])
            if nums:
                return Request(query=",".join(pieces[:i]).strip(), episodes=nums)
    return Request(query=s)


def parse_requests(lines: list[str]) -> list[Request]:
    out = []
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        # A line may contain several bracketed requests.
        found = re.findall(r"\[[^\]]+\]", line)
        if found:
            out.extend(parse_request(f) for f in found)
        else:
            out.append(parse_request(line))
    return out
