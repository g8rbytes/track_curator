"""The end-to-end run: request -> episodes -> tracklists -> free downloads -> Beatport."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

from .beatport import Beatport
from .episodes import find_series, find_set
from .freedl import beatport_link, find_free_download
from .http import Blocked, Http
from .inputs import Request
from .matching import MAX_RESULTS, is_mix_upload, parse_spec, rank, search_queries, soundcloud_candidates
from .models import CuratedTrack, Episode, TrackEntry
from .progress import Progress
from .soundcloud import SoundCloud
from .textmatch import normalize, similarity
from .tracklist_parser import id_ratio, parse_tracklist
from .tracklists1001 import Tracklists1001

ID_THRESHOLD = 0.5  # more IDs than this on SoundCloud -> also ask 1001Tracklists


@dataclass
class Options:
    out_dir: Path
    download: bool = True
    beatport: bool = True
    use_1001: bool = True
    browser: bool = False  # allow a real browser window for bot checks
    include_layers: bool = True


@dataclass
class RunResult:
    episodes: list[Episode] = field(default_factory=list)
    tracks: list[CuratedTrack] = field(default_factory=list)
    requests: list[str] = field(default_factory=list)  # what was asked for, as typed
    cart_name: str = ""  # the Beatport cart this run's matches go in


def merge_tracklists(sc: list[TrackEntry], tl: list[TrackEntry]) -> list[TrackEntry]:
    """Use whichever list has fewer IDs as the base and add anything only the other knows."""
    if not sc:
        return tl
    if not tl:
        return sc
    base, other = (tl, sc) if id_ratio(tl) <= id_ratio(sc) else (sc, tl)
    merged = list(base)
    for e in other:
        if e.is_id:
            continue
        if not any(similarity(e.display, b.display) >= 85 for b in merged if not b.is_id):
            merged.append(e)
    return merged


def entry_key(e: TrackEntry) -> str:
    return normalize(e.display)


class Curator:
    def __init__(self, opts: Options, http: Optional[Http] = None, log: Callable[[str], None] = print):
        self.opts = opts
        self.http = http or Http()
        self.log = log
        self.sc = SoundCloud(self.http)
        browser_factory = None
        if opts.browser:
            from .browser import Browser

            browser_factory = Browser
        self.tl = Tracklists1001(self.http, browser_factory=browser_factory)
        self.bp = Beatport(self.http)

    # -- step 1+2: episodes and their tracklists -----------------------------------
    def episodes(self, req: Request) -> list[Episode]:
        if req.url:
            self.log(f"Using the SoundCloud upload at {req.url}")
            ep = find_set(self.sc, req)
            ep.tracklist_url_1001 = req.url_1001 or ""
            return [ep]
        if req.setlist:  # the user gave the tracklist, so there is no set to find
            n = req.episodes[0] if req.episodes else None
            return [Episode(label=f"{req.query} #{n}" if n is not None else req.query, number=n)]
        if req.url_1001:
            return [Episode(label=req.url_1001, tracklist_url_1001=req.url_1001)]
        if req.is_series:
            self.log(f"Finding {req.query} episodes {req.episodes[0]}-{req.episodes[-1]} on SoundCloud")
            return find_series(self.sc, req, log=self.log)
        self.log(f"Finding {req.query} on SoundCloud")
        return [find_set(self.sc, req)]

    def fill_tracklist(self, ep: Episode, req: Request) -> None:
        if req.setlist:  # the user's own list: nothing to read or look up
            ep.tracks, ep.tracklist_source = list(req.setlist), "setlist"
            self.log(f"  {ep.label}: {len(ep.tracks)} track{'' if len(ep.tracks) == 1 else 's'} from your setlist")
            return
        sc_tracks = parse_tracklist(ep.description) if ep.description else []
        ratio = id_ratio(sc_tracks)
        tl_tracks: list[TrackEntry] = []
        need_1001 = not sc_tracks or ratio > ID_THRESHOLD or bool(ep.tracklist_url_1001)
        if need_1001 and self.opts.use_1001:
            reason = "no tracklist on SoundCloud" if not sc_tracks else f"{ratio:.0%} IDs on SoundCloud"
            try:
                url = ep.tracklist_url_1001 or self.tl.search(
                    ep.soundcloud_title or req.query, ep.number
                )
                if url:
                    ep.tracklist_url_1001 = url
                    tl_tracks = self.tl.tracklist(url)
                    self.log(f"  1001Tracklists ({reason}): {len(tl_tracks)} tracks from {url}")
                else:
                    ep.notes.append("no 1001Tracklists page found")
            except Blocked as e:
                ep.notes.append(f"1001Tracklists blocked the request ({e}); rerun with --browser")
                self.log(f"  ! {ep.notes[-1]}")
            except Exception as e:
                ep.notes.append(f"1001Tracklists error: {e}")
                self.log(f"  ! {ep.notes[-1]}")
        ep.tracks = merge_tracklists(sc_tracks, tl_tracks)
        ep.tracklist_source = (
            "both" if sc_tracks and tl_tracks else "soundcloud" if sc_tracks else "1001tracklists" if tl_tracks else "none"
        )
        ids = sum(t.is_id for t in ep.tracks)
        self.log(f"  {ep.label}: {len(ep.tracks)} tracks ({ids} IDs) from {ep.tracklist_source}")

    # -- step 3+4: per-track work --------------------------------------------------
    def curate(self, entry: TrackEntry, episode: Episode) -> CuratedTrack:
        ct = CuratedTrack(entry=entry, episode=episode.label)
        if entry.is_id:
            ct.status = "id"
            return ct
        sc_track = self._find_on_soundcloud(entry, ct, exclude=episode.soundcloud_url)
        bp_hint = None
        if sc_track:
            ct.soundcloud_url = sc_track.get("permalink_url", "")
            ct.soundcloud_title = f"{(sc_track.get('user') or {}).get('username', '')}: {sc_track.get('title', '')}"
            fd = find_free_download(sc_track)
            bp_hint = beatport_link(sc_track)
            if fd:
                ct.free_download = fd
                ct.status = "gate" if fd.kind == "gate" else "free"
                if self.opts.download and fd.kind in ("soundcloud", "direct"):
                    self._download(ct, sc_track)
                if ct.status in ("downloaded", "gate"):
                    return ct
        if self.opts.beatport:
            try:
                ct.beatport, why = self.bp.match(entry, bp_hint)
                if why:
                    ct.notes.append(why)
            except Exception as e:
                ct.notes.append(f"Beatport search failed: {e}")
            if ct.beatport:
                ct.status = "beatport"
                return ct
        ct.status = "free" if ct.free_download else "not_found"
        return ct

    def _find_on_soundcloud(self, e: TrackEntry, ct: CuratedTrack, exclude: str = "") -> Optional[dict]:
        """The SoundCloud upload of exactly this track, preferring one that is a free download."""
        spec = parse_spec(e.artist, e.title)
        for q in search_queries(spec, "soundcloud"):
            try:
                hits = self.sc.search_tracks(q, limit=MAX_RESULTS)
            except Exception as ex:
                self.log(f"  ! SoundCloud search failed for {e.display}: {ex}")
                return None
            cands = [
                c
                for i, t in enumerate(hits[:MAX_RESULTS])
                if t.get("permalink_url") != exclude and not is_mix_upload(t)
                for c in soundcloud_candidates(t, i)
            ]
            accepted, _ = rank(spec, cands)
            if accepted:
                free = [cv for cv in accepted if find_free_download(cv[0].raw)]
                c, v = (free or accepted)[0]
                ct.soundcloud_score = round(v.score, 1)
                return c.raw
        return None

    def _download(self, ct: CuratedTrack, sc_track: dict) -> None:
        fd = ct.free_download
        assert fd
        url = self.sc.download_url(sc_track) if fd.kind == "soundcloud" else fd.url
        if not url:
            fd.error = "download link unavailable"
            return
        try:
            path = self.http.download(url, self.opts.out_dir / "downloads", ct.entry.display)
            fd.local_path = str(path)
            ct.status = "downloaded"
        except Exception as e:
            fd.error = str(e)[:200]
            self.log(f"  ! download failed for {ct.entry.display}: {fd.error}")

    # -- everything ----------------------------------------------------------------
    def run(self, requests: list[Request], raw: Optional[list[str]] = None, cart_name: str = "") -> RunResult:
        result = RunResult(requests=list(raw or []), cart_name=cart_name)
        for req in requests:
            for ep in self.episodes(req):
                self.fill_tracklist(ep, req)
                result.episodes.append(ep)
        seen: dict[str, CuratedTrack] = {}
        todo = [(ep, e) for ep in result.episodes for e in ep.tracks if self.opts.include_layers or not e.is_layer]
        self.log(f"Checking {len(todo)} tracklist entries for free downloads and Beatport matches")
        progress = Progress(len(todo), log=self.log)
        for ep, e in todo:
            k = entry_key(e)
            if not e.is_id and k in seen:
                seen[k].notes.append(f"also in {ep.label}")
                progress.step("duplicate", e.display)
                continue
            ct = self.curate(e, ep)
            result.tracks.append(ct)
            if not e.is_id:
                seen[k] = ct
            progress.step(ct.status, e.display)
        self.tl.close()
        return result
