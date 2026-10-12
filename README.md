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
   `w/` lines are kept as layered tracks; `ID - ID`, `ID`, `??`, `TBA` count as IDs. Cue times
   (`00:03:04`, `[1:02:03]`, `(03:04)`, at the start or end of a line) are moved into the report's
   **Time** column and are never part of a title or a search.
3. **Fall back to 1001Tracklists** when there is no tracklist or more than 50% of it is IDs. When
   both exist, the list with fewer IDs is the base and the other adds anything it is missing.
4. **Free downloads.** Each identified track is looked up on SoundCloud (same matching rules as
   Beatport below; long uploads such as the mix itself are skipped):
   - SoundCloud's own download button enabled -> downloaded.
   - "Free download" link to Dropbox / Google Drive / a direct file -> downloaded.
   - Link to a gate (Hypeddit, ToneDen, Bandcamp name-your-price...) -> listed as **Free behind a
     gate**. `track-curator gates <run>/result.json` opens them in your browser to click through.
5. **Beatport.** Everything else is searched on Beatport by artist + title (+ remixer), and only
   the first 10 results are checked. A result is accepted only when it is the same recording:
   - the titles (without versions, features and tags) are at least 85% alike and their numbers agree;
   - at least one artist on the tracklist line is credited on it;
   - a remix/flip/bootleg/edit has its remixer on it, named in the mix name (preferred) or
     credited as an artist; a line with no version rejects other people's remixes and
     VIP/instrumental/live versions; VIP and instrumental must agree both ways.

   A Beatport link on the track's SoundCloud upload goes through the same checks. When several
   pass, Extended Mix beats Original Mix. The report shows the Beatport track that was picked
   and why, and for misses the closest result and why it was rejected.
6. **Cart.** `--cart` opens a browser window, lets you log in once, and finds your Beatport cart
   named after the request, creating it in Cart Preferences the first time.
   `"[LUMBERJVCK's Deep Cuts, 18-18]"` gives the cart `lumberjvcks_deep_cuts_18_18`. On each
   track's page it then clicks the down arrow next to the buy button and ticks that cart, the
   way you would. The buy button itself is never pressed, your default cart stays as it is,
   and tracks already in the cart are left alone.
   `track-curator cart <run>/result.json` redoes just this step for a finished run (`--only
   123,456` for some tracks) and updates that run's report.

Output goes to `~/TrackCurator/<date>-<name>/`: `report.html`, `tracks.csv`, `result.json`, `downloads/`.
The report ends with every free download in one list of links, for clicking through gates quickly.
`track-curator report <run>/result.json` rebuilds an older run's report with the current layout.

## Useful flags

| flag | effect |
|---|---|
| `--cart` | add the Beatport matches to a cart named after the request |
| `--cart-name NAME` | use this cart name instead (also on `track-curator cart`) |
| `--default-cart` | press each track's buy button instead, which adds to your default cart |
| `--browser` | use a real browser window if 1001Tracklists shows a bot check (solve it once) |
| `--tracklist-url URL` | use this 1001Tracklists page instead of searching |
| `-f sets.txt` | one request per line |
| `--no-download`, `--no-beatport`, `--no-1001`, `--skip-layers` | skip a stage |
| `--offline` | only use cached pages (everything fetched is cached for 7 days in `~/.track-curator/cache`) |
| `track-curator tracklist "[...]"` | just print the tracklist |

## Known limits

- Site layouts change. The SoundCloud part uses the same API soundcloud.com itself uses; the
  1001Tracklists and Beatport parsers read page structure and are the likeliest to need a tweak.
- Beatport has no public cart API, so the cart step uses the website like you do. When it can't
  find something it saves the page's control labels and a screenshot in the run folder
  (`beatport-cart-page.*` for Cart Preferences, `beatport-track-page.*` for the cart list on
  track pages; labels only, no cookies), and it stops after two tracks if the cart list doesn't
  work. The page scripts are in `src/track_curator/beatport.py`; `tests/beatport_site` is a
  stand-in of those pages for testing them.
- That cart list cuts long names short (`lumberjvcks_deep_cut...`). When several of your carts
  start the same way, the tool tells them apart by their order and checks that the right cart's
  count went up; if it can't, it takes the track back out and stops. Deleting carts you've
  finished with avoids this.
- Download gates are opened for you to finish, not completed automatically.

Run the offline tests with `pip install -e '.[dev]' && pytest`.
