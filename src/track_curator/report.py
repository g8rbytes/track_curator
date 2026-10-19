"""Write run results: result.json, tracks.csv and an interactive report.html."""
from __future__ import annotations

import csv
import html
import json
from dataclasses import asdict
from pathlib import Path

from . import __version__
from .models import CuratedTrack
from .pipeline import RunResult

STATUS_LABEL = {
    "downloaded": "Downloaded",
    "free": "Free (not downloaded)",
    "gate": "Free behind a gate",
    "beatport": "On Beatport",
    "not_found": "Not found",
    "id": "ID",
}


def write_all(result: RunResult, out_dir: Path) -> dict[str, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = {
        "json": out_dir / "result.json",
        "csv": out_dir / "tracks.csv",
        "html": out_dir / "report.html",
    }
    paths["json"].write_text(
        json.dumps(
            {
                "version": __version__,
                "requests": result.requests,
                "cart_name": result.cart_name,
                "episodes": [asdict(e) for e in result.episodes],
                "tracks": [t.to_dict() for t in result.tracks],
            },
            indent=2,
        )
    )
    with open(paths["csv"], "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["episode", "position", "timestamp", "artist", "title", "status", "soundcloud", "free_download",
                    "local_file", "beatport", "beatport_track", "match_reason", "price", "notes"])
        for t in result.tracks:
            bp = t.beatport
            w.writerow([
                t.episode, t.entry.position, t.entry.timestamp, t.entry.artist, t.entry.title, t.status, t.soundcloud_url,
                t.free_download.url if t.free_download else "", (t.free_download.local_path or "") if t.free_download else "",
                bp.url if bp else "", bp.display if bp else "", bp.reason if bp else "", bp.price if bp else "",
                "; ".join(t.notes),
            ])
    paths["html"].write_text(render_html(result, str(paths["json"].resolve())))
    return paths


def _a(url: str, text: str) -> str:
    return f'<a href="{html.escape(url)}" target="_blank" rel="noopener">{html.escape(text)}</a>' if url else ""


def _row(t: CuratedTrack) -> str:
    e = t.entry
    links = []
    if t.soundcloud_url:
        links.append(_a(t.soundcloud_url, "SoundCloud"))
    if t.free_download and t.free_download.kind == "gate":
        links.append(_a(t.free_download.url, f"Get free ({t.free_download.host})"))
    if t.free_download and t.free_download.local_path:
        links.append(html.escape(Path(t.free_download.local_path).name))
    bp = ""
    if t.beatport:
        b = t.beatport
        bp = (f'<label><input type="checkbox" class="bp" data-url="{html.escape(b.url)}" data-id="{b.track_id}" '
              f'data-price="{html.escape(b.price)}" checked> '
              f'{_a(b.url, (b.price or "Beatport"))}</label>')
        if b.in_cart:
            bp += " <span class='ok'>in cart</span>"
        bp += f"<div class='m'>{html.escape(b.display)}</div>"
        if b.reason:
            bp += f"<div class='m why'>{html.escape(b.reason)}</div>"
    found = f"<div class='m'>{html.escape(t.soundcloud_title)}</div>" if t.soundcloud_title else ""
    layer = "w/ " if e.is_layer else ""
    return (
        f"<tr class='s-{t.status}'><td>{html.escape(t.episode)}</td><td>{layer}{e.position}</td>"
        f"<td class='ts'>{html.escape(e.timestamp)}</td>"
        f"<td>{html.escape(e.artist)}</td><td>{html.escape(e.title)}</td>"
        f"<td>{STATUS_LABEL.get(t.status, t.status)}</td><td>{' · '.join(links)}{found}</td><td>{bp}</td>"
        f"<td class='m'>{html.escape('; '.join(t.notes))}</td></tr>"
    )


def _free_list(tracks: list[CuratedTrack]) -> tuple[int, str]:
    """Every free download in one list, in tracklist order, each with the link from its table row."""
    items = []
    for t in tracks:
        fd = t.free_download
        if not fd:
            continue
        name = html.escape(t.entry.display)
        if fd.local_path:
            p = Path(fd.local_path)
            items.append(f"<li>{_a(p.resolve().as_uri(), t.entry.display)} <span class='m'>downloaded: "
                         f"{html.escape(p.name)}</span></li>")
        elif fd.url:
            how = "free behind a gate" if fd.kind == "gate" else "free download"
            items.append(f"<li>{_a(fd.url, t.entry.display)} <span class='m'>{html.escape(fd.host)} · {how}"
                         f"{' · ' + html.escape(fd.error) if fd.error else ''}</span></li>")
        else:
            items.append(f"<li>{name} <span class='m'>no link found</span></li>")
    if not items:
        return 0, ""
    return len(items), (
        f"<h2 id='free'>Free downloads ({len(items)})</h2>"
        "<p class='hint'>Each link opens in a new tab and turns grey once you've opened it.</p>"
        f"<ol class='free'>{''.join(items)}</ol>"
    )


def render_html(result: RunResult, result_path: str = "") -> str:
    counts: dict[str, int] = {}
    for t in result.tracks:
        counts[t.status] = counts.get(t.status, 0) + 1
    summary = " · ".join(f"{STATUS_LABEL.get(k, k)}: {v}" for k, v in counts.items())
    eps = "".join(
        f"<li>{html.escape(e.label)}: {len(e.tracks)} track{'' if len(e.tracks) == 1 else 's'} from "
        f"{'your setlist' if e.tracklist_source == 'setlist' else e.tracklist_source}"
        f"{' · ' + _a(e.soundcloud_url, 'SoundCloud') if e.soundcloud_url else ''}"
        f"{' · ' + _a(e.tracklist_url_1001, '1001Tracklists') if e.tracklist_url_1001 else ''}"
        f"{' · <em>' + html.escape('; '.join(e.notes)) + '</em>' if e.notes else ''}</li>"
        for e in result.episodes
    )
    rows = "".join(_row(t) for t in result.tracks)
    cart = (f"<p>Beatport cart: <strong>{html.escape(result.cart_name)}</strong></p>" if result.cart_name else "")
    n_free, free = _free_list(result.tracks)
    if n_free:
        cart += f"<p><a href='#free'>Free downloads ({n_free})</a>: all in one list at the bottom</p>"
    return f"""<!doctype html><html><head><meta charset="utf-8"><title>Track Curator report</title>
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>
:root{{--bg:#fff;--fg:#1b1b1f;--muted:#666;--line:#e3e3e8;--ok:#1a7f37;--gate:#9a6700;--bp:#0b5cad}}
@media (prefers-color-scheme:dark){{:root{{--bg:#141418;--fg:#ececf1;--muted:#9a9aa5;--line:#2c2c34;--ok:#4ac26b;--gate:#d4a72c;--bp:#58a6ff}}}}
body{{font:14px/1.45 system-ui,sans-serif;background:var(--bg);color:var(--fg);margin:0;padding:16px}}
table{{border-collapse:collapse;width:100%}}td,th{{border-bottom:1px solid var(--line);padding:6px 8px;text-align:left;vertical-align:top}}
th{{position:sticky;top:0;background:var(--bg)}}a{{color:var(--bp)}}.ok{{color:var(--ok)}}
.s-downloaded td:nth-child(6){{color:var(--ok)}}.s-gate td:nth-child(6){{color:var(--gate)}}.s-id{{color:var(--muted)}}
.bar{{display:flex;gap:12px;align-items:center;flex-wrap:wrap;margin:12px 0}}button{{font:inherit;padding:6px 12px}}
.wrap{{overflow-x:auto}}.hint{{color:var(--muted);margin:0 0 12px}}
.m{{color:var(--muted);font-size:12px}}.ts{{white-space:nowrap;font-variant-numeric:tabular-nums}}
.free{{line-height:1.9}}.free a:visited{{color:var(--muted)}}
</style></head><body>
<h1>Track Curator report</h1><p>{html.escape(summary)}</p>{cart}<ul>{eps}</ul>
<div class="bar"><button onclick="copyCart()">Copy cart command</button>
<button id="openb" onclick="openNext()">Open next 10 checked</button>
<button onclick="copySel()">Copy checked links</button>
<button onclick="setAll(true)">Check all</button><button onclick="setAll(false)">Uncheck all</button>
<span id="tot"></span><span id="msg" class="ok"></span></div>
<p class="hint">Uncheck what you don't want, then "Copy cart command" and paste it in Terminal to add the rest to your Beatport cart.</p>
<div class="wrap"><table><thead><tr><th>Set</th><th>#</th><th>Time</th><th>Artist</th><th>Title</th><th>Status</th><th>Links</th><th>Beatport match</th><th>Notes</th></tr></thead>
<tbody>{rows}</tbody></table></div>
{free}
<script>
const sel=()=>[...document.querySelectorAll('input.bp:checked')];
function tot(){{let s=0,c='';for(const i of sel()){{const m=(i.dataset.price||'').match(/([^\\d]*)([\\d.,]+)/);if(m){{c=c||m[1];s+=parseFloat(m[2].replace(',','.'))}}}}
document.getElementById('tot').textContent=sel().length+' checked'+(s?' · about '+c+s.toFixed(2):'')}}
const RESULT={json.dumps(result_path)};let next=0;
function flash(t){{const m=document.getElementById('msg');m.textContent=t;setTimeout(()=>m.textContent='',2500)}}
function openNext(){{const s=sel();if(next>=s.length)next=0;const batch=s.slice(next,next+10);
for(const i of batch)window.open(i.dataset.url,'_blank');next+=batch.length;
document.getElementById('openb').textContent=next>=s.length?'Open next 10 checked (start over)':'Open next 10 checked ('+next+'/'+s.length+' opened)'}}
function copySel(){{navigator.clipboard.writeText(sel().map(i=>i.dataset.url).join('\\n'));flash('Copied')}}
function copyCart(){{const ids=sel().map(i=>i.dataset.id).join(',');if(!ids)return flash('Nothing checked');
navigator.clipboard.writeText('track-curator cart "'+RESULT+'" --only '+ids);flash('Copied: paste in Terminal')}}
function setAll(v){{for(const i of document.querySelectorAll('input.bp'))i.checked=v;next=0;tot()}}
document.addEventListener('change',()=>{{next=0;tot()}});tot();
</script></body></html>"""


def load_result(path: Path) -> RunResult:
    """Rebuild a RunResult from a run's result.json (to regenerate its report)."""
    from .models import BeatportMatch, Episode, FreeDownload, TrackEntry, from_dict

    data = json.loads(Path(path).read_text())
    eps = []
    for e in data["episodes"]:
        e = dict(e)
        e["tracks"] = [from_dict(TrackEntry, t) for t in e.get("tracks", [])]
        eps.append(from_dict(Episode, e))
    tracks = []
    for t in data["tracks"]:
        t = dict(t)
        t["entry"] = from_dict(TrackEntry, t["entry"])
        t["free_download"] = from_dict(FreeDownload, t["free_download"]) if t.get("free_download") else None
        t["beatport"] = from_dict(BeatportMatch, t["beatport"]) if t.get("beatport") else None
        tracks.append(from_dict(CuratedTrack, t))
    return RunResult(episodes=eps, tracks=tracks, requests=data.get("requests") or [], cart_name=data.get("cart_name") or "")
