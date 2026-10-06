"""A polite HTTP client: browser-like headers, per-host rate limit, on-disk cache.

The cache makes reruns deterministic and cheap: the same URL returns the same body
until the cache entry expires (default 7 days; tracklists rarely change).
"""
from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urlparse

import requests

UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/129.0 Safari/537.36"
)
DEFAULT_HOME = Path.home() / ".track-curator"


class Blocked(Exception):
    """The site answered with a bot wall / captcha instead of content."""


class Http:
    def __init__(
        self,
        cache_dir: Optional[Path] = None,
        ttl: float = 7 * 86400,
        min_interval: float = 1.0,
        offline: bool = False,
    ):
        self.cache_dir = cache_dir or DEFAULT_HOME / "cache"
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.ttl = ttl
        self.min_interval = min_interval
        self.offline = offline
        self._last: dict[str, float] = {}
        self.s = requests.Session()
        self.s.headers.update(
            {
                "User-Agent": UA,
                "Accept-Language": "en-US,en;q=0.9",
                "Accept": "text/html,application/json;q=0.9,*/*;q=0.8",
            }
        )

    def _key(self, method: str, url: str, params: Any, data: Any) -> Path:
        h = hashlib.sha256(json.dumps([method, url, params, data], sort_keys=True, default=str).encode())
        return self.cache_dir / f"{h.hexdigest()[:32]}.json"

    def _wait(self, url: str) -> None:
        host = urlparse(url).netloc
        dt = time.monotonic() - self._last.get(host, 0)
        if dt < self.min_interval:
            time.sleep(self.min_interval - dt)
        self._last[host] = time.monotonic()

    def text(
        self,
        url: str,
        params: Optional[dict] = None,
        data: Optional[dict] = None,
        method: str = "GET",
        cache: bool = True,
        headers: Optional[dict] = None,
    ) -> str:
        key = self._key(method, url, params, data)
        if cache and key.exists():
            try:
                entry = json.loads(key.read_text())
            except ValueError:  # half-written by an interrupted run
                entry = {"t": 0}
                key.unlink(missing_ok=True)
            if "body" in entry and (self.offline or time.time() - entry["t"] < self.ttl):
                return entry["body"]
        if self.offline:
            raise ConnectionError(f"offline and not cached: {url}")
        last_err: Exception | None = None
        for attempt in range(3):
            self._wait(url)
            try:
                r = self.s.request(method, url, params=params, data=data, headers=headers, timeout=30)
            except requests.RequestException as e:
                last_err = e
                time.sleep(2**attempt)
                continue
            if r.status_code == 429 or r.status_code >= 500:
                last_err = requests.HTTPError(f"{r.status_code} for {r.url}")
                time.sleep(2 ** (attempt + 1))
                continue
            if r.status_code in (403, 503) and _looks_blocked(r.text):
                raise Blocked(f"{urlparse(url).netloc} served a bot check ({r.status_code})")
            r.raise_for_status()
            if cache:
                tmp = key.with_suffix(".tmp")
                tmp.write_text(json.dumps({"t": time.time(), "url": r.url, "body": r.text}))
                tmp.replace(key)  # atomic, so Ctrl+C never leaves a broken entry
            return r.text
        raise last_err or RuntimeError(url)

    def json(self, url: str, params: Optional[dict] = None, cache: bool = True, **kw) -> Any:
        return json.loads(self.text(url, params=params, cache=cache, **kw))

    def download(self, url: str, dest_dir: Path, filename_hint: str = "") -> Path:
        """Stream a file to dest_dir; returns the path. Never cached."""
        dest_dir.mkdir(parents=True, exist_ok=True)
        self._wait(url)
        with self.s.get(url, stream=True, timeout=120, allow_redirects=True) as r:
            r.raise_for_status()
            ctype = r.headers.get("content-type", "")
            if "text/html" in ctype:
                raise ValueError("got a web page, not an audio file")
            name = _filename_from(r, filename_hint)
            path = _unique(dest_dir / name)
            with open(path, "wb") as f:
                for chunk in r.iter_content(1 << 16):
                    f.write(chunk)
        return path


def _looks_blocked(body: str) -> bool:
    b = body[:5000].lower()
    return any(k in b for k in ("captcha", "cf-chl", "are you a human", "challenge-platform", "access denied"))


def _filename_from(r: requests.Response, hint: str) -> str:
    import re
    from urllib.parse import unquote

    cd = r.headers.get("content-disposition", "")
    m = re.search(r"filename\*=UTF-8''([^;]+)", cd) or re.search(r'filename="?([^";]+)"?', cd)
    if m:
        name = unquote(m.group(1))
    else:
        name = unquote(urlparse(r.url).path.rsplit("/", 1)[-1]) or "track"
    ext = Path(name).suffix
    if hint:
        safe = re.sub(r'[\\/:*?"<>|]+', "_", hint).strip()[:150]
        name = safe + (ext if ext and len(ext) <= 5 else _ext_for(r.headers.get("content-type", "")))
    return name


def _ext_for(ctype: str) -> str:
    return {
        "audio/mpeg": ".mp3",
        "audio/wav": ".wav",
        "audio/x-wav": ".wav",
        "audio/flac": ".flac",
        "audio/aiff": ".aiff",
        "audio/x-aiff": ".aiff",
        "application/zip": ".zip",
    }.get(ctype.split(";")[0].strip(), "")


def _unique(p: Path) -> Path:
    if not p.exists():
        return p
    i = 2
    while True:
        q = p.with_name(f"{p.stem} ({i}){p.suffix}")
        if not q.exists():
            return q
        i += 1
