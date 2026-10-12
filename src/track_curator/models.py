from __future__ import annotations

from dataclasses import asdict, dataclass, field, fields
from typing import Any, Optional


@dataclass
class TrackEntry:
    """One line of a mix's tracklist."""

    position: int
    artist: str
    title: str
    raw: str = ""
    is_id: bool = False
    is_layer: bool = False  # "w/" lines played on top of the previous track
    source: str = ""  # "soundcloud" | "1001tracklists"
    timestamp: str = ""  # cue time in the mix, for the report only (never searched for)

    @property
    def display(self) -> str:
        return f"{self.artist} - {self.title}" if self.artist else self.title


@dataclass
class FreeDownload:
    kind: str  # "direct" (we can fetch it) | "gate" (needs a click-through) | "soundcloud" (native SC download)
    url: str
    host: str = ""
    label: str = ""
    soundcloud_url: str = ""
    local_path: Optional[str] = None
    error: Optional[str] = None


@dataclass
class BeatportMatch:
    track_id: int
    url: str
    artists: str
    name: str
    mix: str = ""
    label: str = ""
    price: str = ""
    score: float = 0.0
    in_cart: bool = False
    cart_error: Optional[str] = None
    reason: str = ""  # why this result was accepted, e.g. "title 100 · artists 2/2 · remixer in mix name"
    query: str = ""  # the Beatport search that found it

    @property
    def display(self) -> str:
        return f"{self.artists} - {self.name}" + (f" ({self.mix})" if self.mix else "")


@dataclass
class CuratedTrack:
    entry: TrackEntry
    episode: str
    soundcloud_url: str = ""
    soundcloud_title: str = ""  # the upload we matched, as "uploader: title"
    soundcloud_score: float = 0.0
    free_download: Optional[FreeDownload] = None
    beatport: Optional[BeatportMatch] = None
    status: str = "pending"  # downloaded | gate | beatport | not_found | id
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def from_dict(cls, d: dict) -> Any:
    """Build a dataclass from a result.json dict, ignoring keys this version doesn't know."""
    names = {f.name for f in fields(cls)}
    return cls(**{k: v for k, v in d.items() if k in names})


@dataclass
class Episode:
    """A mix we resolved on SoundCloud (or only on 1001Tracklists)."""

    label: str  # what the user asked for, e.g. "LUMBERJVCK's Deep Cuts #7"
    number: Optional[int] = None
    soundcloud_url: str = ""
    soundcloud_title: str = ""
    description: str = ""
    tracklist_url_1001: str = ""
    tracklist_source: str = ""
    tracks: list[TrackEntry] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
