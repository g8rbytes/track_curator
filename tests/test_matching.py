"""Match validation, built from the pilot run (LUMBERJVCK's Deep Cuts 18-20) where the
old fuzzy scoring put timestamp junk and originals-instead-of-flips in the cart."""
from track_curator.beatport import Beatport, cart_name_for
from track_curator.matching import (
    MAX_RESULTS,
    beatport_candidate,
    parse_spec,
    rank,
    search_queries,
    soundcloud_candidates,
)
from track_curator.models import TrackEntry
from track_curator.tracklist_parser import parse_tracklist


def bp(artists, name, mix="Original Mix", remixers=(), id=1):
    return {"id": id, "name": name, "mix": mix, "artists": list(artists), "remixers": list(remixers),
            "label": "", "price": "$1.49", "slug": "x"}


def pick(artist, title, results):
    spec = parse_spec(artist, title)
    accepted, closest = rank(spec, [beatport_candidate(t, i) for i, t in enumerate(results)])
    return (accepted[0][0].raw["id"] if accepted else None), (closest[1].reason if closest else "")


def sc(title, user, artist=None):
    return {"title": title, "user": {"username": user}, "publisher_metadata": {"artist": artist} if artist else {},
            "permalink_url": f"https://soundcloud.com/{user}/x"}


def pick_sc(artist, title, uploads):
    spec = parse_spec(artist, title)
    cands = [c for i, t in enumerate(uploads) for c in soundcloud_candidates(t, i)]
    accepted, closest = rank(spec, cands)
    return (accepted[0][0].raw["title"] if accepted else None), (closest[1].reason if closest else "")


def test_timestamp_junk_is_rejected():
    junk = [bp(["Vicente Cabanes"], "00"), bp(["Coil"], "00:00", id=2), bp(["Martin Villalba"], "36", id=3)]
    assert pick("Vincent, KnownAsNat", "Stereo Love", junk)[0] is None
    assert pick("SpaceCase, Marzan", "DARKHORSE", junk + [bp(["Colin McNeil"], "Darkhorse", id=4)])[0] is None


def test_plain_title_prefers_extended_mix():
    res = [bp(["WILL K", "Sanjin"], "Samurai", id=1), bp(["WILL K", "Sanjin"], "Samurai", "Extended Mix", id=2),
           bp(["WILL K", "Sanjin"], "Samurai", "Radio Edit", id=3)]
    assert pick("WILL K, Sanjin", "Samurai", res)[0] == 2
    assert pick("WILL K, Sanjin", "Samurai (Radio Edit)", res)[0] == 3


def test_flip_needs_its_remixer():
    original = bp(["Sean Paul"], "Temperature")
    got, why = pick("Sean Paul", "Temperature (LUMBERJVCK FLIP)", [original])
    assert got is None and "LUMBERJVCK is not on it" in why
    in_mix = bp(["Sean Paul"], "Temperature", "LUMBERJVCK Remix", remixers=["LUMBERJVCK"], id=2)
    as_artist = bp(["LUMBERJVCK"], "Temperature", id=3)
    assert pick("Sean Paul", "Temperature (LUMBERJVCK FLIP)", [original, as_artist])[0] == 3
    # named in the mix beats credited as an artist
    assert pick("Sean Paul", "Temperature (LUMBERJVCK FLIP)", [original, as_artist, in_mix])[0] == 2
    other_flip = bp(["Tate McRae"], "Greedy", "LUMBERJVCK Flip", remixers=["LUMBERJVCK"], id=4)
    assert pick("Tate McRae", "Greedy (DIALYUP FLIP)", [bp(["Tate McRae"], "greedy"), other_flip])[0] is None


def test_flip_of_a_remix():
    want = ("Blue Foundation", "Eyes On Fire (Zeds Dead Remix)(LUMBERJVCK FLIP)")
    zeds = bp(["Blue Foundation"], "Eyes On Fire", "Zeds Dead Remix", remixers=["Zeds Dead"])
    assert pick(*want, [zeds])[0] is None
    tactix = sc("Blue Foundation - Eyes on Fire (Zeds Dead Remix)(TACTIX Flip)", "TACTIX")
    ours = sc("Blue Foundation - Eyes on Fire (Zeds Dead Remix)(LUMBERJVCK FLIP)", "LUMBERJVCK")
    assert pick_sc(*want, [tactix])[0] is None
    assert pick_sc(*want, [tactix, ours])[0] == ours["title"]


def test_original_rejects_other_peoples_versions():
    remix = bp(["Daniel Allan"], "Gloss", "oKay laFaye Remix", remixers=["oKay laFaye"])
    assert pick("Daniel Allan ft. y3kfae", "Gloss", [remix])[0] is None
    real = bp(["y3kfae", "Daniel Allan"], "Gloss (feat. y3kfae)", "Extended Mix", id=2)
    assert pick("Daniel Allan ft. y3kfae", "Gloss", [remix, real])[0] == 2
    assert pick_sc("Daniel Allan", "Gloss", [
        sc("Daniel Allan (ft. y3kfae) - Gloss (DUPPY33 BOOTLEG)", "DUPPY33"),
        sc("Daniel Allan - Gloss (TWK ReKnight) FREE DL", "The Wise Knight"),
        sc("Daniel Allan, y3kfae - GLOSS inst", "ioveslck"),
    ])[0] is None
    assert pick_sc("Daniel Allan", "Gloss", [sc("Gloss (feat. y3kfae)", "Daniel Allan", "Daniel Allan")])[0]


def test_artist_must_match():
    assert pick("Floret Loret", "Dogfight", [bp(["No Rules", "Flame"], "Dogfight")])[0] is None
    assert pick("Blurrd Vzn", "Bad Boi", [bp(["Poolhaus"], "Bad Boi", "Extended Mix")])[0] is None
    assert pick("zotti", "kitty", [bp(["Chinese Kitty"], "Kitty ")])[0] is None


def test_title_must_match_closely():
    assert pick("LUMBERJVCK, SubDocta", "THICC ASL", [bp(["Chuck Shadow"], "Thicc")])[0] is None
    assert pick("LUMBERJVCK, SubDocta", "THICC ASL", [bp(["LUMBERJVCK"], "THICC")])[0] is None
    assert pick("Villager", "VILLAGER RUN THAT AGAIN", [bp(["Willow (Workshop)"], "Villager")])[0] is None


def test_artist_spellings():
    assert pick("Mary Droppinz, SVDKO, Jon Cass", "CHECK", [bp(["JONCASS", "SVDKO", "Mary Droppinz"], "CHECK")])[0] == 1
    assert pick("Envy, JAZ IMSKY", "Codebreaker", [bp(["JAZ IMSKY", "En:vy"], "Codebreaker ")])[0] == 1
    assert pick("WiNK, Borne", "Drown", [bp(["WINK (US)", "Borne"], "Drown")])[0] == 1
    assert pick("Røhaan", "Pelican", [bp(["Rohaan"], "Pelican")])[0] == 1
    assert pick("Jkyl & Hyde, Nikita, the Wicked, Creg", "HORSEPOWER",
                [bp(["Nikita, the Wicked", "CREG", "Jkyl & Hyde"], "HORSEPOWER", "Extended Mix")])[0] == 1


def test_numbers_and_vip_must_agree():
    want = ("Sevdaliza ft. Villano Antillano, Tokischa", "Ride Or Die Pt. 2")
    assert pick(*want, [bp(["Sevdaliza"], "Ride Or Die, Pt. 1")])[0] is None
    assert pick(*want, [bp(["Tokischa", "Villano Antillano", "Sevdaliza"], "Ride or Die, Pt. 2")])[0] == 1
    assert pick("Excision", "Throwin Elbows VIP", [bp(["Excision"], "Throwin Elbows")])[0] is None
    assert pick("Excision", "Throwin Elbows VIP", [bp(["Excision"], "Throwin Elbows", "VIP")])[0] == 1
    assert pick("Excision", "Throwin Elbows", [bp(["Excision"], "Throwin Elbows", "VIP")])[0] is None


def test_soundcloud_slowed_and_reposts():
    want = ("Sevdaliza ft. Villano Antillano, Tokischa", "Ride Or Die Pt. 2 (LUMBERJVCK FLIP)")
    assert pick_sc(*want, [
        sc("Ride Or Die Pt.2 (SLOWED + REVERB) – Sevdaliza, Villano Antillano, Tokischa.", "bokkdrug"),
        sc("SEVDALIZA - RIDE OR DIE PT. 2 FT. TOKISCHA & VILLANO ANTILLANO", "TATADA", "SEVDALIZA"),
    ])[0] is None
    assert pick_sc("Paige Eliza, Casey Club", "Virtual Girl",
                   [sc("Casey Club & Paige Eliza - Virtual Girl", "Casey Club", "Casey Club")])[0]


def test_queries_never_carry_timestamps():
    t = parse_tracklist("TRACKLIST:\n1. Vincent, KnownAsNat - Stereo Love 00:00:00\n"
                        "2. Sean Paul - Temperature (LUMBERJVCK FLIP) 00:02:03\n3. Kercha - Forbidden Truth 00:42:39")
    assert [e.timestamp for e in t] == ["00:00:00", "00:02:03", "00:42:39"]
    for e in t:
        for source in ("beatport", "soundcloud"):
            for q in search_queries(parse_spec(e.artist, e.title), source):
                assert ":" not in q and "00" not in q, q
    assert search_queries(parse_spec(t[1].artist, t[1].title), "beatport")[0] == "Sean Paul Temperature LUMBERJVCK"


def test_only_the_top_results_are_considered():
    class FakeBeatport(Beatport):
        def search(self, q):
            return [bp(["Someone"], f"Junk {i}", id=i) for i in range(MAX_RESULTS)] + [bp(["Kercha"], "Forbidden Truth", id=99)]

    entry = TrackEntry(position=1, artist="Kercha", title="Forbidden Truth")
    match, why = FakeBeatport(http=None).match(entry)
    assert match is None and f"top {MAX_RESULTS}" in why and "Junk" in why


def test_cart_name():
    assert cart_name_for(["[LUMBERJVCK's Deep Cuts, 18-18]"]) == "lumberjvcks_deep_cuts_18_18"
    assert cart_name_for(["[Dirtyphonics @ Rampage Open Air 2026]"]) == "dirtyphonics_rampage_open_air_2026"
    assert cart_name_for(["[A, 1-2]", "[Røhaan @ Café 2026]"]) == "a_1_2_rohaan_cafe_2026"


def test_soundcloud_promo_tags_and_years():
    want = ("Joey Tuckshop", "Hold Me")
    for title in ("FREE DL: Joey Tuckshop - Hold Me", "[FREE] Joey Tuckshop - Hold Me (2024)",
                  "[DUBSTEP] Joey Tuckshop - Hold Me [FREE DOWNLOAD]"):
        assert pick_sc(*want, [sc(title, "Bass Channel")])[0] == title, title
    assert pick_sc(*want, [sc("Joey Tuckshop - Hold Me (Sped Up)", "Bass Channel")])[0] is None
