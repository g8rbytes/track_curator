"""Beatport: match tracklist entries to Beatport tracks, then fill a named cart.

Search reads the JSON that beatport.com embeds in its own search page (__NEXT_DATA__),
so no API key is needed. Each result is checked by matching.judge before it is accepted.
Adding to a cart uses a real browser window logged in as you, because Beatport has no
public cart API: the run's cart is created (or reused) on the Cart Preferences page and
made your default cart, so each track's Buy button drops the track into it.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Callable, Iterator, Optional

from bs4 import BeautifulSoup

from .http import Http
from .matching import MAX_RESULTS, beatport_candidate, parse_spec, rank, search_queries
from .models import BeatportMatch, TrackEntry
from .textmatch import slugify

WEB = "https://www.beatport.com"
CARTS_URL = f"{WEB}/account/carts"


def _walk(o: Any) -> Iterator[dict]:
    if isinstance(o, dict):
        yield o
        for v in o.values():
            yield from _walk(v)
    elif isinstance(o, list):
        for v in o:
            yield from _walk(v)


def _names(lst: Any, remixers: bool) -> list[str]:
    """Artist names from a Beatport credit list, split into artists and remixers by role."""
    out: list[str] = []
    for a in lst or []:
        if not isinstance(a, dict):
            continue
        name = (a.get("artist_name") or a.get("name") or "").strip()
        is_remixer = "remixer" in (a.get("artist_type_name") or "").lower()
        if name and is_remixer == remixers and name not in out:
            out.append(name)
    return out


def _track(d: dict) -> Optional[dict]:
    tid = d.get("track_id") or (d.get("id") if "mix_name" in d else None)
    name = d.get("track_name") or d.get("name")
    if not tid or not name or "mix_name" not in d:
        return None
    price = d.get("price")
    if isinstance(price, dict):
        price = price.get("display") or price.get("value")
    label = d.get("label") if isinstance(d.get("label"), dict) else {}
    remixers = _names(d.get("artists"), remixers=True)
    for r in _names(d.get("remixers"), remixers=False):  # older pages list remixers separately
        if r not in remixers:
            remixers.append(r)
    return {
        "id": int(tid),
        "name": name,
        "mix": d.get("mix_name") or "",
        "artists": _names(d.get("artists"), remixers=False),
        "remixers": remixers,
        "label": label.get("label_name") or label.get("name") or "",
        "price": str(price or ""),
        "slug": d.get("slug") or slugify(name),
    }


def tracks_from_next_data(html: str) -> list[dict]:
    """Tracks from a beatport.com page's __NEXT_DATA__ blob, in the page's own order.

    On a search page that is the "search-tracks" query's result list; other pages fall
    back to every track-shaped object in the blob.
    """
    soup = BeautifulSoup(html, "html.parser")
    tag = soup.find("script", id="__NEXT_DATA__")
    if not tag or not tag.string:
        return []
    data = json.loads(tag.string)
    queries = (((data.get("props") or {}).get("pageProps") or {}).get("dehydratedState") or {}).get("queries") or []
    for q in queries:
        key = q.get("queryKey") or []
        if key and key[0] == "search-tracks":
            items = (((q.get("state") or {}).get("data") or {}).get("data")) or []
            return [t for t in (_track(d) for d in items if isinstance(d, dict)) if t]
    out, seen = [], set()
    for d in _walk(data):
        t = _track(d)
        if t and t["id"] not in seen:
            seen.add(t["id"])
            out.append(t)
    return out


def track_url(t: dict) -> str:
    return f"{WEB}/track/{t['slug']}/{t['id']}"


class Beatport:
    def __init__(self, http: Http):
        self.http = http

    def search(self, q: str) -> list[dict]:
        html = self.http.text(f"{WEB}/search/tracks", params={"q": q})
        return tracks_from_next_data(html)

    def match(self, entry: TrackEntry, hint_url: Optional[str] = None) -> tuple[Optional[BeatportMatch], str]:
        """The Beatport track this tracklist line names, or (None, why not)."""
        if hint_url and "/track/" in hint_url:
            m = re.search(r"/track/([^/]+)/(\d+)", hint_url)
            if m:
                return BeatportMatch(track_id=int(m.group(2)), url=hint_url, artists=entry.artist, name=entry.title,
                                     score=100.0, reason="linked from its SoundCloud upload"), ""
        spec = parse_spec(entry.artist, entry.title)
        closest = None
        tried = []
        for q in search_queries(spec, "beatport"):
            tried.append(q)
            results = self.search(q)[:MAX_RESULTS]
            accepted, near = rank(spec, [beatport_candidate(t, i) for i, t in enumerate(results)])
            if accepted:
                c, v = accepted[0]
                t = c.raw
                return BeatportMatch(
                    track_id=t["id"], url=track_url(t), artists=", ".join(t["artists"]), name=t["name"], mix=t["mix"],
                    label=t["label"], price=t["price"], score=round(v.score, 1), reason=v.reason, query=q,
                ), ""
            if near and (closest is None or (near[1].artist_hit, near[1].title_score) > (closest[1].artist_hit, closest[1].title_score)):
                closest = near
        note = f"Beatport: no match in the top {MAX_RESULTS} for " + " / ".join(f'"{q}"' for q in tried)
        if closest:
            note += f"; closest: {closest[0].display}, rejected because {closest[1].reason}"
        return None, note


# -- cart ------------------------------------------------------------------------------

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


def cart_name_for(requests: list[str], limit: int = 100) -> str:
    """The run's cart name: the request text, normalised like a Python identifier.

    "[LUMBERJVCK's Deep Cuts, 18-18]" -> "lumberjvcks_deep_cuts_18_18"
    """
    import unicodedata

    s = " ".join(requests)
    s = re.sub(r"https?://(www\.)?", "", s)
    s = unicodedata.normalize("NFKD", s.translate(str.maketrans({"ø": "o", "Ø": "O", "æ": "ae", "ß": "ss", "ł": "l"})))
    s = "".join(c for c in s if not unicodedata.combining(c)).lower()
    s = re.sub(r"['’‘`´]", "", s)
    s = re.sub(r"[^a-z0-9]+", "_", s).strip("_")
    return s[:limit].rstrip("_") or "track_curator"


def _first_visible(locators: list, timeout_ms: int = 2500):
    """The first locator (of several guesses) that shows up on the page."""
    for loc in locators:
        try:
            loc.first.wait_for(state="visible", timeout=timeout_ms)
            return loc.first
        except Exception:
            continue
    return None


def _cart_row(page, name: str):
    """The row on Cart Preferences that shows the cart called `name` (with its icons)."""
    label = page.get_by_text(name, exact=True)
    try:
        if label.count() == 0:
            return None
    except Exception:
        return None
    row = label.first.locator("xpath=ancestor::*[.//button][1]")
    return row if row.count() else label.first


def _create_cart(page, name: str, log: Callable[[str], None]) -> str:
    btn = _first_visible([
        page.get_by_role("button", name=re.compile(r"new cart", re.I)),
        page.get_by_text(re.compile(r"^\s*\+?\s*new cart\s*$", re.I)),
    ])
    if btn is None:
        raise RuntimeError("no New Cart button on Cart Preferences")
    btn.click()
    field = _first_visible([
        page.get_by_role("dialog").locator("input"),
        page.get_by_placeholder(re.compile(r"name", re.I)),
        page.locator("input[name*='name' i]"),
        page.locator("input[type='text']"),
    ])
    if field is None:
        raise RuntimeError("no name box after clicking New Cart")
    maxlen = field.get_attribute("maxlength")
    if maxlen and maxlen.isdigit() and int(maxlen) < len(name):
        log(f"  ! Beatport allows {maxlen} characters in a cart name; using {name[:int(maxlen)]!r}")
        name = name[: int(maxlen)]
    field.fill(name)
    save = _first_visible([
        page.get_by_role("button", name=re.compile(r"^\s*(save|create|ok)\s*$", re.I)),
        page.locator("button[type='submit']"),
    ])
    if save is None:
        field.press("Enter")
    else:
        save.click()
    page.wait_for_timeout(2000)
    if _cart_row(page, name) is None:
        raise RuntimeError("the new cart didn't appear on Cart Preferences")
    log(f"  Created the Beatport cart {name}")
    return name


def _make_default(page, name: str) -> None:
    """Open the cart's gear menu and choose its "default" option."""
    row = _cart_row(page, name)
    if row is None:
        raise RuntimeError(f"no cart called {name}")
    gear = _first_visible([
        row.locator("button[aria-label*='setting' i], button[title*='setting' i]"),
        row.locator("[data-testid*='setting' i], [data-testid*='gear' i], [aria-label*='gear' i]"),
        row.locator("button[aria-label*='edit' i], button[aria-label*='option' i], button[aria-label*='more' i]"),
        row.locator("[class*='gear' i], [class*='Gear'], [class*='setting' i], [class*='Setting']"),
    ], timeout_ms=1500)
    if gear is None:
        raise RuntimeError("no settings (gear) button next to the cart")
    gear.click()
    choice = _first_visible([
        page.get_by_role("checkbox", name=re.compile(r"default", re.I)),
        page.get_by_role("switch", name=re.compile(r"default", re.I)),
        page.get_by_role("menuitem", name=re.compile(r"default", re.I)),
        page.get_by_role("button", name=re.compile(r"default", re.I)),
        page.get_by_label(re.compile(r"default", re.I)),
        page.get_by_text(re.compile(r"(set|make|use).{0,12}default", re.I)),
    ], timeout_ms=1500)
    if choice is None:
        raise RuntimeError("no 'default' option in the cart's settings")
    choice.click()
    save = _first_visible([page.get_by_role("button", name=re.compile(r"^\s*save\s*$", re.I))], timeout_ms=1000)
    if save is not None:
        save.click()
    page.wait_for_timeout(1500)


_CONTROLS_JS = """() => [...document.querySelectorAll(
    'button, input, a, [role=menuitem], [role=checkbox], [role=switch], [role=dialog], [data-testid]')]
  .slice(0, 400).map(e => ({
    tag: e.tagName.toLowerCase(), text: (e.innerText || '').trim().slice(0, 80),
    aria: e.getAttribute('aria-label'), testid: e.getAttribute('data-testid'), title: e.getAttribute('title'),
    role: e.getAttribute('role'), type: e.getAttribute('type'), placeholder: e.getAttribute('placeholder'),
    maxlength: e.getAttribute('maxlength'), cls: (typeof e.className === 'string' ? e.className : '').slice(0, 120)}))"""


def _save_controls(page, path: Path) -> bool:
    """Write the page's buttons and inputs (labels only: no page source, cookies or tokens)
    plus a screenshot, so a selector that stopped working can be fixed."""
    path.parent.mkdir(parents=True, exist_ok=True)
    saved = False
    try:
        page.screenshot(path=str(path.with_suffix(".png")), full_page=True)
        saved = True
    except Exception:
        pass
    try:
        path.write_text(json.dumps({"url": page.url, "controls": page.evaluate(_CONTROLS_JS)}, indent=1))
        saved = True
    except Exception:
        pass
    return saved


def use_cart(page, name: str, log: Callable[[str], None] = print, debug_dir: Optional[Path] = None) -> tuple[str, bool]:
    """Create (or reuse) the cart called `name` and make it the default cart.

    Returns (the cart name used, whether every step went through by itself).
    """
    if "/account/carts" not in page.url:
        page.goto(CARTS_URL, wait_until="domcontentloaded")
    page.wait_for_timeout(2500)
    try:
        if _cart_row(page, name) is None:
            name = _create_cart(page, name, log)
        else:
            log(f"  Reusing the Beatport cart {name}")
        _make_default(page, name)
        return name, True
    except Exception as e:
        log(f"  ! couldn't set up the cart {name} by itself: {str(e).splitlines()[0][:160]}")
        if debug_dir and _save_controls(page, debug_dir / "beatport-cart-page.json"):
            log(f"    (saved the page's button labels and a screenshot to {debug_dir}/beatport-cart-page.*)")
        return name, False


def fill_cart(
    matches: list[BeatportMatch],
    browser,
    cart_name: str = "",
    log: Callable[[str], None] = print,
    ask: Callable[[str], Any] = input,
    debug_dir: Optional[Path] = None,
) -> None:
    """Put the matches in the cart called `cart_name` (or your current default cart when
    empty) by pressing each track's Buy button in your logged-in browser."""
    page = browser.page
    page.goto(CARTS_URL if cart_name else WEB, wait_until="domcontentloaded")
    ask("  Log in to Beatport in the browser window if you aren't already, then press Enter here... ")
    if "/auth/error" in page.url or "login" in page.url:
        raise RuntimeError(
            "Beatport login didn't complete. Run `track-curator login --reset` and log in again."
        )
    if cart_name:
        cart_name, _ = use_cart(page, cart_name, log, debug_dir)
        ask(
            f"  Check the browser window: {cart_name} should now be your default cart "
            f"(Account > Cart Preferences, {CARTS_URL}).\n"
            f"  Create it or set it as default there if it isn't, then press Enter to add {len(matches)} tracks... "
        )
    added = 0
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
            added += 1
            log(f"  + cart: {m.artists} - {m.name}" + (f" ({m.mix})" if m.mix else ""))
        except Exception as e:
            m.cart_error = str(e).splitlines()[0][:200]
            log(f"  ! couldn't add {m.url}: {m.cart_error}")
    where = f"the cart {cart_name}" if cart_name else "your default cart"
    log(f"  Added {added} of {len(matches)} tracks to {where}.")
    if cart_name:
        log(f"  {cart_name} stays your default cart, so Buy buttons on beatport.com keep filling it until you "
            f"pick another default in Cart Preferences.")
