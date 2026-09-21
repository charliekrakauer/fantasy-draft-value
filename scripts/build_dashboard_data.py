#!/usr/bin/env python3
"""
build_dashboard_data.py
========================
Joins the two halves of the Value Board together:

  * data/bos_adp_top250.csv           -- locked, never-changes preseason
                                          ADP (from build_bos_adp.py / the
                                          original FFC pull), in all three
                                          scoring formats.
  * data/current_performance_<season>.csv -- this season's actual fantasy
                                          points and position rank so far
                                          (from fetch_current_performance.py),
                                          also in all three scoring formats.

...and produces data/dashboard_data.json, the single file the dashboard
website (site/value_board_template.html) reads to render itself. Nothing
in this script talks to the network -- it's pure joining/shaping of the
two CSVs already on disk.

MATCHING PLAYERS ACROSS THE TWO FILES
---------------------------------------
The ADP file's player names/team codes come from FantasyFootballCalculator;
the performance file's come from nflverse. They usually agree, but not
always (see scripts/lib_names.py for the specific ways they can differ and
how we normalize around it). Every ADP player we can't find in the
performance file is reported at the end of this script -- if that list
looks longer than "a few backup kickers/defenses nobody rosters," something
is probably wrong with the matching logic and is worth a look before you
trust the dashboard.

Before the season has started AT ALL (no player anywhere has a stat line
yet), a player with no current-season stats gets null current-rank/delta
fields rather than a fabricated number -- the front end shows its
"waiting on this season's first games" empty state instead of pretending
we know something we don't. Once the season IS underway, though, a player
who *still* has no stat line this week (hasn't debuted, on bye, injured,
or a name that didn't match -- see MATCHING PLAYERS below) is instead
bucketed at their position's "+" cutoff, same as anyone ranked beyond it
-- see cap_or_bucket_rank()'s docstring. That keeps every position's
full/"See All" list genuinely complete: every player from the preseason
ADP dataset gets a Now Rk slot somewhere, rather than quietly vanishing
from the dashboard.

Separately, a player missing from one scoring format's ADP column in the
source file (a blank cell -- see the Notes tab of the original ADP
workbook) gets nulled out entirely *for that format*, so switching the
dashboard's scoring-format toggle to a format that player lacks just
drops them from view rather than showing a broken row.

UNDRAFTED PERFORMERS (players in the stats file but NOT in the ADP file)
--------------------------------------------------------------------------
"Now Rk" is each ADP-tracked player's rank within their position by
real points scored -- ranked against EVERY player at that position who
recorded a stat line that week, not just the ~250 in the ADP file. So a
player who was never drafted (an emergency starter, a waiver pickup) can
easily outrank several ADP-tracked players, and if they're just left out
of the dashboard, they silently "eat" a rank slot: the ADP-tracked
players' Now Rk numbers jump (3, 5, 6...) with no explanation of the
missing 4. build_dataset()'s UNDRAFTED PERFORMERS section (below the
main ADP loop) fixes this by adding an entry for every such player too
-- ADP rank bucketed at their position's "+" cutoff (never having a
preseason ADP means, by definition, going no better than the
worst-drafted players at that position), current rank their own real,
capped rank. That closes the gap in the Now Rk sequence and surfaces
them for what they usually are: a dramatic, unexpected overperformer.

THREE SCORING FORMATS, EACH WITH TWO "NOW RK" MODES
------------------------------------------------------
Every player gets a `formats` object keyed "standard" / "half_ppr" / "ppr",
matching the dashboard's PPR / Half-PPR / Standard toggle. Inside each one:

  - `adpRank` / `adp`           -- that format's locked preseason ADP
  - `currentRankTotal` / `deltaTotal` -- "Now Rk" by season-TOTAL points
  - `currentRankPPG` / `deltaPPG`     -- "Now Rk" by points-PER-GAME

...so the dashboard's Scoring format and Season Total/Per Game toggles can
both be switched client-side, without re-running this script.

POSITION-DEPTH CUTOFFS ("RB37+" style buckets)
-------------------------------------------------
Every adpRank/currentRankTotal/currentRankPPG value above is capped to a
per-position roster-depth cutoff defined in POSITION_RANK_CAPS below (e.g.
RB ranks stop at 36 -- anyone drafted/performing 37th or worse at the
position is bucketed together as rank 37). This applies to both sides of
the comparison and to the delta between them, so a deep-bench player's
huge single-week performance can't manufacture a misleadingly large delta
against an equally-deep-bench ADP slot. See POSITION_RANK_CAPS and
cap_rank() for the exact cutoffs and how they're applied.

USAGE
-----
    python3 build_dashboard_data.py --season 2026
"""

import argparse
import json
import os
import sys
from datetime import datetime, timezone

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from lib_names import normalize_name, normalize_team, DEFENSE_NAME_TO_TEAM

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(SCRIPT_DIR)
DATA_DIR = os.path.join(PROJECT_ROOT, "data")

# The three scoring formats the dashboard toggles between. `key` is used in
# the JSON output and matches the prefix on current_performance_<season>.csv's
# columns (e.g. "ppr_pos_rank"); `label` is the exact column-name prefix used
# in bos_adp_top250.csv (e.g. "PPR ADP", "PPR Pos Rank").
FORMATS = [
    {"key": "standard", "label": "Standard"},
    {"key": "half_ppr", "label": "Half-PPR"},
    {"key": "ppr", "label": "PPR"},
]

# Which format the dashboard should show on first load. Purely a default --
# the toggle lets a viewer switch to either of the other two at any time.
DEFAULT_SCORING_FORMAT = "ppr"

# ---------------------------------------------------------------------------
# Position-depth cutoffs ("bucketing")
# ---------------------------------------------------------------------------
# Below the roster-relevant depth at each position, exactly *how* deep a
# player is drafted/ranked stops meaning anything practically (there's no
# real difference between "RB40" and "RB60" -- neither is getting drafted
# or started in a normal league). So instead of showing/using the raw
# position rank past this cutoff, every player ranked beyond it -- in ADP
# *and* in current-season performance -- is bucketed together at
# `cutoff + 1` and displayed as e.g. "RB37+". This applies uniformly to
# both halves of the comparison (ADP rank and Now Rk) and to the delta
# computed between them, so a strong Week-1 game by a deep-bench player
# doesn't manufacture a huge, meaningless delta against their (also
# bucketed) ADP rank.
#
# These are roster-depth judgment calls, not derived from anything in the
# data -- edit the numbers here to change where each position's cutoff
# sits.
POSITION_RANK_CAPS = {
    "QB": 24,
    "RB": 36,
    "WR": 36,
    "TE": 24,
    "D/ST": 12,
    "K": 12,
}


def cap_rank(rank, position):
    """Clamp a within-position rank to this project's roster-depth cutoff
    (POSITION_RANK_CAPS above). Anyone ranked beyond the cutoff -- whether
    by ADP or by current performance -- is treated as tied at `cutoff + 1`
    everywhere: in the displayed rank, and in the delta math against the
    other side of the comparison. `None` passes through unchanged (no ADP,
    or no current-season stats yet, stays "no data" rather than becoming a
    fake rank)."""
    if rank is None:
        return None
    cap = POSITION_RANK_CAPS.get(position)
    if cap is None:
        return rank
    return min(rank, cap + 1)


def cap_or_bucket_rank(rank, position, season_started):
    """Same clamping as cap_rank(), but a player with NO current-season
    rank at all (rank is None -- no stat line this week: injured, on
    bye, or just a name nflverse's file doesn't match) is bucketed at
    `cutoff + 1` too, the same "+"-suffixed group used for anyone ranked
    beyond the cutoff, instead of being left out of the dashboard
    entirely. That keeps every position's full/"See All" list genuinely
    complete -- every player from the preseason ADP dataset gets a Now
    Rk slot, even one who hasn't produced anything yet.

    The one exception is `season_started=False`: before ANY player at
    this position has a current-week stat line (i.e. this build ran
    before Week 1 games happened at all), bucketing everyone at once
    would fabricate a "worst possible performer" for the entire
    dashboard before a single snap has been played. In that case this
    still returns None for everyone, same as cap_rank(None, ...), and
    the front end shows its "waiting on this season's first games"
    empty state instead."""
    if rank is not None:
        return cap_rank(rank, position)
    if not season_started:
        return None
    cap = POSITION_RANK_CAPS.get(position)
    return cap + 1 if cap is not None else None


def load_bos_adp() -> pd.DataFrame:
    """Load the locked preseason ADP file and normalize it into a common
    shape: one row per player/defense with position, a join key, and --
    for each of the three scoring formats -- that format's ADP value and
    position rank (or NaN, if that player has a blank cell for this
    format in the source file)."""
    path = os.path.join(DATA_DIR, "bos_adp_top250.csv")
    df = pd.read_csv(path)

    out = df[["Overall Rank", "Player", "Position", "Team"]].copy()
    out = out.rename(columns={
        "Overall Rank": "overall_rank",
        "Player": "player",
        "Position": "position",
        "Team": "team",
    })
    # Normalize position labels to what the dashboard displays.
    out["position"] = out["position"].replace({"PK": "K", "DEF": "D/ST"})

    for fmt in FORMATS:
        adp_col = f"{fmt['label']} ADP"
        rank_col = f"{fmt['label']} Pos Rank"
        out[f"{fmt['key']}_adp"] = df[adp_col]
        # "RB1" -> 1, "DEF7" -> 7, "PK3" -> 3 -- strip the position letters
        # off the front of the label to get the bare rank number. A blank
        # cell in the source file becomes NaN here, same as an actually-
        # unparseable value would -- both mean "no ADP in this format."
        out[f"{fmt['key']}_adp_rank"] = pd.to_numeric(
            df[rank_col].str.extract(r"(\d+)$")[0], errors="coerce"
        )

    # Build the same join key used on the performance side: for defenses,
    # FFC's "Team" column is often blank/inconsistent, so derive the team
    # code from the defense's display name instead.
    def resolve_team(row):
        if row["position"] == "D/ST":
            return DEFENSE_NAME_TO_TEAM.get(row["player"], row["team"])
        return row["team"]

    out["team"] = out.apply(resolve_team, axis=1)
    out["join_key"] = out.apply(
        lambda r: f"{normalize_name(r['player'])}|{normalize_team(r['team'])}", axis=1
    )
    return out


def load_current_performance(season: int) -> pd.DataFrame:
    """Load this season's computed performance file. If the season hasn't
    started yet (or fetch_current_performance.py hasn't been run for it),
    return an empty-but-correctly-shaped frame so the join below degrades
    gracefully instead of crashing."""
    path = os.path.join(DATA_DIR, f"current_performance_{season}.csv")
    if not os.path.exists(path):
        print(f"WARNING: {path} not found -- run fetch_current_performance.py "
              f"--season {season} first. Proceeding with no current-performance data.")
        return pd.DataFrame(columns=["player", "team", "position", "games_played",
                                      "join_name", "join_team"])
    df = pd.read_csv(path)
    if df.empty:
        return df
    df["join_key"] = df["join_name"] + "|" + df["join_team"]
    return df


def build_dataset(season: int):
    adp = load_bos_adp()
    perf = load_current_performance(season)

    perf_lookup = {}
    if not perf.empty:
        perf_lookup = perf.set_index("join_key").to_dict(orient="index")

    # Whether the season has actually gotten underway -- i.e. at least
    # one player anywhere has a current-week stat line. Gates the
    # "bucket the missing ones" behavior in cap_or_bucket_rank() below;
    # see its docstring for why that matters.
    season_started = not perf.empty

    matched, unmatched = 0, []
    by_position = {}

    for _, row in adp.iterrows():
        perf_row = perf_lookup.get(row["join_key"])
        games_played = 0
        if perf_row is not None:
            matched += 1
            games_played = int(perf_row["games_played"])
        else:
            unmatched.append(f"{row['player']} ({row['position']}, {row['team']})")

        # Build one {adpRank, adp, currentRankTotal, deltaTotal,
        # currentRankPPG, deltaPPG} block per scoring format. If this
        # player has no ADP in a given format (blank cell in the source
        # file), the whole block for that format is left null -- the
        # front end filters those out when that format is selected,
        # rather than showing a player with no ADP to compare against.
        formats = {}
        for fmt in FORMATS:
            key = fmt["key"]
            adp_rank_raw = row[f"{key}_adp_rank"]
            adp_val_raw = row[f"{key}_adp"]
            has_adp = pd.notna(adp_rank_raw) and pd.notna(adp_val_raw)

            if not has_adp:
                formats[key] = {
                    "adpRank": None, "adp": None,
                    "currentRankTotal": None, "deltaTotal": None,
                    "currentRankPPG": None, "deltaPPG": None,
                }
                continue

            # Every rank below is capped to this position's roster-depth
            # cutoff (POSITION_RANK_CAPS) before it's stored or used in any
            # delta math -- see cap_rank()'s docstring above. A player with
            # no current-week stat line at all falls through to
            # cap_or_bucket_rank(None, ...), which -- once the season is
            # underway -- buckets them into the same "+" overflow group
            # rather than leaving them out of the dashboard, so every
            # preseason-ADP player at this position gets a Now Rk slot.
            adp_rank = cap_rank(int(adp_rank_raw), row["position"])
            raw_current_total = int(perf_row[f"{key}_pos_rank"]) if perf_row is not None else None
            raw_current_ppg = int(perf_row[f"{key}_ppg_pos_rank"]) if perf_row is not None else None
            current_rank_total = cap_or_bucket_rank(raw_current_total, row["position"], season_started)
            current_rank_ppg = cap_or_bucket_rank(raw_current_ppg, row["position"], season_started)

            formats[key] = {
                "adpRank": adp_rank,
                "adp": round(float(adp_val_raw), 1),
                # Delta follows the same convention throughout: ADP rank
                # minus current rank, so positive = outperforming draft slot.
                "currentRankTotal": current_rank_total,
                "deltaTotal": (adp_rank - current_rank_total) if current_rank_total is not None else None,
                "currentRankPPG": current_rank_ppg,
                "deltaPPG": (adp_rank - current_rank_ppg) if current_rank_ppg is not None else None,
            }

        entry = {
            "player": row["player"],
            "team": row["team"],
            "overallRank": int(row["overall_rank"]),
            "gamesPlayed": games_played,
            "formats": formats,
        }
        by_position.setdefault(row["position"], []).append(entry)

    # ------------------------------------------------------------------
    # UNDRAFTED PERFORMERS
    # ------------------------------------------------------------------
    # Everything above only ever looks at players FROM the ADP file. But
    # nflverse's stats file also includes players who were never on the
    # preseason ADP list at all -- an injury fill-in, a waiver pickup, a
    # rookie nobody drafted -- and one of those can easily outscore half
    # the ADP-tracked field in a given week. Left out entirely, a player
    # like that silently "eats" a rank slot: e.g. the actual #4 tight end
    # in real points that week is one of these, so the ADP-tracked TEs
    # visibly jump from Now Rk 3 to 5 with no explanation. Since never
    # having a preseason ADP is, by definition, going no better than the
    # worst-drafted players at the position, each one is added here with
    # their ADP rank bucketed at the position's "+" cutoff (same bucket
    # used everywhere else for "ranked beyond the depth cutoff") and
    # their own real, capped current rank -- which both closes that gap
    # in the Now Rk sequence AND surfaces them for what they usually are:
    # a dramatic, unexpected overperformer.
    undrafted_added = []
    if not perf.empty:
        adp_join_keys = set(adp["join_key"])
        for _, prow in perf.iterrows():
            if prow["join_key"] in adp_join_keys:
                continue  # already handled above as a matched ADP player
            position = prow["position"]
            cap = POSITION_RANK_CAPS.get(position)
            if cap is None:
                continue  # no roster-depth cutoff defined for this position
            bucketed_adp_rank = cap + 1

            formats = {}
            for fmt in FORMATS:
                key = fmt["key"]
                current_rank_total = cap_rank(int(prow[f"{key}_pos_rank"]), position)
                current_rank_ppg = cap_rank(int(prow[f"{key}_ppg_pos_rank"]), position)
                formats[key] = {
                    "adpRank": bucketed_adp_rank,
                    "adp": None,  # never had a real preseason ADP pick number
                    "currentRankTotal": current_rank_total,
                    "deltaTotal": bucketed_adp_rank - current_rank_total,
                    "currentRankPPG": current_rank_ppg,
                    "deltaPPG": bucketed_adp_rank - current_rank_ppg,
                }

            entry = {
                "player": prow["player"],
                "team": prow["team"],
                "overallRank": 9999,  # no preseason overall rank; unused by the front end
                "gamesPlayed": int(prow["games_played"]),
                "formats": formats,
                # Tells the front end to show "undrafted" instead of a
                # "pick #.#" subtext, since there's no real ADP value.
                "undrafted": True,
            }
            by_position.setdefault(position, []).append(entry)
            undrafted_added.append(f"{prow['player']} ({position}, {prow['team']})")

    total_games_played = int(perf["games_played"].max()) if not perf.empty else 0

    meta = {
        "season": season,
        "generatedAt": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "defaultScoringFormat": DEFAULT_SCORING_FORMAT,
        "maxGamesPlayed": total_games_played,
        "status": "in_season" if total_games_played > 0 else "pre_season",
        "playersMatched": matched,
        "playersUnmatched": len(unmatched),
        # Count of undrafted-performer entries added below (see
        # UNDRAFTED PERFORMERS above) -- players with real current-week
        # stats but no preseason ADP at all. Used for the status-banner
        # note in the front end.
        "undraftedAdded": len(undrafted_added),
        # Passed through so the front end knows, per position, which
        # displayed rank number means "this cutoff and everyone below it"
        # -- it renders that value with a trailing "+" (e.g. "RB37+").
        # Keep this in sync with POSITION_RANK_CAPS above.
        "positionRankCaps": POSITION_RANK_CAPS,
    }

    print(f"Matched {matched}/{len(adp)} ADP players to current-season performance "
          "(this count is format-independent -- a player can still be missing ADP "
          "in one particular scoring format even when matched here).")
    if unmatched:
        bucket_note = (
            "these will be bucketed at their position's '+' cutoff (e.g. 'RB37+') "
            "once the season is underway, rather than shown with no current rank at all:"
            if season_started else
            "these show ADP rank only, no current rank, since the season hasn't started yet:"
        )
        print(f"Unmatched ({len(unmatched)}) -- {bucket_note}")
        for name in unmatched[:25]:
            print(f"  - {name}")
        if len(unmatched) > 25:
            print(f"  ... and {len(unmatched) - 25} more")

    if undrafted_added:
        print(f"Undrafted performers added ({len(undrafted_added)}) -- no preseason ADP, "
              "bucketed at their position's '+' cutoff, real current rank:")
        for name in undrafted_added[:25]:
            print(f"  - {name}")
        if len(undrafted_added) > 25:
            print(f"  ... and {len(undrafted_added) - 25} more")

    return by_position, meta


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--season", type=int, required=True,
                         help="Season to build the dashboard for, e.g. 2026")
    args = parser.parse_args()

    by_position, meta = build_dataset(args.season)

    out = {"meta": meta, "positions": by_position}
    out_path = os.path.join(DATA_DIR, "dashboard_data.json")
    with open(out_path, "w") as f:
        json.dump(out, f, indent=None, separators=(",", ":"))

    total_players = sum(len(v) for v in by_position.values())
    print(f"\nWrote {out_path} ({total_players} players/defenses across "
          f"{len(by_position)} positions). status={meta['status']}")


if __name__ == "__main__":
    main()
