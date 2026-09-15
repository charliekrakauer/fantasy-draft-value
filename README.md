# Value Board

A small dashboard that answers one question per fantasy football position:
**who is beating their draft slot, and who is losing to it?**

It compares two things:

1. **ADP Rk** — where a player was drafted, position by position, in a locked
   "Beginning Of Season" (BOS) snapshot taken right before the 2026 season
   kicked off. This never changes; it's the fixed yardstick everything else
   is measured against.
2. **Now Rk** — where that player currently ranks within their position by
   actual fantasy points scored so far *this* season. The dashboard has a
   toggle for how "so far" is measured: **season total** (the default) or
   **points per game** — these can disagree quite a bit early in a season,
   since a player who's missed a game can still lead the per-game ranking
   while sitting well down the season-total one.

Both ADP Rk and Now Rk also depend on scoring format, so there's a second
toggle for **PPR / Half-PPR / Standard** (defaulting to PPR) that swaps
both sides of the comparison together.

`Delta = ADP Rk − Now Rk`. A positive delta means a player is outperforming
where they were drafted (e.g. a WR drafted as the 20th receiver off the
board who's currently the 8th-highest-scoring WR: delta = 20 − 8 = **+12**).
A negative delta means the opposite.

The output is `dist/value_board.html` — one self-contained web page with a
position toggle and two ranked tables (overperformers / underperformers).
Open it in any browser; nothing needs to be installed to view it.

---

## How it works, end to end

```
 ┌──────────────────────────┐        ┌───────────────────────────────┐
 │ data/bos_adp_top250.csv  │        │ nflverse (public NFL stats)    │
 │ Locked preseason ADP.    │        │ downloaded fresh each run by   │
 │ NEVER regenerated.       │        │ fetch_current_performance.py   │
 └────────────┬─────────────┘        └───────────────┬─────────────────┘
              │                                       │
              │                      data/current_performance_<season>.csv
              │                                       │
              └───────────────┬───────────────────────┘
                               ▼
                 scripts/build_dashboard_data.py
                 (joins the two on player + team,
                  computes Now Rk and Delta)
                               │
                               ▼
                   data/dashboard_data.json
                               │
                               ▼
                    scripts/build_site.py
             (stamps the JSON into the HTML template)
                               │
                               ▼
                    dist/value_board.html   ← open this file
```

Three scripts, run in order, each one producing the file the next one reads.
Nothing runs automatically — you re-run these by hand whenever you want an
updated dashboard (see [Refreshing the dashboard](#refreshing-the-dashboard)
below).

---

## Data sources

### Beginning Of Season ADP — `data/bos_adp_top250.csv`

The top 250 NFL players/defenses drafted in ESPN-style fantasy leagues,
with Average Draft Position (ADP) and positional ADP rank in Standard, PPR,
and Half-PPR scoring. This file is **locked** — it represents the market's
consensus valuation of every player *before* a single snap of the season
was played, so it must never be regenerated or edited once the season
starts. (If you ever do need to rebuild it — e.g. for a future season —
the pull came from
[FantasyFootballCalculator.com's public ADP API](https://fantasyfootballcalculator.com/adp),
which aggregates ADP from real mock drafts. ESPN's own Live Draft Trends
tool and FantasyPros were investigated first; ESPN only exposes a single,
PPR-only ADP regardless of scoring format, and FantasyPros' full ADP table
sits behind a free-account signup wall, which is why FFC was used instead.)

### Current-season performance — `data/current_performance_<season>.csv`

Built by `scripts/fetch_current_performance.py` from
[nflverse](https://github.com/nflverse/nflverse-data), a community-maintained,
freely downloadable mirror of official NFL stats (no login, no API key —
just CSV files published as GitHub release assets). Specifically:

| File | What it gives us |
|---|---|
| `player_stats.csv` | Weekly QB/RB/WR/TE stats, including nflverse's own computed `fantasy_points` (standard) and `fantasy_points_ppr` columns |
| `player_stats_kicking.csv` | Weekly kicker stats (field goals by distance, extra points) |
| `player_stats_def.csv` | Weekly *individual* defensive stats (sacks, INTs, fumble recoveries, defensive TDs, safeties) — aggregated up to the team level for D/ST scoring |
| `games.csv` | The season schedule with final scores, used for each defense's "points allowed" |

The script downloads these into `data/raw/` (cached there so repeat runs
don't re-download ~70MB every time — delete that folder, or pass
`--refresh`, to force a fresh pull) and computes season-to-date fantasy
points and position rank for every player and team defense.

---

## Scoring methodology

**QB / RB / WR / TE**: nflverse already computes standard and PPR fantasy
points per game from the raw play-by-play data, so we just sum those across
the season. Half-PPR is derived rather than separately computed: since
`fantasy_points_ppr = fantasy_points + 1 point per reception` (verified
directly against the data), half-PPR is simply the arithmetic mean of the
two — `(standard + ppr) / 2`.

**Kicker**: there's no ready-made kicker fantasy score in the source data,
so `fetch_current_performance.py` applies a standard distance-tiered table
itself:

| Field goal distance | Points | | Extra point | Points |
|---|---|---|---|---|
| 0–39 yards | 3 | | Made | 1 |
| 40–49 yards | 4 | | | |
| 50+ yards | 5 | | | |

Missed kicks aren't penalized (many "standard" leagues don't either). This
score is the same across all three scoring formats, since kickers don't get
a reception bonus.

**Defense/Special Teams (D/ST)**: also not provided directly, since every
fantasy site invents its own D/ST table. We use a standard ESPN-default-style
one, computed per team per week and summed for the season:

| Category | Points |
|---|---|
| Sack | 1 |
| Interception | 2 |
| Fumble recovery (opponent's) | 2 |
| Defensive or return TD | 6 |
| Safety | 2 |
| 0 points allowed | 10 |
| 1–6 points allowed | 7 |
| 7–13 points allowed | 4 |
| 14–20 points allowed | 1 |
| 21–27 points allowed | 0 |
| 28–34 points allowed | −1 |
| 35+ points allowed | −4 |

**All of the scoring constants above live at the top of
`scripts/fetch_current_performance.py`** (`DST_POINTS`,
`POINTS_ALLOWED_TIERS`, and `kicker_points_for_row`). If your actual league
scores kickers or defenses differently, that's the only place you need to
change — no other script needs to know about it.

The dashboard has a **PPR / Half-PPR / Standard** toggle, defaulting to PPR
(nflverse's most complete scoring column) — that default is set at the top
of `scripts/build_dashboard_data.py` as `DEFAULT_SCORING_FORMAT`. All three
formats' ADP and current-rank data are computed and shipped in
`dashboard_data.json` together, so switching formats on the page is
instant and doesn't need a rebuild. A player missing ADP in a particular
format (a blank cell in the source file) simply drops out of that format's
lists rather than showing a broken row.

---

## File layout

```
value-board/
├── README.md                          ← you are here
├── DEPLOY.md                          ← steps to put this on a public domain (GitHub Pages)
├── .gitignore
├── data/
│   ├── bos_adp_top250.csv             ← locked preseason ADP (never regenerate)
│   ├── raw/                           ← cached nflverse downloads (gitignored)
│   ├── current_performance_<season>.csv   ← generated by fetch_current_performance.py
│   └── dashboard_data.json            ← generated by build_dashboard_data.py
├── scripts/
│   ├── lib_names.py                   ← shared player/team-name matching helpers
│   ├── fetch_current_performance.py   ← pulls nflverse stats, computes fantasy points + rank
│   ├── build_dashboard_data.py        ← joins ADP + performance into dashboard_data.json
│   └── build_site.py                  ← injects dashboard_data.json into both outputs below
├── site/
│   └── value_board_template.html      ← the dashboard's HTML/CSS/JS, with a data placeholder
├── dist/
│   └── value_board.html               ← Artifact-ready fragment (e.g. for the Claude Artifact tool)
└── docs/
    ├── index.html                     ← standalone page GitHub Pages actually serves publicly
    ├── CNAME                          ← custom domain for GitHub Pages (fantasydraftvalue.com)
    ├── robots.txt                     ← crawler instructions (update the domain placeholder)
    └── sitemap.xml                    ← for search engines (update the domain placeholder)
```

`dist/value_board.html` and `docs/index.html` have the *same* dashboard content —
they're just packaged differently for their two different destinations (see
`build_site.py`'s docstring for why). You'll only ever open one or the
other depending on what you're doing with it that moment.

## Requirements

- Python 3 with `pandas` (`pip install pandas` if you don't already have it)
- Internet access (only `fetch_current_performance.py` needs it, to reach
  `github.com`/`raw.githubusercontent.com`)

No other dependencies — no Node, no build tooling, no database.

## Refreshing the dashboard

Run the three scripts in order from the `value-board/` directory,
whenever you want to pull in the latest games:

```bash
python3 scripts/fetch_current_performance.py --season 2026
python3 scripts/build_dashboard_data.py --season 2026
python3 scripts/build_site.py
```

That's the entire local refresh cycle — there's no scheduling or server
involved, by design (this was built as an on-request refresh, not an
always-on live service). Opening `dist/value_board.html` locally shows you
the update immediately, without needing anything deployed — handy for
checking a refresh before publishing it anywhere.

If the site has been deployed to GitHub Pages (see `DEPLOY.md`), publishing
a refresh is those same three commands plus a `git add`/`commit`/`push` of
`docs/index.html` — the exact commands are in `DEPLOY.md`'s
"Refreshing the live data going forward" section, so they're not repeated
here. If you outgrow manual refreshes later, the natural next step is
wrapping that whole sequence (three scripts + `git push`) in a scheduled
task (cron, GitHub Actions, etc.).

`build_dashboard_data.py` prints a short report of how many ADP players it
was able to match to current-season stats, and lists anyone it couldn't
(rookies who haven't debuted yet, players who moved teams in a way the
matching didn't catch, etc.) — worth a skim after each refresh so a bad
match doesn't go unnoticed.

## Current status (as shipped)

This was built and delivered on 2026-09-10 — the day before the 2026
season's first games. Running the pipeline right now correctly finds **zero
completed games**, so `dist/value_board.html` currently shows "Now Rk" as
not-yet-available for every player, with a banner explaining that. This is
expected, not a bug: re-run the three commands above after Week 1 games are
played and the Overperformers/Underperformers tables will populate for
real.

## Known limitations

- **Name/team matching is fuzzy, not exact.** `scripts/lib_names.py`
  normalizes names (dropping punctuation and suffixes like "Jr."/"III")
  and a small number of known team-code differences (e.g. FFC's "LAR" vs.
  nflverse's "LA") to match players across the two data sources. It isn't
  guaranteed to catch every mid-season trade or every naming quirk — check
  the unmatched-players list `build_dashboard_data.py` prints after each run.
- **Kicker and D/ST scoring are approximations of "standard" scoring**, not
  pulled from any specific league's actual settings — see
  [Scoring methodology](#scoring-methodology) for the exact tables and where
  to change them.
- **"Now Rk" is computed from actual points scored, not from an outside
  site's expert rankings.** That was a deliberate choice (see
  [Future ideas](#future-ideas)) — it means a player with only one great
  game can rank higher than their true current value would suggest, since
  the sample size early in a season is small.

## Future ideas

- An alternate "Now Rk" sourced from a site's live/updated expert positional
  rankings (e.g. ESPN's), as originally discussed, shown alongside or
  instead of the stats-based rank — useful because it factors in things raw
  stats don't yet (recent role changes, injury outlook, upcoming schedule).
- Automating the refresh on a schedule instead of running it by hand.
- Publishing `dist/value_board.html` somewhere it gets a shareable link
  that updates itself on refresh, rather than a local file you re-open.
