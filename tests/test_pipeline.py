"""Run the whole pipeline against fake sites (no network)."""
from pathlib import Path

import io
import json
import re

import pytest

from track_curator.inputs import parse_request
from track_curator.pipeline import Curator, Options, merge_tracklists
from track_curator.report import write_all
from track_curator.tracklist_parser import parse_setlist, parse_tracklist


class FakeSC:
    def __init__(self):
        self.queries = []
        self.resolved = []
        self.mix = {"id": 1, "kind": "track", "title": "Deep Cuts #007", "user": {"id": 9, "username": "LUMBERJVCK"},
                    "permalink_url": "https://soundcloud.com/lumberjvck/deep-cuts-007", "duration": 3_600_000,
                    "description": "Tracklist\n1. ID - ID\n2. ID - ID\n3. A - Song"}
        self.free = {"id": 2, "kind": "track", "title": "Alpha - Free Thing [FREE DOWNLOAD]", "user": {"username": "Alpha"},
                     "permalink_url": "https://soundcloud.com/alpha/free-thing", "downloadable": True}
        self.gated = {"id": 3, "kind": "track", "title": "Beta - Gated", "user": {"username": "Beta"},
                      "permalink_url": "https://soundcloud.com/beta/gated", "purchase_url": "https://hypeddit.com/b/g",
                      "purchase_title": "Free Download"}

    def search_tracks(self, q, limit=20):
        self.queries.append(q)
        return [t for t in (self.mix, self.free, self.gated) if any(w.lower() in (t["title"] + t["user"]["username"]).lower() for w in q.split())]

    def user_tracks(self, uid):
        return iter([self.mix])

    def resolve(self, url):
        self.resolved.append(url)
        return self.mix

    def download_url(self, t):
        return "https://files.example/free.wav"


class FakeTL:
    def __init__(self):
        self.calls = []

    def search(self, q, n):
        self.calls.append(q)
        return "https://www.1001tracklists.com/tracklist/x/deep-cuts-007.html"

    def tracklist(self, url):
        self.calls.append(url)
        return parse_tracklist("1. Alpha - Free Thing 00:00\n2. Beta - Gated 03:10\n3. Gamma - Paid One [06:20]\n4. ID - ID 09:00",
                               source="1001tracklists")

    def close(self):
        pass


class FakeBP:
    def match(self, entry, hint=None):
        from track_curator.models import BeatportMatch
        if entry.artist == "Gamma":
            return BeatportMatch(track_id=5, url="https://www.beatport.com/track/paid-one/5", artists="Gamma",
                                 name="Paid One", price="$1.49", score=95, reason="title 100"), ""
        return None, "Beatport: nothing in the top 10"


class FakeHttp:
    def download(self, url, dest, hint):
        dest.mkdir(parents=True, exist_ok=True)
        p = dest / f"{hint}.wav"
        p.write_bytes(b"RIFF")
        return p


def test_end_to_end(tmp_path: Path):
    cur = Curator(Options(out_dir=tmp_path), http=FakeHttp(), log=lambda *_: None)
    cur.sc, cur.tl, cur.bp = FakeSC(), FakeTL(), FakeBP()
    res = cur.run([parse_request("[LUMBERJVCK Deep Cuts, 7]")], raw=["[LUMBERJVCK Deep Cuts, 7]"],
                  cart_name="lumberjvck_deep_cuts_7")
    ep = res.episodes[0]
    assert ep.tracklist_source == "both"  # SoundCloud list was 2/3 IDs, so 1001Tracklists was used too
    by = {t.entry.display: t.status for t in res.tracks}
    assert by["Alpha - Free Thing"] == "downloaded"
    assert by["Beta - Gated"] == "gate"
    assert by["Gamma - Paid One"] == "beatport"
    assert by["A - Song"] == "not_found"
    assert {t.entry.title: t.entry.timestamp for t in res.tracks}["Paid One"] == "06:20"
    assert not [q for q in cur.sc.queries if ":" in q]  # cue times never reach a search
    paths = write_all(res, tmp_path)
    page = paths["html"].read_text()
    assert "Copy cart command" in page and "lumberjvck_deep_cuts_7" in page and "06:20" in page
    assert '"cart_name": "lumberjvck_deep_cuts_7"' in paths["json"].read_text()
    assert "Paid One" in paths["csv"].read_text().splitlines()[3] and "06:20" in paths["csv"].read_text()
    assert (tmp_path / "downloads" / "Alpha - Free Thing.wav").exists()
    free = page[page.index("<h2 id='free'>"):]  # every free download, listed once under the table
    assert "Free downloads (2)" in free and "href='#free'" in page
    assert 'href="https://hypeddit.com/b/g"' in free and "Beta - Gated" in free and "free behind a gate" in free
    assert "Alpha - Free Thing" in free and "downloaded: Alpha - Free Thing.wav" in free
    assert "Gamma - Paid One" not in free


def test_merge_prefers_fewer_ids():
    sc = parse_tracklist("1. ID - ID\n2. ID - ID\n3. X - Only On SC")
    tl = parse_tracklist("1. A - B\n2. C - D\n3. ID - ID")
    m = merge_tracklists(sc, tl)
    assert [e.display for e in m] == ["A - B", "C - D", "ID - ID", "X - Only On SC"]


def test_report_roundtrip(tmp_path: Path):
    from track_curator.report import load_result, render_html

    cur = Curator(Options(out_dir=tmp_path), http=FakeHttp(), log=lambda *_: None)
    cur.sc, cur.tl, cur.bp = FakeSC(), FakeTL(), FakeBP()
    paths = write_all(cur.run([parse_request("[LUMBERJVCK Deep Cuts, 7]")], cart_name="deep_cuts_7"), tmp_path)
    loaded = load_result(paths["json"])
    assert loaded.cart_name == "deep_cuts_7"
    page = render_html(loaded, str(paths["json"]))
    assert 'data-id="5"' in page and "Copy cart command" in page and str(paths["json"]) in page
    assert "Gamma - Paid One" in page and "title 100" in page


def test_loads_results_from_older_versions(tmp_path: Path):
    from track_curator.report import load_result

    old = {"episodes": [{"label": "Deep Cuts #18", "tracks": [{"position": 1, "artist": "A", "title": "B 00:00:00",
                                                                "raw": "", "is_id": False, "is_layer": False, "source": ""}]}],
           "tracks": [{"entry": {"position": 1, "artist": "A", "title": "B", "raw": "", "is_id": False, "is_layer": False,
                                 "source": ""}, "episode": "Deep Cuts #18", "status": "beatport",
                       "beatport": {"track_id": 1, "url": "u", "artists": "A", "name": "B", "score": 90.0},
                       "brand_new_field": 1}]}
    p = tmp_path / "result.json"
    p.write_text(__import__("json").dumps(old))
    r = load_result(p)
    assert r.tracks[0].beatport.track_id == 1 and r.cart_name == ""


def test_cart_command_records_what_went_in(tmp_path: Path, monkeypatch):
    import track_curator.cli as cli

    cur = Curator(Options(out_dir=tmp_path), http=FakeHttp(), log=lambda *_: None)
    cur.sc, cur.tl, cur.bp = FakeSC(), FakeTL(), FakeBP()
    paths = write_all(cur.run([parse_request("[LUMBERJVCK Deep Cuts, 7]")], cart_name="deep_cuts_7"), tmp_path)
    used = []

    def fake_cart(matches, cart_name, debug_dir):
        used.append(cart_name)
        for m in matches:
            m.in_cart = True

    monkeypatch.setattr(cli, "_cart", fake_cart)
    assert cli.main(["cart", str(paths["json"])]) == 0
    assert used == ["deep_cuts_7"]
    saved = [t["beatport"] for t in __import__("json").loads(paths["json"].read_text())["tracks"] if t.get("beatport")]
    assert saved and all(b["in_cart"] for b in saved)
    assert "in cart" in paths["html"].read_text()


def _curator(tmp_path: Path) -> Curator:
    cur = Curator(Options(out_dir=tmp_path), http=FakeHttp(), log=lambda *_: None)
    cur.sc, cur.tl, cur.bp = FakeSC(), FakeTL(), FakeBP()
    return cur


def test_sc_link_skips_only_the_set_search(tmp_path: Path):
    cur = _curator(tmp_path)
    req = parse_request("[LUMBERJVCK Deep Cuts, 7]")
    req.url = "https://soundcloud.com/lumberjvck/deep-cuts-007?si=abc&utm_source=clipboard#t=1:23"
    ep = cur.run([req]).episodes[0]
    assert cur.sc.resolved == ["https://soundcloud.com/lumberjvck/deep-cuts-007"]
    assert not [q for q in cur.sc.queries if "Deep Cuts" in q]  # no looking for the set itself
    assert ep.label == "LUMBERJVCK Deep Cuts #7" and ep.number == 7
    assert ep.tracklist_source == "both"  # its description, then 1001Tracklists as usual


def test_setlist_skips_finding_the_set_and_its_tracklist(tmp_path: Path):
    cur = _curator(tmp_path)
    req = parse_request("[My Set]")
    req.setlist = parse_setlist("Alpha - Free Thing\nBeta - Gated\nGamma - Paid One")[0]
    res = cur.run([req])
    assert not cur.sc.resolved and not cur.tl.calls
    assert not [q for q in cur.sc.queries if "My Set" in q]
    ep = res.episodes[0]
    assert (ep.label, ep.tracklist_source, ep.soundcloud_url) == ("My Set", "setlist", "")
    assert {t.entry.display: t.status for t in res.tracks} == {
        "Alpha - Free Thing": "downloaded", "Beta - Gated": "gate", "Gamma - Paid One": "beatport"}
    assert "3 tracks from your setlist" in write_all(res, tmp_path)["html"].read_text()


def test_setlist_with_sc_link_uses_the_list(tmp_path: Path):
    cur = _curator(tmp_path)
    req = parse_request("https://soundcloud.com/lumberjvck/deep-cuts-007")
    req.setlist = parse_setlist("Gamma - Paid One")[0]
    ep = cur.run([req]).episodes[0]
    assert (ep.label, ep.tracklist_source) == ("Deep Cuts #007", "setlist")
    assert ep.soundcloud_url.endswith("/deep-cuts-007") and [e.display for e in ep.tracks] == ["Gamma - Paid One"]
    assert not cur.tl.calls


@pytest.fixture
def cli_run(tmp_path: Path, monkeypatch):
    """track-curator run ... against the fake sites; returns (exit code, the run's result.json)."""
    import track_curator.cli as cli
    import track_curator.pipeline as pipeline

    class FakeCurator(Curator):
        def __init__(self, opts, http=None, log=print):
            super().__init__(opts, http=FakeHttp(), log=lambda *_: None)
            self.sc, self.tl, self.bp = FakeSC(), FakeTL(), FakeBP()

    monkeypatch.setattr(pipeline, "Curator", FakeCurator)
    monkeypatch.setattr(cli, "Http", lambda **_: FakeHttp())

    def run(*args):
        before = set(tmp_path.glob("runs/*"))
        code = cli.main(["run", *args, "--base", str(tmp_path / "runs"), "--no-open"])
        new = set(tmp_path.glob("runs/*")) - before
        return code, (json.loads((new.pop() / "result.json").read_text()) if new else None)

    return run


def test_cli_names_a_run_after_the_upload_or_the_setlist(cli_run, tmp_path: Path, monkeypatch, capsys):
    code, data = cli_run("--sc-link", "https://soundcloud.com/lumberjvck/deep-cuts-007")
    assert code == 0 and data["cart_name"] == "deep_cuts_007" and data["requests"] == []
    assert [e["label"] for e in data["episodes"]] == ["Deep Cuts #007"]
    assert list((tmp_path / "runs").glob("*-deep-cuts-007"))

    setlist = tmp_path / "deep cuts 19.txt"
    setlist.write_text("Tracklist:\nGamma - Paid One\nnot a track\n")
    code, data = cli_run("--setlist", str(setlist))
    assert code == 0 and data["cart_name"] == "deep_cuts_19"
    assert "not a track" in capsys.readouterr().out  # the skipped line is shown
    code, data = cli_run("[LUMBERJVCK's Deep Cuts, 19]", "--setlist", str(setlist))
    assert data["cart_name"] == "lumberjvcks_deep_cuts_19"
    assert data["episodes"][0]["label"] == "LUMBERJVCK's Deep Cuts #19"

    monkeypatch.setattr("sys.stdin", io.StringIO("Gamma - Paid One\n"))
    code, data = cli_run("--setlist", "-")
    assert code == 0 and re.fullmatch(r"setlist_\d{4}_\d\d_\d\d_\d{4}", data["cart_name"])


def test_cli_setlist_needs_one_set(cli_run, tmp_path: Path):
    setlist = tmp_path / "s.txt"
    setlist.write_text("A - B\n")
    assert cli_run("[A]", "[B]", "--setlist", str(setlist))[0] == 2
    assert cli_run("[Show, 1-2]", "--sc-link", "https://soundcloud.com/a/b")[0] == 2
    (tmp_path / "empty.txt").write_text("nothing here\n")
    assert cli_run("--setlist", str(tmp_path / "empty.txt"))[0] == 2
    with pytest.raises(SystemExit):
        cli_run("--setlist", str(setlist), "--tracklist-url", "https://www.1001tracklists.com/tracklist/x.html")

