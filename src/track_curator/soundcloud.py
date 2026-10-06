"""SoundCloud via its public web API (api-v2), the same one soundcloud.com uses.

The web app's client_id is read from soundcloud.com's own JavaScript bundles, so no
account or API key is needed. "Behind this track" on a track page is the track's
`description` field.
"""
from __future__ import annotations

import re
from typing import Any, Iterator, Optional

from .http import Http

API = "https://api-v2.soundcloud.com"
WEB = "https://soundcloud.com"


class SoundCloud:
    def __init__(self, http: Http, client_id: Optional[str] = None):
        self.http = http
        self._client_id = client_id

    # -- auth -------------------------------------------------------------------
    @property
    def client_id(self) -> str:
        if not self._client_id:
            self._client_id = self._discover_client_id()
        return self._client_id

    def _discover_client_id(self) -> str:
        html = self.http.text(WEB, cache=False)
        scripts = re.findall(r'<script[^>]+src="(https://a-v2\.sndcdn\.com/assets/[^"]+\.js)"', html)
        for src in reversed(scripts):  # the id lives in one of the last bundles
            js = self.http.text(src)
            m = re.search(r'client_id\s*[:=]\s*"([A-Za-z0-9]{32})"', js) or re.search(
                r"client_id=([A-Za-z0-9]{32})", js
            )
            if m:
                return m.group(1)
        raise RuntimeError("could not find a SoundCloud client_id in soundcloud.com's scripts")

    def _get(self, path_or_url: str, params: Optional[dict] = None, cache: bool = True) -> Any:
        url = path_or_url if path_or_url.startswith("http") else API + path_or_url
        p = dict(params or {})
        p["client_id"] = self.client_id
        try:
            return self.http.json(url, params=p, cache=cache)
        except Exception as e:  # stale client_id -> refresh once
            if "401" in str(e) or "403" in str(e):
                self._client_id = self._discover_client_id()
                p["client_id"] = self._client_id
                return self.http.json(url, params=p, cache=cache)
            raise

    # -- lookups ----------------------------------------------------------------
    def search_tracks(self, q: str, limit: int = 20) -> list[dict]:
        data = self._get("/search/tracks", {"q": q, "limit": limit, "offset": 0, "linked_partitioning": 1})
        return [t for t in data.get("collection", []) if t.get("kind") == "track"]

    def resolve(self, url: str) -> dict:
        return self._get("/resolve", {"url": url})

    def user_tracks(self, user_id: int, max_items: int = 2000) -> Iterator[dict]:
        url: Optional[str] = f"{API}/users/{user_id}/tracks"
        params: Optional[dict] = {"limit": 200, "linked_partitioning": 1}
        seen = 0
        while url and seen < max_items:
            data = self._get(url, params)
            for t in data.get("collection", []):
                seen += 1
                yield t
            url, params = data.get("next_href"), None

    def download_url(self, track: dict) -> Optional[str]:
        """The original file the uploader enabled for download, if any."""
        if not track.get("downloadable"):
            return None
        try:
            data = self._get(f"/tracks/{track['id']}/download", cache=False)
        except Exception:
            return None
        return data.get("redirectUri")


def track_artist(t: dict) -> str:
    """Best guess of the performing artist for a SoundCloud track."""
    meta = t.get("publisher_metadata") or {}
    if meta.get("artist"):
        return meta["artist"]
    title = t.get("title", "")
    if " - " in title:
        return title.split(" - ", 1)[0]
    return (t.get("user") or {}).get("username", "")


def track_title(t: dict) -> str:
    title = t.get("title", "")
    return title.split(" - ", 1)[1] if " - " in title else title


def describe(t: dict) -> str:
    return f"{(t.get('user') or {}).get('username', '')} {t.get('title', '')}".strip()
