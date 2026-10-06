"""1001Tracklists: find a mix's tracklist page and read its tracks.

1001Tracklists runs bot protection. Plain HTTP is tried first; if the site serves a
check, a real browser window (Playwright, optional) is used so you can clear it once.
If search can't find the set, pass its URL directly (see README).
"""
from __future__ import annotations

import re
from typing import Optional
from urllib.parse import parse_qs, unquote, urljoin, urlparse

from bs4 import BeautifulSoup

from .http import Blocked, Http
from .models import TrackEntry
from .textmatch import normalize, similarity
from .tracklist_parser import is_id_text, parse_tracklist, split_artist_title

BASE = "https://www.1001tracklists.com"
MATCH = 65


class Tracklists1001:
    def __init__(self, http: Http, browser_factory=None):
        self.http = http
        self._browser_factory = browser_factory
        self._browser = None

    def _html(self, url: str, data: Optional[dict] = None, wait: Optional[str] = None) -> str:
        try:
            return self.http.text(url, data=data, method="POST" if data else "GET")
        except Blocked:
            if not self._browser_factory:
                raise
            if self._browser is None:
                self._browser = self._browser_factory()
            if data:  # browser path: use the site's GET search page instead
                url = f"{BASE}/search/result.php?" + "&".join(f"{k}={v}" for k, v in data.items())
            return self._browser.html(url, wait_selector=wait)

    # -- search -----------------------------------------------------------------
    def search(self, query: str, episode: Optional[int] = None) -> Optional[str]:
        """Best-matching tracklist URL for a set name, or None."""
        candidates: list[tuple[str, str]] = []
        try:
            html = self._html(
                f"{BASE}/search/result.php",
                data={"main_search": query, "search_selection": "9"},
                wait="a[href*='/tracklist/']",
            )
            candidates += _tracklist_links(html, BASE)
        except Blocked:
            pass
        except Exception:
            pass
        if not candidates:
            candidates += self._web_search(query)
        return pick_tracklist(candidates, query, episode)

    def _web_search(self, query: str) -> list[tuple[str, str]]:
        """Fallback: a web search restricted to 1001tracklists.com."""
        try:
            html = self.http.text(
                "https://html.duckduckgo.com/html/", params={"q": f"site:1001tracklists.com/tracklist {query}"}
            )
        except Exception:
            return []
        out = []
        soup = BeautifulSoup(html, "html.parser")
        for a in soup.select("a.result__a, a[href]"):
            href = a.get("href", "")
            if "uddg=" in href:
                href = unquote(parse_qs(urlparse(href).query).get("uddg", [""])[0])
            if "1001tracklists.com/tracklist/" in href:
                out.append((href.split("#")[0], a.get_text(" ", strip=True)))
        return out

    # -- tracklist --------------------------------------------------------------
    def tracklist(self, url: str) -> list[TrackEntry]:
        html = self._html(url, wait="div.tlpItem, [itemprop='tracks']")
        return parse_tracklist_page(html)

    def close(self) -> None:
        if self._browser:
            self._browser.close()


def _tracklist_links(html: str, base: str) -> list[tuple[str, str]]:
    soup = BeautifulSoup(html, "html.parser")
    out, seen = [], set()
    for a in soup.select("a[href*='/tracklist/']"):
        href = urljoin(base, a["href"]).split("#")[0]
        if href in seen:
            continue
        seen.add(href)
        out.append((href, a.get_text(" ", strip=True)))
    return out


def pick_tracklist(cands: list[tuple[str, str]], query: str, episode: Optional[int]) -> Optional[str]:
    best, best_s = None, 0.0
    for url, text in cands:
        slug = urlparse(url).path.rstrip("/").split("/")[-1].replace("-", " ")
        label = f"{text} {slug}"
        s = similarity(query, label)
        if episode is not None:
            nums = {int(n) for n in re.findall(r"\d+", label)}
            s += 15 if episode in nums else -40
        if s > best_s:
            best, best_s = url, s
    return best if best_s >= MATCH else None


def parse_tracklist_page(html: str) -> list[TrackEntry]:
    """Read tracks from a 1001Tracklists tracklist page.

    Each row is a `div.tlpItem`; its name is in `meta[itemprop=name]` or `.trackValue`,
    and its number cell reads "w/" for a track layered over the previous one.
    """
    soup = BeautifulSoup(html, "html.parser")
    rows = soup.select("div.tlpItem") or soup.select("[itemprop='tracks']")
    entries: list[TrackEntry] = []
    pos = 0
    for row in rows:
        name = ""
        meta = row.select_one("meta[itemprop='name']")
        if meta and meta.get("content"):
            name = meta["content"]
        if not name:
            tv = row.select_one(".trackValue")
            name = tv.get_text(" ", strip=True) if tv else ""
        name = re.sub(r"\s+", " ", name).strip()
        if not name:
            continue
        num_el = row.select_one("[id$='_tracknumber_value'], .fontXL, .trackNumber")
        num_txt = num_el.get_text(strip=True).lower() if num_el else ""
        layer = num_txt.startswith("w/") or "tlpSubTog" in (row.get("class") or [])
        at = split_artist_title(name)
        if at:
            artist, title = at
        else:
            artist, title = ("ID", "ID") if is_id_text(name) else ("", name)
        if not layer:
            pos += 1
        entries.append(
            TrackEntry(
                position=pos,
                artist=artist,
                title=title,
                raw=name,
                is_id=is_id_text(title) or (artist.upper() == "ID" and is_id_text(title)),
                is_layer=layer,
                source="1001tracklists",
            )
        )
    if not entries:  # page layout changed: fall back to the text parser
        text = soup.get_text("\n")
        entries = parse_tracklist(text, source="1001tracklists")
    return entries
