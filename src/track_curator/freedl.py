"""Decide whether a SoundCloud track is a free download, and how to get it.

Three outcomes:
  soundcloud  the uploader enabled SoundCloud's own download button -> fetched directly
  direct      the free-download link points straight at a file host (Dropbox, Drive, a .wav)
              -> fetched directly
  gate        the link goes to a download gate (Hypeddit, ToneDen, ...) -> listed for you to
              click through in your browser; Track Curator doesn't operate gates for you
"""
from __future__ import annotations

import re
from typing import Optional
from urllib.parse import parse_qs, urlparse

from .models import FreeDownload

FREE_WORDS = re.compile(r"\b(free\s*(?:download|dl)?|free\s*d/?l|download|dl)\b", re.I)
FREE_TITLE = re.compile(r"\bfree\s*(?:download|dl|d/l)\b|\[\s*free\s*\]|\(\s*free\s*\)", re.I)

GATE_HOSTS = (
    "hypeddit.com", "hyperfollow", "toneden.io", "hive.co", "show.co", "gate.fm",
    "fanlink.to", "feature.fm", "ffm.to", "smarturl.it", "lnk.to", "linktr.ee",
    "edmdistrict.com", "pumpyoursound.com", "dl.toneden.io", "bit.ly", "artist.link",
    "unlock.fm", "freedownload", "fangate",
)
DIRECT_HOSTS = (
    "dropbox.com", "dropboxusercontent.com", "drive.google.com", "docs.google.com",
    "mediafire.com", "we.tl", "wetransfer.com", "mega.nz", "box.com", "files.fm",
)
STORE_HOSTS = ("beatport.com", "spotify.com", "apple.com", "itunes.apple.com", "bandcamp.com",
               "traxsource.com", "junodownload.com", "amazon.", "deezer.com", "tidal.com", "youtube.com")
AUDIO_EXT = (".mp3", ".wav", ".aif", ".aiff", ".flac", ".zip", ".m4a")
_URL = re.compile(r"https?://[^\s<>\"')\]]+")


def host_of(url: str) -> str:
    return urlparse(url).netloc.lower().removeprefix("www.")


def direct_file_url(url: str) -> Optional[str]:
    """Rewrite share links into direct-download links where the host allows it."""
    u = urlparse(url)
    h = host_of(url)
    if "dropbox.com" in h:
        return re.sub(r"([?&])dl=0", r"\1dl=1", url) if "dl=" in url else url + ("&" if u.query else "?") + "dl=1"
    if "drive.google.com" in h or "docs.google.com" in h:
        m = re.search(r"/d/([\w-]+)", u.path)
        fid = m.group(1) if m else parse_qs(u.query).get("id", [None])[0]
        return f"https://drive.google.com/uc?export=download&id={fid}" if fid else None
    if u.path.lower().endswith(AUDIO_EXT):
        return url
    return None


def classify_link(url: str, label: str = "") -> Optional[FreeDownload]:
    h = host_of(url)
    if any(s in h for s in STORE_HOSTS):
        # Bandcamp "name your price" is free but needs you to enter 0; treat as a gate.
        if "bandcamp.com" in h and FREE_WORDS.search(label or ""):
            return FreeDownload(kind="gate", url=url, host=h, label=label)
        return None
    direct = direct_file_url(url)
    if direct:
        return FreeDownload(kind="direct", url=direct, host=h, label=label)
    if any(g in h for g in GATE_HOSTS) or FREE_WORDS.search(label or ""):
        return FreeDownload(kind="gate", url=url, host=h, label=label)
    return None


def find_free_download(track: dict) -> Optional[FreeDownload]:
    sc_url = track.get("permalink_url", "")
    if track.get("downloadable") and track.get("has_downloads_left", True):
        return FreeDownload(kind="soundcloud", url=sc_url, host="soundcloud.com", label="SoundCloud download",
                            soundcloud_url=sc_url)
    title = track.get("title", "")
    p_url = track.get("purchase_url") or ""
    p_title = track.get("purchase_title") or ""
    says_free = bool(FREE_TITLE.search(title)) or bool(re.search(r"\bfree\b", p_title, re.I))
    if p_url:
        fd = classify_link(p_url, p_title if says_free or FREE_WORDS.search(p_title) else "")
        if fd and (says_free or fd.kind == "direct" or any(g in fd.host for g in GATE_HOSTS[:6])):
            fd.soundcloud_url = sc_url
            return fd
    desc = track.get("description") or ""
    if says_free or re.search(r"free\s*(download|dl)", desc, re.I):
        for line in desc.splitlines():
            for url in _URL.findall(line):
                fd = classify_link(url, line if FREE_WORDS.search(line) else "")
                if fd:
                    fd.soundcloud_url = sc_url
                    return fd
    if says_free:
        return FreeDownload(kind="gate", url=p_url or sc_url, host=host_of(p_url or sc_url),
                            label="marked free, no link found", soundcloud_url=sc_url)
    return None


def beatport_link(track: dict) -> Optional[str]:
    """A Beatport track/release URL the uploader linked as the buy link, if any."""
    for u in [track.get("purchase_url") or ""] + _URL.findall(track.get("description") or ""):
        if "beatport.com/track/" in u or "beatport.com/release/" in u:
            return u
    return None
