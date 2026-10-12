"""The cart step against a stand-in for beatport.com's cart controls (tests/beatport_site),
in a real browser. Skipped when Playwright or a Chromium isn't available."""
import functools
import glob
import http.server
import json
import threading
import urllib.parse
from pathlib import Path

import pytest

import track_curator.beatport as B
from track_curator.models import BeatportMatch

sync_api = pytest.importorskip("playwright.sync_api")
SITE = Path(__file__).parent / "beatport_site"
CART = "lumberjvcks_deep_cuts_18_18"


@pytest.fixture(scope="module")
def site():
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(SITE))
    handler.log_message = lambda *a, **k: None
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_port}"
    srv.shutdown()


@pytest.fixture(scope="module")
def page(site):
    pw = sync_api.sync_playwright().start()
    browser = None
    for kw in [{}] + [{"executable_path": p} for p in glob.glob("/opt/pw-browsers/chromium-*/chrome-linux/chrome")]:
        try:
            browser = pw.chromium.launch(**kw)
            break
        except Exception:
            continue
    if browser is None:
        pw.stop()
        pytest.skip("no Chromium for Playwright")
    pg = browser.new_page(viewport={"width": 1280, "height": 900})
    pg.set_default_timeout(10000)
    yield pg
    browser.close()
    pw.stop()


@pytest.fixture(autouse=True)
def beatport_is_the_mock(site, monkeypatch):
    monkeypatch.setattr(B, "WEB", site)
    monkeypatch.setattr(B, "CARTS_URL", f"{site}/carts.html?/account/carts")


class Window:
    def __init__(self, page):
        self.page = page


def setup(page, site, *custom, main=(), variant="A", **kw):
    st = {"carts": [{"key": "cart", "name": "Main Cart", "items": list(main)}, {"key": "hold-bin", "name": "Hold Bin", "items": []}]
          + [{"key": n, "name": n, "items": list(items)} for n, items in custom],
          "def": "cart", "variant": variant, "buyClicks": 0, "caretClicks": 0, **kw}
    page.goto(f"{site}/setup.html#" + urllib.parse.quote(json.dumps(st)))
    page.wait_for_selector("#out:has-text('ok')")


def state(page, site) -> dict:
    page.goto(f"{site}/state.html")
    st = json.loads(page.inner_text("#out"))
    return {c["name"]: c["items"] for c in st["carts"]} | {"buy clicks": st["buyClicks"]}


def tracks(site, *ids):
    return [BeatportMatch(track_id=i if isinstance(i, int) else 0, url=f"{site}/track.html?id={i}", artists="A", name=f"T{i}")
            for i in ids]


def fill(page, ms, name, debug=None) -> list[str]:
    logs = []
    B.fill_cart(ms, Window(page), cart_name=name, log=logs.append, ask=lambda _: None, debug_dir=debug)
    return logs


def test_creates_the_cart_and_ticks_it_without_touching_buy_or_main_cart(page, site):
    setup(page, site, main=["1", "2"])
    ms = tracks(site, 1, 2, 3)
    logs = fill(page, ms, CART)
    st = state(page, site)
    assert st[CART] == ["1", "2", "3"] and st["Main Cart"] == ["1", "2"] and st["buy clicks"] == 0
    assert all(m.in_cart and not m.cart_error for m in ms)
    assert any("Created the Beatport cart" in x for x in logs) and any("Beatport shows 3 items" in x for x in logs)
    logs = fill(page, ms, CART)  # again: found, and nothing is unticked
    assert state(page, site)[CART] == ["1", "2", "3"]
    assert any("Using your Beatport cart" in x for x in logs) and sum("already in" in x for x in logs) == 3


def test_shortened_names_hidden_inputs_and_a_list_that_closes(page, site):
    setup(page, site, ("lumberjvcks_deep_cuts_17_17", ["9"]), (CART, []), variant="B")
    ms = tracks(site, 1, 2)
    fill(page, ms, CART)
    st = state(page, site)
    assert st[CART] == ["1", "2"] and st["lumberjvcks_deep_cuts_17_17"] == ["9"] and st["buy clicks"] == 0
    assert all(m.in_cart for m in ms)


def test_a_track_in_the_wrong_lookalike_cart_is_taken_back_out(page, site):
    setup(page, site, ("lumberjvcks_deep_cuts_17_17", ["9"]), (CART, []), variant="B", reverse=True)
    ms = tracks(site, 1, 2)
    logs = fill(page, ms, CART)
    st = state(page, site)
    assert st["lumberjvcks_deep_cuts_17_17"] == ["9"] and st[CART] == []
    assert not any(m.in_cart for m in ms) and "taken back out" in ms[0].cart_error
    assert any("Stopped adding" in x for x in logs) and ms[1].cart_error is None


def test_menuitemcheckbox_rows_and_a_div_arrow(page, site):
    setup(page, site, (CART, ["2"]), variant="C")
    ms = tracks(site, 1, 2, 3)
    fill(page, ms, CART)
    assert sorted(state(page, site)[CART]) == ["1", "2", "3"] and all(m.in_cart for m in ms)


def test_tracks_you_own_are_skipped(page, site):
    setup(page, site, (CART, []))
    ms = tracks(site, "owned1", "owned2", 3)
    fill(page, ms, CART)
    assert state(page, site)[CART] == ["3"]
    assert [m.in_cart for m in ms] == [False, False, True] and "no buy button" in ms[0].cart_error


def test_stops_and_saves_the_page_when_there_is_no_cart_list(page, site, tmp_path):
    setup(page, site, (CART, []), variant="E")
    ms = tracks(site, 1, 2, 3)
    logs = fill(page, ms, CART, debug=tmp_path)
    assert state(page, site)[CART] == [] and any("Stopped adding" in x for x in logs)
    assert ms[0].cart_error and ms[2].cart_error is None
    snap = json.loads((tmp_path / "beatport-track-page.json").read_text())
    assert (tmp_path / "beatport-track-page.png").exists()
    assert [c["text"] for c in snap["controls"] if c["picked"] == "data-tc-buy"] == ["$1.49"]


def test_default_cart_presses_buy(page, site):
    setup(page, site, main=["2"])
    ms = tracks(site, 1, 2)
    fill(page, ms, "")
    st = state(page, site)
    assert st["Main Cart"] == ["2", "1"] and st["buy clicks"] == 1


def test_long_names_are_cut_to_what_beatport_allows(page, site):
    setup(page, site)
    long = CART + "_and_a_much_longer_suffix_here"
    fill(page, tracks(site, 1), long)
    assert state(page, site)[long[:40]] == ["1"]
