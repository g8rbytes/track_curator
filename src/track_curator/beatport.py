"""Beatport: match tracklist entries to Beatport tracks, then fill a cart.

Search reads the JSON that beatport.com embeds in its own search page (__NEXT_DATA__),
so no API key is needed. Adding to cart uses a real browser window logged in as you,
because Beatport has no public cart API.
"""
from __future__ import annotations

import json
import re
from typing import Any, Iterator, Optional

from bs4 import BeautifulSoup

from .http import Http
from .models import BeatportMatch, TrackEntry
from .textmatch import slugify, track_score

WEB = "https://www.beatport.com"
MATCH = 72


def _walk(o: Any) -> Iterator[dict]:
    if isinstance(o, dict):
        yield o
        for v in o.values():
            yield from _walk(v)
    elif isinstance(o, list):
        for v in o:
            yield from _walk(v)


def _artists(lst: Any) -> str:
    names = []
    for a in lst or []:
        if isinstance(a, dict):
            names.append(a.get("artist_name") or a.get("name") or "")
    return ", ".join(n for n in names if n)


def tracks_from_next_data(html: str) -> list[dict]:
    """Normalised track dicts from a beatport.com page's __NEXT_DATA__ blob."""
    soup = BeautifulSoup(html, "html.parser")
    tag = soup.find("script", id="__NEXT_DATA__")
    if not tag or not tag.string:
        return []
    data = json.loads(tag.string)
    out, seen = [], set()
    for d in _walk(data):
        tid = d.get("track_id") or (d.get("id") if "mix_name" in d else None)
        name = d.get("track_name") or d.get("name")
        if not tid or not name or "mix_name" not in d or tid in seen:
            continue
        seen.add(tid)
        price = d.get("price")
        if isinstance(price, dict):
            price = price.get("display") or price.get("value")
        label = d.get("label")
        out.append(
            {
                "id": int(tid),
                "name": name,
                "mix": d.get("mix_name") or "",
                "artists": _artists(d.get("artists")),
                "remixers": _artists(d.get("remixers")),
                "label": (label or {}).get("label_name") or (label or {}).get("name") or "" if isinstance(label, dict) else "",
                "price": str(price or ""),
                "slug": d.get("slug") or slugify(name),
            }
        )
    return out


class Beatport:
    def __init__(self, http: Http):
        self.http = http

    def search(self, q: str) -> list[dict]:
        html = self.http.text(f"{WEB}/search/tracks", params={"q": q, "per_page": 25})
        return tracks_from_next_data(html)

    def match(self, entry: TrackEntry, hint_url: Optional[str] = None) -> Optional[BeatportMatch]:
        if hint_url and "/track/" in hint_url:
            m = re.search(r"/track/([^/]+)/(\d+)", hint_url)
            if m:
                return BeatportMatch(track_id=int(m.group(2)), url=hint_url, artists=entry.artist,
                                     name=entry.title, score=100.0)
        best, best_s = None, 0.0
        for t in self.search(entry.query):
            full = t["name"] + (f" ({t['mix']})" if t["mix"] and t["mix"].lower() != "original mix" else "")
            s = track_score(entry.artist, entry.title, f"{t['artists']} {t['remixers']}", full)
            if s > best_s:
                best, best_s = t, s
        if not best or best_s < MATCH:
            return None
        return BeatportMatch(
            track_id=best["id"],
            url=f"{WEB}/track/{best['slug']}/{best['id']}",
            artists=best["artists"],
            name=best["name"],
            mix=best["mix"],
            label=best["label"],
            price=best["price"],
            score=best_s,
        )


CART_BUTTONS = (
    "[data-testid='add-to-cart-button']",
    "button[aria-label*='add to cart' i]",
    "button[title*='add to cart' i]",
    "button:has-text('Add to cart')",
    "[class*='AddToCart'] button",
    "button:has-text('$')",
    "button:has-text('€')",
    "button:has-text('£')",
)


def fill_cart(matches: list[BeatportMatch], browser, log=print) -> None:
    """Open each track page in your logged-in browser and press its add-to-cart button."""
    page = browser.page
    page.goto(WEB, wait_until="domcontentloaded")
    input("  Log in to Beatport in the browser window if you aren't already, then press Enter here... ")
    if "/auth/error" in page.url or "login" in page.url:
        raise RuntimeError(
            "Beatport login didn't complete. Run `track-curator login --reset` and log in again."
        )
    for m in matches:
        try:
            page.goto(m.url, wait_until="domcontentloaded")
            btn = None
            for sel in CART_BUTTONS:
                loc = page.locator(sel).first
                try:
                    loc.wait_for(state="visible", timeout=4000)
                    btn = loc
                    break
                except Exception:
                    continue
            if btn is None:
                raise RuntimeError("no add-to-cart button found on the track page")
            btn.click()
            page.wait_for_timeout(1200)
            m.in_cart = True
            log(f"  + cart: {m.artists} - {m.name}")
        except Exception as e:
            m.cart_error = str(e).splitlines()[0][:200]
            log(f"  ! couldn't add {m.url}: {m.cart_error}")
