"""One progress line per track: position, percent, time left, outcome, running tally."""
from __future__ import annotations

import time
from typing import Callable

ICON = {
    "downloaded": "↓ downloaded",
    "free": "★ free",
    "gate": "★ free (gate)",
    "beatport": "$ beatport",
    "not_found": "· not found",
    "id": "· ID",
    "duplicate": "· already seen",
}


def _fmt(seconds: float) -> str:
    s = int(seconds)
    if s >= 3600:
        return f"{s // 3600}h{s % 3600 // 60:02d}m"
    return f"{s // 60}m{s % 60:02d}s" if s >= 60 else f"{s}s"


class Progress:
    def __init__(self, total: int, log: Callable[[str], None] = print, summary_every: int = 25):
        self.total = total
        self.log = log
        self.summary_every = summary_every
        self.done = 0
        self.counts: dict[str, int] = {}
        self.start = time.monotonic()
        self._width = len(str(total))

    def step(self, status: str, label: str) -> None:
        self.done += 1
        self.counts[status] = self.counts.get(status, 0) + 1
        elapsed = time.monotonic() - self.start
        left = elapsed / self.done * (self.total - self.done)
        pct = self.done * 100 // max(self.total, 1)
        eta = "done" if self.done == self.total else f"~{_fmt(left)} left"
        self.log(f"[{self.done:>{self._width}}/{self.total} {pct:>3}% {eta}] {ICON.get(status, status):<16} {label}")
        if self.done % self.summary_every == 0 or self.done == self.total:
            self.log("    so far: " + self.tally())

    def tally(self) -> str:
        order = ["downloaded", "gate", "free", "beatport", "not_found", "id", "duplicate"]
        parts = [f"{ICON[k].split(' ', 1)[1]} {self.counts[k]}" for k in order if self.counts.get(k)]
        return " · ".join(parts) + f" · {_fmt(time.monotonic() - self.start)} elapsed"
