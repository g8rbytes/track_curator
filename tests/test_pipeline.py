"""Run the whole pipeline against fake sites (no network)."""
from pathlib import Path

from track_curator.inputs import parse_request
from track_curator.pipeline import Curator, Options, merge_tracklists
from track_curator.report import write_all
from track_curator.tracklist_parser import parse_tracklist


class FakeSC:
    def __init__(self):
        self.mix = {"id": 1, "kind": "track", "title": "Deep Cuts #007", "user": {"id": 9, "username": "LUMBERJVCK"},
                    "permalink_url": "https://soundcloud.com/lumberjvck/deep-cuts-007", "duration": 3_600_000,
                    "description": "Tracklist\n1. ID - ID\n2. ID - ID\n3. A - Song"}
        self.free = {"id": 2, "kind": "track", "title": "Alpha - Free Thing [FREE DOWNLOAD]", "user": {"username": "Alpha"},
                     "permalink_url": "https://soundcloud.com/alpha/free-thing", "downloadable": True}
        self.gated = {"id": 3, "kind": "track", "title": "Beta - Gated", "user": {"username": "Beta"},
                      "permalink_url": "https://soundcloud.com/beta/gated", "purchase_url": "https://hypeddit.com/b/g",
                      "purchase_title": "Free Download"}

    def search_tracks(self, q, limit=20):
        return [t for t in (self.mix, self.free, self.gated) if any(w.lower() in (t["title"] + t["user"]["username"]).lower() for w in q.split())]

    def user_tracks(self, uid):
        return iter([self.mix])

    def resolve(self, url):
        return self.mix

    def download_url(self, t):
        return "https://files.example/free.wav"


class FakeTL:
    def search(self, q, n):
        return "https://www.1001tracklists.com/tracklist/x/deep-cuts-007.html"

    def tracklist(self, url):
        return parse_tracklist("1. Alpha - Free Thing\n2. Beta - Gated\n3. Gamma - Paid One\n4. ID - ID", source="1001tracklists")

    def close(self):
        pass


class FakeBP:
    def match(self, entry, hint=None):
        from track_curator.models import BeatportMatch
        if entry.artist == "Gamma":
            return BeatportMatch(track_id=5, url="https://www.beatport.com/track/paid-one/5", artists="Gamma",
                                 name="Paid One", price="$1.49", score=95)
        return None


class FakeHttp:
    def download(self, url, dest, hint):
        dest.mkdir(parents=True, exist_ok=True)
        p = dest / f"{hint}.wav"
        p.write_bytes(b"RIFF")
        return p


def test_end_to_end(tmp_path: Path):
    cur = Curator(Options(out_dir=tmp_path), http=FakeHttp(), log=lambda *_: None)
    cur.sc, cur.tl, cur.bp = FakeSC(), FakeTL(), FakeBP()
    res = cur.run([parse_request("[LUMBERJVCK Deep Cuts, 7]")])
    ep = res.episodes[0]
    assert ep.tracklist_source == "both"  # SoundCloud list was 2/3 IDs, so 1001Tracklists was used too
    by = {t.entry.display: t.status for t in res.tracks}
    assert by["Alpha - Free Thing"] == "downloaded"
    assert by["Beta - Gated"] == "gate"
    assert by["Gamma - Paid One"] == "beatport"
    assert by["A - Song"] == "not_found"
    paths = write_all(res, tmp_path)
    assert "Copy cart command" in paths["html"].read_text()
    assert (tmp_path / "downloads" / "Alpha - Free Thing.wav").exists()


def test_merge_prefers_fewer_ids():
    sc = parse_tracklist("1. ID - ID\n2. ID - ID\n3. X - Only On SC")
    tl = parse_tracklist("1. A - B\n2. C - D\n3. ID - ID")
    m = merge_tracklists(sc, tl)
    assert [e.display for e in m] == ["A - B", "C - D", "ID - ID", "X - Only On SC"]


def test_report_roundtrip(tmp_path: Path):
    from track_curator.report import load_result, render_html

    cur = Curator(Options(out_dir=tmp_path), http=FakeHttp(), log=lambda *_: None)
    cur.sc, cur.tl, cur.bp = FakeSC(), FakeTL(), FakeBP()
    paths = write_all(cur.run([parse_request("[LUMBERJVCK Deep Cuts, 7]")]), tmp_path)
    page = render_html(load_result(paths["json"]), str(paths["json"]))
    assert 'data-id="5"' in page and "Copy cart command" in page and str(paths["json"]) in page
