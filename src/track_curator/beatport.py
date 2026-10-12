"""Beatport: match tracklist entries to Beatport tracks, then fill a named cart.

Search reads the JSON that beatport.com embeds in its own search page (__NEXT_DATA__),
so no API key is needed. Each result is checked by matching.judge before it is accepted.
Adding to a cart uses a real browser window logged in as you, because Beatport has no
public cart API: the run's cart is created (or reused) on the Cart Preferences page, then
ticked in each track's cart list (the down arrow next to its buy button).
"""
from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any, Callable, Iterator, Optional

from bs4 import BeautifulSoup

from .http import Http
from .matching import MAX_RESULTS, beatport_candidate, judge, parse_spec, rank, search_queries
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
    release = d.get("release") if isinstance(d.get("release"), dict) else {}
    label = d.get("label") or release.get("label")  # search results | track pages
    label = label if isinstance(label, dict) else {}
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

    def track(self, url: str) -> Optional[dict]:
        """The track a beatport.com/track/<slug>/<id> link points at, read from its page."""
        m = re.search(r"/track/([^/?#]+)/(\d+)", url or "")
        if not m:
            return None
        tid = int(m.group(2))
        html = self.http.text(f"{WEB}/track/{m.group(1)}/{tid}")
        return next((t for t in tracks_from_next_data(html) if t["id"] == tid), None)

    def match(self, entry: TrackEntry, hint_url: Optional[str] = None) -> tuple[Optional[BeatportMatch], str]:
        """The Beatport track this tracklist line names, or (None, why not).

        A Beatport link on the track's SoundCloud upload is checked with the same rules as a
        search result, since descriptions also link originals and other releases.
        """
        spec = parse_spec(entry.artist, entry.title)
        hint_note = ""
        if hint_url and "/track/" in hint_url:
            try:
                t = self.track(hint_url)
            except Exception:
                t = None  # page didn't load: search instead
            if t:
                c = beatport_candidate(t)
                v = judge(spec, c)
                if v.ok:
                    return _match(t, v.score, f"linked from its SoundCloud upload · {v.reason}"), ""
                hint_note = f"; its SoundCloud upload links {c.display}, rejected because {v.reason}"
        closest = None
        tried = []
        for q in search_queries(spec, "beatport"):
            tried.append(q)
            results = self.search(q)[:MAX_RESULTS]
            accepted, near = rank(spec, [beatport_candidate(t, i) for i, t in enumerate(results)])
            if accepted:
                c, v = accepted[0]
                return _match(c.raw, v.score, v.reason, q), ""
            if near and (closest is None or (near[1].artist_hit, near[1].title_score) > (closest[1].artist_hit, closest[1].title_score)):
                closest = near
        note = f"Beatport: no match in the top {MAX_RESULTS} for " + " / ".join(f'"{q}"' for q in tried)
        if closest:
            note += f"; closest: {closest[0].display}, rejected because {closest[1].reason}"
        return None, note + hint_note


def _match(t: dict, score: float, reason: str, query: str = "") -> BeatportMatch:
    return BeatportMatch(
        track_id=t["id"], url=track_url(t), artists=", ".join(t["artists"]), name=t["name"], mix=t["mix"],
        label=t["label"], price=t["price"], score=round(score, 1), reason=reason, query=query,
    )



# -- cart ------------------------------------------------------------------------------
#
# What the cart step relies on (beatport.com, October 2026):
# - Every page's header holds a hidden list of your carts: buttons labelled
#   "Open <cart>, <n> items" around an icon with data-testid "cart-<cart>".
# - Cart Preferences (/account/carts) lists your carts and has a "New Cart" button.
# - A track page's buy button shows the price, or "In Cart" once the track is in any of your
#   carts. The down arrow beside it opens a list of your carts, each with a checkbox, and
#   ticking one adds the track to that cart. The buy button itself adds to your default
#   cart, so it is only pressed with --default-cart.

# Text of a track's buy button: its price, or "In Cart".
BUY_TEXT = r"\bin cart\b|\badd to cart\b|^buy\b|[$€£]\s?\d|\d[.,]\d\d\s?[$€£]"


class CartPageError(RuntimeError):
    """The page's cart controls aren't where this version looks for them."""


class _StopCart(CartPageError):
    """Carrying on would put tracks in the wrong cart."""


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


def _n(count: int, word: str) -> str:
    return f"{count} {word}{'' if count == 1 else 's'}"


def _first_line(e: Exception) -> str:
    return (str(e).strip().splitlines() or [type(e).__name__])[0][:200]


def _poll(page, fn: Callable[[], Any], seconds: float, step_ms: int = 250) -> Any:
    """fn() until it returns something truthy or `seconds` pass (then its last value)."""
    end = time.monotonic() + seconds
    while True:
        try:
            value = fn()
        except Exception:  # the page navigated or re-rendered mid-call
            value = None
        if value or time.monotonic() >= end:
            return value
        page.wait_for_timeout(step_ms)


def _first_visible(locators: list, timeout_ms: int = 2500):
    """The first locator (of several guesses) that shows up on the page."""
    for loc in locators:
        try:
            loc.first.wait_for(state="visible", timeout=timeout_ms)
            return loc.first
        except Exception:
            continue
    return None


# Helpers shared by the page scripts below. A cart row is the largest element around one
# checkbox that holds no other checkbox; its label is its text.
_JS_LIB = r"""
  const BOXES = 'input[type=checkbox], [role=checkbox], [role=menuitemcheckbox], [role=switch], [aria-checked]';
  const CONSENT = '#onetrust-consent-sdk, #onetrust-pc-sdk, #onetrust-banner-sdk';
  const shown = e => {
    if (!e || e.nodeType !== 1) return false;
    const r = e.getBoundingClientRect();
    if (!r.width || !r.height) return false;
    for (let x = e; x; x = x.parentElement) {
      const s = getComputedStyle(x);
      if (s.display === 'none' || s.visibility === 'hidden' || s.opacity === '0') return false;
    }
    return true;
  };
  const text = e => ((e && e.innerText) || '').replace(/\s+/g, ' ').trim();
  const own = e => [...e.childNodes].filter(n => n.nodeType === 3).map(n => n.textContent).join('').replace(/\s+/g, ' ').trim();
  const unmark = a => document.querySelectorAll('[' + a + ']').forEach(e => e.removeAttribute(a));
  const ELL = /\s*(\.\.\.|…)$/;
  const short = r => ELL.test(r.label) ? r.label.replace(ELL, '') : null;
  const state = el => {
    for (const e of [el && (el.matches('input') ? el : el.querySelector('input[type=checkbox]')), el]) {
      if (!e) continue;
      if (e.matches('input')) return e.checked;
      const a = e.getAttribute('aria-checked'), d = e.getAttribute('data-state');
      if (a === 'true' || a === 'mixed' || d === 'checked') return true;
      if (a === 'false' || d === 'unchecked') return false;
    }
    return null;
  };
  const cartRows = () => {
    const all = [...document.querySelectorAll(BOXES)].filter(e => !e.closest(CONSENT));
    const outer = all.filter(e => !all.some(o => o !== e && o.contains(e)));
    const rows = [];
    for (const box of outer) {
      let row = box;
      for (let p = box.parentElement; p && p !== document.body; p = p.parentElement) {
        if (outer.some(o => o !== box && p.contains(o)) || text(p).length > 160) break;
        row = p;
      }
      if (!shown(row)) continue;
      const attrs = [row, ...row.querySelectorAll('*')].flatMap(e =>
        ['aria-label', 'title', 'data-testid', 'name', 'value'].map(a => e.getAttribute(a))).filter(Boolean);
      rows.push({box, row, label: text(row), attrs});
    }
    return rows;
  };
"""

_HEADER_CARTS_JS = r"""() => [...document.querySelectorAll('[aria-label^="Open "]')].map(b => {
  const m = (b.getAttribute('aria-label') || '').match(/^Open (.+), (\d+) items?$/);
  const icon = b.querySelector('[data-testid^="cart-"]');
  return m && icon ? {name: m[1], count: +m[2], testid: icon.getAttribute('data-testid')} : null;
}).filter(Boolean)"""

_SHOWS_TEXT_JS = "(name) => {" + _JS_LIB + r"""
  return [...document.querySelectorAll('body *')].some(e => own(e) === name && !e.closest(CONSENT) && shown(e));
}"""

# Marks the track's own buy button with data-tc-buy and returns its text and position.
# Buttons in rows that link to other tracks (related tracks, a playing track) are skipped:
# such a row is the first element around the button with a link to another track, and it
# is short and doesn't hold the page's title. Of the rest, the top-left one wins.
_BUY_JS = "(src) => {" + _JS_LIB + r"""
  const re = new RegExp(src, 'i');
  unmark('data-tc-buy');
  const tid = (location.pathname.match(/\/track\/[^/]+\/(\d+)/) || [])[1];
  const h1 = [...document.querySelectorAll('h1')].find(shown);
  const otherTrack = x => [...x.querySelectorAll('a[href*="/track/"]')].some(a => {
    const m = (a.getAttribute('href') || '').match(/\/track\/[^/?#]+\/(\d+)/);
    return m && m[1] !== tid;
  });
  const elsewhere = e => {
    for (let x = e.parentElement; x && x !== document.body; x = x.parentElement)
      if (otherTrack(x)) return !(h1 && x.contains(h1)) && x.getBoundingClientRect().height < 400;
    return false;
  };
  const hits = [...document.querySelectorAll('button, [role=button], a')].filter(e => {
    if (e.closest(CONSENT) || !shown(e) || elsewhere(e)) return false;
    const t = text(e), tag = (e.getAttribute('data-testid') || '') + ' ' + (typeof e.className === 'string' ? e.className : '');
    return (t.length <= 30 && re.test(t)) || /add-?to-?cart/i.test(tag);
  });
  const inner = hits.filter(e => !hits.some(o => o !== e && e.contains(o)));
  const box = e => e.getBoundingClientRect();
  inner.sort((a, b) => Math.abs(box(a).top - box(b).top) > 8 ? box(a).top - box(b).top : box(a).left - box(b).left);
  if (!inner.length) return null;
  inner[0].setAttribute('data-tc-buy', '');
  const r = box(inner[0]);
  return {text: text(inner[0]), x: r.left, y: r.top, w: r.width, h: r.height};
}"""

# Marks the down arrow drawn against the buy button's right edge with data-tc-caret.
_CARET_JS = "() => {" + _JS_LIB + r"""
  unmark('data-tc-caret');
  const buy = document.querySelector('[data-tc-buy]');
  if (!buy) return null;
  const b = buy.getBoundingClientRect();
  const fits = e => {
    if (!e || e.nodeType !== 1 || e === buy || e.contains(buy) || buy.contains(e)) return false;
    const r = e.getBoundingClientRect();
    return r.width > 0 && r.height > 0 && r.width <= 80 && r.left >= b.left + b.width / 2
      && r.left <= b.right + 24 && r.top < b.bottom && r.bottom > b.top;
  };
  const CLICK = 'button, [role=button], [aria-haspopup], [aria-expanded], a';
  let caret = null;
  for (let e = buy, i = 0; e && i < 3 && !caret; e = e.parentElement, i++)
    for (let s = e.nextElementSibling; s && !caret; s = s.nextElementSibling)
      caret = [s, ...s.querySelectorAll(CLICK)].find(fits) || null;
  if (!caret) {
    const at = document.elementFromPoint(Math.min(b.right + 6, innerWidth - 1), b.top + b.height / 2);
    const c = at && (at.closest(CLICK) || at);
    if (fits(c)) caret = c;
  }
  if (!caret) return null;
  caret.setAttribute('data-tc-caret', '');
  return [caret.getAttribute('aria-label'), caret.getAttribute('data-testid'),
    ...[...caret.querySelectorAll('[data-testid]')].map(x => x.getAttribute('data-testid'))].filter(Boolean).join(' ')
    || caret.tagName.toLowerCase();
}"""

# Whether the cart list is open: "boxes" when rows with checkboxes name your carts, "open"
# when "Main Cart" shows without a checkbox this version knows, else "".
_MENU_JS = "(names) => {" + _JS_LIB + r"""
  const known = r => r.attrs.some(a => a.startsWith('cart-'))
    || names.some(n => r.label === n || r.label.includes(n) || (short(r) && short(r).length >= 4 && n.startsWith(short(r))));
  if (cartRows().some(known)) return 'boxes';
  return [...document.querySelectorAll('body *')].some(e => /^(main cart|hold bin)$/i.test(own(e)) && shown(e)) ? 'open' : '';
}"""

# Finds the row for cart `name` in the open list and marks its checkbox (data-tc-box), row
# (data-tc-row) and label (data-tc-label). `carts` is every cart's full name in the
# header's order, used when the list shortens several names to the same beginning.
_CART_BOX_JS = "([name, carts]) => {" + _JS_LIB + r"""
  ['data-tc-box', 'data-tc-row', 'data-tc-label'].forEach(unmark);
  const rows = cartRows(), labels = rows.map(r => r.label);
  const tiers = [
    ['icon', r => r.attrs.includes('cart-' + name)],
    ['name', r => r.label === name || r.attrs.includes(name)],
    ['text', r => name.length >= 3 && r.label.includes(name)],
    ['shortened name', r => { const p = short(r); return p !== null && p.length >= 6 && name.startsWith(p); }],
  ];
  for (const [how, test] of tiers) {
    const hit = rows.filter(test);
    if (!hit.length) continue;
    let pick = hit.length === 1 ? hit[0] : null, byOrder = false;
    if (how === 'shortened name' && carts.length) {
      // Carts shown with the same beginning are told apart by their order, which the
      // header's full list shares with this one.
      const p = short(hit[0]);
      const same = carts.filter(c => c.startsWith(p) && !labels.includes(c));
      pick = null;
      if (hit.every(r => short(r) === p) && same.length === hit.length && same.includes(name)) {
        pick = hit[same.indexOf(name)];
        byOrder = hit.length > 1;
      }
    }
    if (!pick) return {ok: false, ambiguous: true, rows: labels,
                       error: "can't tell which cart in the list is " + name + ' (' + hit.map(r => r.label).join(', ') + ')'};
    pick.box.setAttribute('data-tc-box', '');
    pick.row.setAttribute('data-tc-row', '');
    const lab = pick.box.closest('label') || (pick.box.id && document.querySelector('label[for="' + CSS.escape(pick.box.id) + '"]'));
    if (lab) lab.setAttribute('data-tc-label', '');
    const r = pick.box.getBoundingClientRect();
    const at = r.width >= 4 && r.height >= 4 ? document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2) : null;
    const direct = !!at && (at === pick.box || pick.box.contains(at));
    return {ok: true, how, byOrder, direct, checked: state(pick.box), label: pick.label, rows: labels};
  }
  return {ok: false, rows: labels, error: rows.length ? 'no ' + name + ' in the cart list (it shows ' + labels.join(', ') + ')'
                                                      : 'the cart list has no checkboxes'};
}"""

# State of the marked checkbox: true, false, null (can't tell) or "gone" (the list closed).
_BOX_STATE_JS = "() => {" + _JS_LIB + r"""
  const box = document.querySelector('[data-tc-box]');
  if (!box || !shown(document.querySelector('[data-tc-row]') || box)) return 'gone';
  return state(box);
}"""


def _header_carts(page) -> list[dict]:
    """Your carts with their item counts, from the hidden cart list in Beatport's header."""
    try:
        carts = page.evaluate(_HEADER_CARTS_JS) or []
    except Exception:
        return []
    seen: set[str] = set()
    return [c for c in carts if not (c["name"] in seen or seen.add(c["name"]))]


def _count(carts: list[dict], name: str) -> Optional[int]:
    return next((c["count"] for c in carts if c["name"] == name or c["testid"] == f"cart-{name}"), None)


def _has_cart(page, name: str) -> bool:
    """Whether you have a cart called `name`: in the header's cart list, or shown on the page."""
    if _count(_header_carts(page), name) is not None:
        return True
    try:
        return bool(page.evaluate(_SHOWS_TEXT_JS, name))
    except Exception:
        return False


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
        page.locator("input[type='text']:not([placeholder*='search' i]):not([name*='search' i])"),
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
    if not _poll(page, lambda: _has_cart(page, name), 8):
        page.reload(wait_until="domcontentloaded")
        if not _poll(page, lambda: _has_cart(page, name), 15):
            raise RuntimeError("the new cart didn't show up on Cart Preferences")
    log(f"  Created the Beatport cart {name}")
    return name


_CONTROLS_JS = "(name) => {" + _JS_LIB + r"""
  const box = e => { const r = e.getBoundingClientRect(); return [r.left, r.top, r.width, r.height].map(Math.round); };
  const cls = e => (typeof e.className === 'string' ? e.className : '').slice(0, 120);
  const clean = s => s.replace(/\S+@\S+\.\w+/g, '[email]');
  const els = [...document.querySelectorAll('button, input, a, label, li, [role], [aria-checked], [aria-haspopup], '
    + '[aria-expanded], [data-testid], [data-tc-buy], [data-tc-caret], [data-tc-box], [data-tc-row]')].filter(e => !e.closest(CONSENT));
  const controls = [...els.filter(shown), ...els.filter(e => !shown(e))].slice(0, 600).map(e => ({
    tag: e.tagName.toLowerCase(), shown: shown(e), box: box(e), text: shown(e) ? clean(text(e).slice(0, 80)) : '',
    aria: e.getAttribute('aria-label'), testid: e.getAttribute('data-testid'), title: e.getAttribute('title'),
    role: e.getAttribute('role'), type: e.getAttribute('type'), placeholder: e.getAttribute('placeholder'),
    maxlength: e.getAttribute('maxlength'), checked: e.matches('input') ? e.checked : e.getAttribute('aria-checked'),
    popup: e.getAttribute('aria-haspopup'), expanded: e.getAttribute('aria-expanded'),
    picked: ['data-tc-buy', 'data-tc-caret', 'data-tc-box', 'data-tc-row', 'data-tc-label'].filter(a => e.hasAttribute(a)).join(' '),
    cls: cls(e)}));
  const stem = (name || '').slice(0, 8).toLowerCase();
  const texts = [...document.querySelectorAll('body *')].filter(e => !e.closest(CONSENT)).map(e => [e, own(e)])
    .filter(([e, t]) => t && t.length <= 60 && (/cart|bin/i.test(t) || (stem && t.toLowerCase().startsWith(stem))) && shown(e))
    .slice(0, 100).map(([e, t]) => ({tag: e.tagName.toLowerCase(), text: clean(t), box: box(e), cls: cls(e),
      parent: e.parentElement ? e.parentElement.tagName.toLowerCase() + ' ' + cls(e.parentElement) : ''}));
  return {controls, texts};
}"""


def _save_controls(page, path: Path, name: str = "") -> bool:
    """Write the page's controls (labels, roles, positions: no page source, cookies or
    tokens) plus a screenshot, so a selector that stopped working can be fixed."""
    path.parent.mkdir(parents=True, exist_ok=True)
    saved = False
    try:
        page.screenshot(path=str(path.with_suffix(".png")), full_page=True)
        saved = True
    except Exception:
        pass
    try:
        path.write_text(json.dumps({"url": page.url, **page.evaluate(_CONTROLS_JS, name)}, indent=1))
        saved = True
    except Exception:
        pass
    return saved


def ensure_cart(page, name: str, log: Callable[[str], None] = print,
                debug_dir: Optional[Path] = None) -> tuple[str, bool]:
    """Find your cart called `name`, or create it on Cart Preferences.

    Returns (the cart's name, whether it exists now). The name comes back shorter when
    Beatport limits the length of cart names.
    """
    if "/account/carts" not in page.url:
        page.goto(CARTS_URL, wait_until="domcontentloaded")
    new_cart = page.get_by_role("button", name=re.compile(r"new cart", re.I)).first
    _poll(page, lambda: _header_carts(page) or new_cart.is_visible(), 15)
    if _poll(page, lambda: _has_cart(page, name), 3):
        log(f"  Using your Beatport cart {name}")
        return name, True
    try:
        return _create_cart(page, name, log), True
    except Exception as e:
        log(f"  ! couldn't create the cart {name} by itself: {_first_line(e)}")
        if debug_dir and _save_controls(page, debug_dir / "beatport-cart-page.json", name):
            log(f"    (saved the page's button labels and a screenshot to {debug_dir}/beatport-cart-page.*)")
        return name, False


def _buy_button(page, seconds: float = 15) -> dict:
    buy = _poll(page, lambda: page.evaluate(_BUY_JS, BUY_TEXT), seconds)
    if not buy:
        raise RuntimeError("no buy button on the track page")
    return buy


def _open_cart_menu(page, names: list[str]) -> None:
    """Open the list of carts behind the down arrow next to the track's buy button."""
    def menu():
        return page.evaluate(_MENU_JS, names)

    if menu() == "boxes":
        return
    _buy_button(page)
    if page.evaluate(_CARET_JS):
        try:
            page.locator("[data-tc-caret]").first.click(timeout=3000)
        except Exception:
            pass
        if _poll(page, lambda: menu() == "boxes", 3):
            return
    if menu() != "open":
        # Nothing opened: click just right of the buy button, where the arrow is drawn.
        buy = _buy_button(page, 3)
        page.mouse.click(buy["x"] + buy["w"] + 6, buy["y"] + buy["h"] / 2)
        if _poll(page, lambda: menu() == "boxes", 3):
            return
    if menu() == "open":
        raise CartPageError("the cart list opened, but this version can't find its checkboxes")
    raise CartPageError("the arrow next to the buy button didn't open a list of carts")


def _find_box(page, cart: str, names: list[str]) -> dict:
    found = page.evaluate(_CART_BOX_JS, [cart, names])
    if found.get("ambiguous"):
        raise _StopCart(found["error"])
    if not found.get("ok"):
        raise CartPageError(found.get("error") or f"no {cart} in the cart list")
    return found


def _click_box(page, direct: bool) -> bool:
    """Click the marked checkbox: itself when it takes clicks, else its label or its row.
    Returns whether a click went through."""
    ways = [("[data-tc-box]", False)] if direct else []
    ways += [("[data-tc-label]", False), ("[data-tc-row]", False), ("[data-tc-box]", True)]
    for sel, force in ways:
        loc = page.locator(sel).first
        try:
            if loc.count():
                loc.click(timeout=2000, force=force)
                return True
        except Exception:
            continue
    return False


def _settle(page, cart: str, names: list[str], want: bool, before: Optional[int] = None) -> Optional[bool]:
    """The clicked box's state once it shows `want`, or after 6 seconds: True, False, or
    None when it can't be told.

    The list may close or redraw when clicked, and Beatport takes a moment to save, so once
    the box disappears the list is reopened for every look. When ticking, the cart's count
    in the header going up also counts.
    """
    end = time.monotonic() + 6
    reopen = False
    while True:
        try:
            state = page.evaluate(_BOX_STATE_JS)
            if state == "gone" or (reopen and state != want):
                reopen = True
                page.keyboard.press("Escape")
                _open_cart_menu(page, names)
                state = _find_box(page, cart, names)["checked"]
        except CartPageError:
            raise
        except Exception:
            state = not want  # mid-redraw: look again
        if state is None or state == want:
            return state
        if want and before is not None and (_count(_header_carts(page), cart) or 0) > before:
            return True
        if time.monotonic() >= end:
            return state
        page.wait_for_timeout(300)


def _tick(page, cart: str, names: list[str], before: Optional[int], direct: bool) -> Optional[bool]:
    """Tick the marked checkbox. True once it shows ticked, None when its state can't be read.

    If it still shows unticked, the page is reloaded and checked again before another click,
    so a slow save is never clicked a second time (which would untick it).
    """
    for _ in range(2):
        if not _click_box(page, direct):
            raise CartPageError("the cart's checkbox can't be clicked")
        state = _settle(page, cart, names, True, before)
        if state is not False:
            return state
        page.reload(wait_until="domcontentloaded")
        _open_cart_menu(page, names)
        if _find_box(page, cart, names)["checked"]:
            return True
    return False


def _add_here(page, cart: str) -> str:
    """Tick `cart` in the list under the buy button's down arrow on the open track page.

    Returns "added", "already", or "unconfirmed" when Beatport gives no sign either way.
    """
    _buy_button(page)
    header = _poll(page, lambda: _header_carts(page), 3) or []
    names = [c["name"] for c in header] or ["Main Cart", "Hold Bin"]
    names = names + [cart] if cart not in names else names
    before = _count(header, cart)
    _open_cart_menu(page, names)
    found = _find_box(page, cart, names)
    if found["checked"] is True:
        return "already"
    state = _tick(page, cart, names, before, found["direct"])
    if state is False:
        raise RuntimeError("the cart's checkbox didn't stay ticked")
    if not found["byOrder"]:
        return "added" if state else "unconfirmed"
    # Picked by position among carts the list shows with the same shortened name: check
    # that this cart's count went up, not another one's.
    def changed():
        now = _header_carts(page)
        return now if any(c["count"] != _count(header, c["name"]) for c in now) else None

    now = _poll(page, changed, 2.5)
    if not now:
        page.reload(wait_until="domcontentloaded")
        now = _poll(page, changed, 8)
    if not now or before is None or (_count(now, cart) or 0) > before:
        return "added"
    wrong = ", ".join(c["name"] for c in now if c["count"] > (_count(header, c["name"]) or 0)) or "another cart"
    undone = False
    try:  # take it back out
        _open_cart_menu(page, names)
        again = _find_box(page, cart, names)
        if again["checked"] and _click_box(page, again["direct"]):
            undone = _settle(page, cart, names, False) is False
    except Exception:
        pass
    raise _StopCart(
        f"it went into {wrong} instead" + (" and was taken back out" if undone else ", so take it out there")
        + f": the cart list shows several carts as \"{found['label']}\" and lists them in another order"
    )


def _fill_default(page, matches: list[BeatportMatch], log: Callable[[str], None]) -> None:
    """Press each track's buy button, which adds it to your default cart."""
    added = 0
    for m in matches:
        m.in_cart, m.cart_error = False, None
        what = f"{m.artists} - {m.name}" + (f" ({m.mix})" if m.mix else "")
        try:
            page.goto(m.url, wait_until="domcontentloaded")
            buy = _buy_button(page)
            if re.search(r"in cart", buy["text"], re.I):
                m.in_cart = True
                log(f"  = already in one of your carts: {what}")
                continue
            page.locator("[data-tc-buy]").first.click(timeout=5000)
            page.wait_for_timeout(1500)
            m.in_cart = True
            added += 1
            log(f"  + cart: {what}")
        except Exception as e:
            m.cart_error = _first_line(e)
            log(f"  ! couldn't add {what}: {m.cart_error}")
    log(f"  Added {added} of {len(matches)} tracks to your default cart.")


def fill_cart(
    matches: list[BeatportMatch],
    browser,
    cart_name: str = "",
    log: Callable[[str], None] = print,
    ask: Callable[[str], Any] = input,
    debug_dir: Optional[Path] = None,
) -> None:
    """Put the matches in your Beatport cart called `cart_name` by ticking it in each track's
    cart list (the down arrow next to the buy button). With no cart name, press each buy
    button instead, which adds to your default cart."""
    page = browser.page
    page.goto(CARTS_URL if cart_name else WEB, wait_until="domcontentloaded")
    ask("  Log in to Beatport in the browser window if you aren't already, then press Enter here... ")
    if "/auth/error" in page.url or "login" in page.url:
        raise RuntimeError(
            "Beatport login didn't complete. Run `track-curator login --reset` and log in again."
        )
    if not cart_name:
        _fill_default(page, matches, log)
        return
    cart_name, ok = ensure_cart(page, cart_name, log, debug_dir)
    if not ok:
        ask(f"  Create a cart called {cart_name} in the browser window (Cart Preferences > New Cart), "
            f"then press Enter here... ")
        page.goto(CARTS_URL, wait_until="domcontentloaded")
        if not _poll(page, lambda: _has_cart(page, cart_name), 15):
            log(f"  ! still no cart called {cart_name}, so no tracks were added")
            return
    start = _count(_header_carts(page), cart_name)
    added = already = stuck = 0
    saved = False
    for m in matches:
        m.in_cart, m.cart_error = False, None
        what = f"{m.artists} - {m.name}" + (f" ({m.mix})" if m.mix else "")
        try:
            page.goto(m.url, wait_until="domcontentloaded")
            got = _add_here(page, cart_name)
        except Exception as e:
            m.cart_error = _first_line(e)
            log(f"  ! couldn't add {what}: {m.cart_error}")
            if debug_dir and not saved:
                saved = _save_controls(page, debug_dir / "beatport-track-page.json", cart_name)
                if saved:
                    log(f"    (saved the track page's controls and a screenshot to {debug_dir}/beatport-track-page.*)")
            if isinstance(e, _StopCart):
                log(f"  Stopped adding. Beatport cuts long names short in that list, so rename or delete the "
                    f"older carts whose names start like {cart_name} (Cart Preferences), then run the cart step again.")
                break
            stuck += isinstance(e, CartPageError)
            if stuck >= 2 and not (added or already):
                log("  Stopped adding: the cart list on track pages doesn't work the way this version expects. "
                    "The files saved above show how the page is built.")
                break
            continue
        if got == "unconfirmed":
            m.cart_error = f"ticked {cart_name}, but Beatport didn't show it as added"
            log(f"  ? {what}: ticked {cart_name}, but Beatport didn't show it as added")
            continue
        m.in_cart = True
        if got == "already":
            already += 1
            log(f"  = already in {cart_name}: {what}")
        else:
            added += 1
            log(f"  + {cart_name}: {what}")
    log(f"  {added + already} of {_n(len(matches), 'track')} {'is' if added + already == 1 else 'are'} in your cart {cart_name}"
        + (f" ({already} already {'was' if already == 1 else 'were'})." if already else "."))
    page.goto(CARTS_URL, wait_until="domcontentloaded")
    total = _count(_poll(page, lambda: _header_carts(page), 8) or [], cart_name)
    if total is not None:
        log(f"  Beatport shows {_n(total, 'item')} in {cart_name}"
            + (f" ({start} before this run)." if start is not None else "."))
