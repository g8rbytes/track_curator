# Track Curator

Turn DJ mixes into: a tracklist, every free download you can grab, and a Beatport cart for the rest.

```
track-curator run "[LUMBERJVCK's Deep Cuts, 1-20]" "[Dirtyphonics @ Rampage Open Air 2026]"
```

## Install (macOS)

One-time setup with [pipx](https://pipx.pypa.io), which gives the tool its own environment and puts
`track-curator` on your PATH in every terminal (no venv to activate):

```
brew install pipx && pipx ensurepath          # then open a new terminal
pipx install "git+https://github.com/g8rbytes/track_curator.git"
pipx inject track-curator playwright           # only for --cart / --browser
track-curator setup-browser                     # optional: bundled Chromium if Google Chrome isn't installed
```

Update to the latest version:

```
pipx reinstall track-curator
```

(`reinstall` always pulls the latest commit and keeps the injected Playwright; `pipx upgrade` can skip a
git install when the version number hasn't changed.)

Pin or roll back to a specific commit or tag:
`pipx install --force "git+https://github.com/g8rbytes/track_curator.git@<commit-or-tag>"`.

For hacking on the code instead: `python3 -m venv .venv && source .venv/bin/activate && pip install -e '.[browser,dev]'`.

## What a run does

1. **Find the upload on SoundCloud.** A series (`[Show, 1-20]`, `[Show, 3, 7, 9-11]`) is matched by
   show name, then the show's own account is walked so every episode number is found, not just
   the ones search ranks highly. A single set (`[Artist @ Event 2026]`) takes the best-matching long
   upload. A SoundCloud URL works too.
2. **Read "Behind this track".** A tracklist is taken from the description when it has a
   `Tracklist`/`Playlist`/`TL` header (fuzzy) or at least 3 numbered/timestamped `Artist - Title` lines.
   `w/` lines are kept as layered tracks; `ID - ID`, `ID`, `??`, `TBA` count as IDs.
3. **Fall back to 1001Tracklists** when there is no tracklist or more than 50% of it is IDs. When
   both exist, the list with fewer IDs is the base and the other adds anything it is missing.
4. **Free downloads.** Each identified track is looked up on SoundCloud:
   - SoundCloud's own download button enabled -> downloaded.
   - "Free download" link to Dropbox / Google Drive / a direct file -> downloaded.
   - Link to a gate (Hypeddit, ToneDen, Bandcamp name-your-price...) -> listed as **Free behind a
     gate**. `track-curator gates <run>/result.json` opens them in your browser to click through.
5. **Beatport.** Everything else is matched on Beatport (remix names must agree) and listed in the
   report with checkboxes, a running total, and "open checked" / "copy checked links" buttons.
   `--cart` (or `track-curator cart <run>/result.json --only 123,456`) opens a browser window,
   lets you log in once, and presses add-to-cart on each track.

Output goes to `~/TrackCurator/<date>-<name>/`: `report.html`, `tracks.csv`, `result.json`, `downloads/`.

## Useful flags

| flag | effect |
|---|---|
| `--browser` | use a real browser window if 1001Tracklists shows a bot check (solve it once) |
| `--tracklist-url URL` | use this 1001Tracklists page instead of searching |
| `-f sets.txt` | one request per line |
| `--no-download`, `--no-beatport`, `--no-1001`, `--skip-layers` | skip a stage |
| `--offline` | only use cached pages (everything fetched is cached for 7 days in `~/.track-curator/cache`) |
| `track-curator tracklist "[...]"` | just print the tracklist |

## Known limits

- Site layouts change. The SoundCloud part uses the same API soundcloud.com itself uses; the
  1001Tracklists and Beatport parsers read page structure and are the likeliest to need a tweak.
- Beatport has no public cart API, so the cart step clicks the button on each track page. If
  Beatport renames the button, update `CART_BUTTONS` in `src/track_curator/beatport.py`.
- Download gates are opened for you to finish, not completed automatically.

Run the offline tests with `pip install -e '.[dev]' && pytest`.
