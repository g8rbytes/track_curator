"""Command line entry point.

  track-curator run "[LUMBERJVCK's Deep Cuts, 1-20]" "[Dirtyphonics @ Rampage Open Air 2026]"
  track-curator run "[LUMBERJVCK's Deep Cuts, 18-18]" --cart        # cart: lumberjvcks_deep_cuts_18_18
  track-curator run --sc-link https://soundcloud.com/<artist>/<set> --cart   # named after the upload
  track-curator run "[My Set]" --setlist setlist.txt --cart          # your own tracklist, one per line
  track-curator run -f sets.txt --cart
  track-curator tracklist "[Dirtyphonics @ Rampage Open Air 2026]"   # just print the tracklist
  track-curator cart ~/TrackCurator/<run>/result.json                  # fill the Beatport cart later
  track-curator gates ~/TrackCurator/<run>/result.json                 # open free-download gates
  track-curator report ~/TrackCurator/<run>/result.json                # rebuild the report page
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import webbrowser
from pathlib import Path

from . import __version__
from .beatport import cart_name_for
from .episodes import find_set
from .http import Http
from .inputs import Request, parse_requests
from .models import BeatportMatch, TrackEntry
from .textmatch import slugify
from .tracklist_parser import parse_setlist


def _out_dir(base: Path, reqs) -> Path:
    name = slugify(" + ".join(r.query or (r.url or r.url_1001 or "") for r in reqs))[:60]
    return base / f"{time.strftime('%Y%m%d-%H%M')}-{name}"


def _read_setlist(path: str) -> list[TrackEntry]:
    if path == "-":
        if sys.stdin.isatty():
            print("Paste the tracklist, then press Ctrl-D on a new line:")
        text = sys.stdin.read()
    else:
        text = Path(path).expanduser().read_text()
    tracks, skipped = parse_setlist(text)
    if skipped:
        print('Skipped these setlist lines, which don\'t read as "Artist - Title":')
        for line in skipped[:10]:
            print(f"  {line}")
        if len(skipped) > 10:
            print(f"  ...and {len(skipped) - 10} more")
    if not tracks:
        print("No tracks in the setlist: put one per line as Artist - Title", file=sys.stderr)
    return tracks


def cmd_run(a) -> int:
    from .pipeline import Curator, Options
    from .report import write_all

    lines = list(a.requests)
    if a.file:
        lines += Path(a.file).read_text().splitlines()
    reqs = parse_requests(lines)
    raw = [ln.strip() for ln in lines if ln.strip() and not ln.strip().startswith("#")]
    if a.sc_link or a.setlist:
        if len(reqs) > 1 or (reqs and len(reqs[0].episodes) > 1):
            print("--sc-link and --setlist are for one set: give at most one request, for one episode", file=sys.stderr)
            return 2
        reqs = reqs or [Request(query="")]
    if not reqs:
        print("Nothing to do: give a request like \"[Dirtyphonics @ Rampage Open Air 2026]\", a --sc-link or a --setlist",
              file=sys.stderr)
        return 2
    req = reqs[0]
    if a.tracklist_url:
        req.url_1001 = a.tracklist_url
    if a.sc_link:
        req.url = a.sc_link
    if a.setlist:
        req.setlist = _read_setlist(a.setlist)
        if not req.setlist:
            return 2
    opts = Options(
        out_dir=Path(a.base).expanduser(),  # set below, once the run has a name
        download=not a.no_download,
        beatport=not a.no_beatport,
        use_1001=not a.no_1001,
        browser=a.browser,
        include_layers=not a.skip_layers,
    )
    cur = Curator(opts, http=Http(offline=a.offline))
    if not raw:  # no request typed: name the run after the upload, else after the setlist file
        if req.url:
            req.query = find_set(cur.sc, req).soundcloud_title
        elif a.setlist != "-":
            req.query = Path(a.setlist).stem
        else:
            req.query = time.strftime("setlist %Y-%m-%d %H%M")
    cart_name = "" if a.default_cart else (a.cart_name or cart_name_for(raw or [req.query]))
    out = Path(a.out).expanduser() if a.out else _out_dir(Path(a.base).expanduser(), reqs)
    opts.out_dir = out
    result = cur.run(reqs, raw=raw, cart_name=cart_name)
    paths = write_all(result, out)
    if a.cart:
        _cart([t.beatport for t in result.tracks if t.beatport], cart_name, out)
        paths = write_all(result, out)  # record what made it into the cart
    counts: dict[str, int] = {}
    for t in result.tracks:
        counts[t.status] = counts.get(t.status, 0) + 1
    print("\nDone:", ", ".join(f"{k} {v}" for k, v in sorted(counts.items())))
    print(f"Report: {paths['html']}")
    if not a.no_open:
        webbrowser.open(paths["html"].as_uri())
    return 0


def cmd_tracklist(a) -> int:
    from .pipeline import Curator, Options

    reqs = parse_requests(a.requests)
    cur = Curator(Options(out_dir=Path("."), download=False, beatport=False, browser=a.browser), log=lambda s: print(s, file=sys.stderr))
    for req in reqs:
        for ep in cur.episodes(req):
            cur.fill_tracklist(ep, req)
            print(f"\n## {ep.label}  [{ep.tracklist_source}]")
            for t in ep.tracks:
                ts = f"[{t.timestamp}] " if t.timestamp else ""
                print(f"{'  w/' if t.is_layer else f'{t.position:>3}.'} {ts}{t.display}{'  (ID)' if t.is_id else ''}")
    return 0


def _load(path: str) -> dict:
    return json.loads(Path(path).expanduser().read_text())


def _cart(matches: list[BeatportMatch], cart_name: str = "", debug_dir: Path | None = None) -> None:
    from .beatport import fill_cart
    from .browser import Browser

    if not matches:
        print("No Beatport matches to add.")
        return
    print(f"\nAdding {len(matches)} tracks to " + (f"the Beatport cart {cart_name}" if cart_name else "your default Beatport cart"))
    b = Browser()
    try:
        fill_cart(matches, b, cart_name=cart_name, debug_dir=debug_dir)
    finally:
        b.close()


def cmd_cart(a) -> int:
    from .report import load_result, write_all

    path = Path(a.result).expanduser()
    data = _load(str(path))
    result = load_result(path)
    matches = [t.beatport for t in result.tracks if t.beatport]
    if a.only:
        keep = {int(x) for x in a.only.split(",")}
        matches = [m for m in matches if m.track_id in keep]
    if a.default_cart:
        cart_name = ""
    elif a.cart_name:
        cart_name = a.cart_name
    elif "cart_name" in data:
        cart_name = data["cart_name"]
    else:
        cart_name = ""
        print("This run predates named carts, so tracks go to your default cart (use --cart-name NAME for a new one).")
    _cart(matches, cart_name, path.parent)
    result.cart_name = cart_name or result.cart_name
    paths = write_all(result, path.parent)  # record what made it into the cart
    print(f"Report: {paths['html']}")
    return 0


def cmd_login(a) -> int:
    import shutil

    from .browser import Browser
    from .http import DEFAULT_HOME

    if a.reset:
        shutil.rmtree(DEFAULT_HOME / "browser", ignore_errors=True)
        print("Cleared the saved browser profile.")
    b = Browser()
    try:
        b.page.goto("https://www.beatport.com/", wait_until="domcontentloaded")
        input("Log in to Beatport in the browser window, then press Enter here to save the login... ")
    finally:
        b.close()
    return 0


def cmd_setup_browser(a) -> int:
    import subprocess

    return subprocess.call([sys.executable, "-m", "playwright", "install", "chromium"])


def cmd_report(a) -> int:
    from .report import load_result, render_html

    src = Path(a.result).expanduser().resolve()
    out = src.with_name("report.html")
    out.write_text(render_html(load_result(src), str(src)))
    print(f"Report: {out}")
    webbrowser.open(out.as_uri())
    return 0


def cmd_gates(a) -> int:
    data = _load(a.result)
    gates = [t for t in data["tracks"] if (t.get("free_download") or {}).get("kind") == "gate"]
    if not gates:
        print("No gated free downloads in this run.")
        return 0
    print(f"Opening {len(gates)} free-download pages. Finish each one in your browser; files land in your Downloads folder.")
    for t in gates:
        e = t["entry"]
        print(f"  {e['artist']} - {e['title']}: {t['free_download']['url']}")
        webbrowser.open_new_tab(t["free_download"]["url"])
        if a.pause:
            input("    Enter for the next one... ")
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="track-curator", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = p.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("run", help="tracklists -> free downloads -> Beatport list")
    r.add_argument("requests", nargs="*", help='e.g. "[LUMBERJVCK\'s Deep Cuts, 1-20]"')
    r.add_argument("-f", "--file", help="file with one request per line")
    r.add_argument("--sc-link", metavar="URL",
                   help="the set's SoundCloud link, instead of searching for it "
                        "(without a request, the run is named after it)")
    given = r.add_mutually_exclusive_group()
    given.add_argument("--setlist", metavar="FILE",
                       help="your own tracklist, one track per line (- reads stdin), instead of finding one "
                            "(without a request, the run is named after the file)")
    given.add_argument("--tracklist-url", help="use this 1001Tracklists page for the (single) request")
    r.add_argument("--base", default="~/TrackCurator", help="where run folders go (default ~/TrackCurator)")
    r.add_argument("--out", help="exact output folder")
    r.add_argument("--cart", action="store_true",
                   help="add Beatport matches to a cart named after the request, in a browser window")
    r.add_argument("--cart-name", help="name the Beatport cart yourself (default: the request, e.g. lumberjvcks_deep_cuts_18_18)")
    r.add_argument("--default-cart", action="store_true", help="press each track's buy button instead, which adds to your default cart")
    r.add_argument("--browser", action="store_true", help="use a browser window when a site shows a bot check")
    r.add_argument("--no-download", action="store_true")
    r.add_argument("--no-beatport", action="store_true")
    r.add_argument("--no-1001", action="store_true")
    r.add_argument("--skip-layers", action="store_true", help="ignore w/ layered tracks")
    r.add_argument("--offline", action="store_true", help="only use cached pages")
    r.add_argument("--no-open", action="store_true", help="don't open the report when done")
    r.set_defaults(fn=cmd_run)

    t = sub.add_parser("tracklist", help="print the tracklist(s) only")
    t.add_argument("requests", nargs="+")
    t.add_argument("--browser", action="store_true")
    t.set_defaults(fn=cmd_tracklist)

    c = sub.add_parser("cart", help="add a run's Beatport matches to its named cart")
    c.add_argument("result", help="path to a run's result.json")
    c.add_argument("--only", help="comma-separated Beatport track ids to add")
    c.add_argument("--cart-name", help="cart to fill (default: the one the run was named for)")
    c.add_argument("--default-cart", action="store_true", help="press each track's buy button instead, which adds to your default cart")
    c.set_defaults(fn=cmd_cart)

    lg = sub.add_parser("login", help="log in to Beatport once in the cart browser")
    lg.add_argument("--reset", action="store_true", help="forget the saved browser profile first")
    lg.set_defaults(fn=cmd_login)

    sb = sub.add_parser("setup-browser", help="download Playwright's Chromium (only needed without Google Chrome)")
    sb.set_defaults(fn=cmd_setup_browser)

    rp = sub.add_parser("report", help="rebuild a run's report.html from its result.json")
    rp.add_argument("result")
    rp.set_defaults(fn=cmd_report)

    g = sub.add_parser("gates", help="open a run's gated free downloads in your browser")
    g.add_argument("result")
    g.add_argument("--pause", action="store_true", help="wait for Enter between pages")
    g.set_defaults(fn=cmd_gates)

    a = p.parse_args(argv)
    try:
        return a.fn(a)
    except KeyboardInterrupt:
        return 130
    except Exception as e:  # keep the CLI readable; full trace with TRACK_CURATOR_DEBUG=1
        import os

        if os.environ.get("TRACK_CURATOR_DEBUG"):
            raise
        print(f"error: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
