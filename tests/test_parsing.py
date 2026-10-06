from track_curator.beatport import tracks_from_next_data
from track_curator.episodes import episode_number
from track_curator.freedl import find_free_download
from track_curator.inputs import parse_request, parse_requests
from track_curator.tracklist_parser import id_ratio, parse_tracklist
from track_curator.tracklists1001 import parse_tracklist_page, pick_tracklist


def test_inputs():
    r = parse_request("[LUMBERJVCK's Deep Cuts, 1-20]")
    assert r.query == "LUMBERJVCK's Deep Cuts" and r.episodes == list(range(1, 21))
    r = parse_request("[Dirtyphonics @ Rampage Open Air 2026]")
    assert r.query == "Dirtyphonics @ Rampage Open Air 2026" and not r.is_series
    r = parse_request("[Monstercat Call of the Wild, Uncaged, 3, 7, 9-10]")
    assert r.query == "Monstercat Call of the Wild, Uncaged" and r.episodes == [3, 7, 9, 10]
    assert parse_request("https://soundcloud.com/a/b").url
    assert parse_request("https://www.1001tracklists.com/tracklist/x/y.html").url_1001
    assert len(parse_requests(["[A, 1-2] [B @ C 2026]", "# comment", ""])) == 2


DESC = """Deep Cuts episode 7! Follow me on IG https://instagram.com/x
Tracklist:
01. LUMBERJVCK - Bad Habit [DISCIPLE]
02. ID - ID
03) Virtual Riot – Idols (Getter Remix)
w/ Excision - Throwin Elbows
04 Space Laces & Marauda - Hold It
[05] ID - ID
00:31:12 Subtronics - Griztronics II (Another Level)
"""


def test_soundcloud_description():
    t = parse_tracklist(DESC)
    assert [e.position for e in t] == [1, 2, 3, 3, 4, 5, 6]
    assert t[0].artist == "LUMBERJVCK" and t[0].title == "Bad Habit"
    assert t[2].title == "Idols (Getter Remix)"
    assert t[3].is_layer and t[3].artist == "Excision"
    assert t[6].artist == "Subtronics"
    assert t[1].is_id and t[5].is_id
    assert abs(id_ratio(t) - 2 / 6) < 1e-9


def test_no_header_needs_numbers():
    assert parse_tracklist("Thanks for listening!\nArtist - Title\nFoo - Bar\nBaz - Qux") == []
    assert len(parse_tracklist("1. A - B\n2. C - D\n3. E - F")) == 3


def test_header_fuzzy_and_unnumbered():
    t = parse_tracklist("great show\nTrack-list:\nA - B\nC - D\nID\nE - F (VIP)")
    assert [e.display for e in t] == ["A - B", "C - D", "ID - ID", "E - F (VIP)"]


def test_mostly_ids():
    t = parse_tracklist("1. ID - ID\n2. ID\n3. Artist - ID\n4. A - B")
    assert id_ratio(t) == 0.75


def test_episode_number():
    assert episode_number("LUMBERJVCK's Deep Cuts #012") == 12
    assert episode_number("Deep Cuts Vol. 3 (2025)") == 3
    assert episode_number("Deep Cuts 2024 Recap") is None
    assert episode_number("Deep Cuts - Episode 7") == 7
    assert episode_number("Monstercat 2.0 - 45", "Monstercat 2.0") == 45


def test_free_download_kinds():
    assert find_free_download({"downloadable": True, "permalink_url": "u"}).kind == "soundcloud"
    fd = find_free_download({"title": "X - Y [FREE DOWNLOAD]", "purchase_url": "https://hypeddit.com/x/y",
                             "purchase_title": "FREE DL"})
    assert fd.kind == "gate" and fd.host == "hypeddit.com"
    fd = find_free_download({"title": "X - Y (Free DL)", "purchase_url": "https://www.dropbox.com/s/a/x.wav?dl=0"})
    assert fd.kind == "direct" and fd.url.endswith("dl=1")
    assert find_free_download({"title": "X - Y", "purchase_url": "https://www.beatport.com/track/y/1",
                               "purchase_title": "Buy"}) is None
    fd = find_free_download({"title": "X - Y", "description": "FREE DOWNLOAD: https://drive.google.com/file/d/ABC/view"})
    assert fd.kind == "direct" and "id=ABC" in fd.url


PAGE_1001 = """<html><body>
<div class="tlpItem" id="tlp_1"><span id="tlp1_tracknumber_value">01</span>
 <div itemprop="tracks"><meta itemprop="name" content="LUMBERJVCK - Bad Habit"></div></div>
<div class="tlpItem tlpSubTog" id="tlp_2"><span id="tlp2_tracknumber_value">w/</span>
 <span class="trackValue">Excision - Throwin Elbows</span></div>
<div class="tlpItem" id="tlp_3"><span id="tlp3_tracknumber_value">02</span>
 <span class="trackValue">ID - ID</span></div>
</body></html>"""


def test_1001_page():
    t = parse_tracklist_page(PAGE_1001)
    assert [(e.position, e.is_layer, e.is_id) for e in t] == [(1, False, False), (1, True, False), (2, False, True)]
    assert t[1].display == "Excision - Throwin Elbows"


def test_pick_tracklist():
    c = [("https://www.1001tracklists.com/tracklist/1/lumberjvck-deep-cuts-006-2025.html", "LUMBERJVCK - Deep Cuts 006"),
         ("https://www.1001tracklists.com/tracklist/2/lumberjvck-deep-cuts-007-2025.html", "LUMBERJVCK - Deep Cuts 007")]
    assert pick_tracklist(c, "LUMBERJVCK's Deep Cuts", 7).endswith("007-2025.html")
    assert pick_tracklist(c, "Dirtyphonics Rampage", None) is None


def test_beatport_next_data():
    html = """<script id="__NEXT_DATA__" type="application/json">{"props":{"pageProps":{"dehydratedState":
    {"queries":[{"state":{"data":{"data":[{"track_id":123,"track_name":"Bad Habit","mix_name":"Original Mix",
    "artists":[{"artist_name":"LUMBERJVCK"}],"label":{"label_name":"Disciple"},"price":{"display":"$1.49"}}]}}}]}}}}</script>"""
    t = tracks_from_next_data(html)
    assert t == [{"id": 123, "name": "Bad Habit", "mix": "Original Mix", "artists": "LUMBERJVCK", "remixers": "",
                  "label": "Disciple", "price": "$1.49", "slug": "bad-habit"}]
