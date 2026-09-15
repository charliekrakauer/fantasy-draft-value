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

A player with NO current-season stats yet (rookie who hasn't debuted,
season hasn't started, etc.) gets null current-rank/delta fields rather
than a fabricated number -- the front end shows "–" for these instead of
pretending we know something we don't. Likewise, a player missing from one
scoring format's ADP column in the source file (a blank cell -- see the
Notes tab of the original ADP workbook) gets nulled out entirely *for that
format*, so switching the dashboard's scoring-format toggle to a format
that player lacks just drops them from view rather than showing a broken
row.

THREE SCORING FORMATS, EACH WITH TWO "NOW RK" MODES
------------------------------------------------------
Every player gets a `formats` object keyed "standard" / "half_ppr" / "ppr",
matching the dashboard's PPR / Half-PPR / Standard toggle. Inside each one:

  - `adpRank` / `adp`           -- that format's locked preseason ADP
  - `currentRankTotal` / `deltaTotal` -- "Now Rk" by season-TOTAL points
  - `currentRankPPG` / `deltaPPG`     -- "Now Rk" by points-PER-GAME

...so the dashboard's Scoring format and Season Total/Per Game toggles can
both be switched client-side, without re-running this script.

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

            adp_rank = int(adp_rank_raw)
            current_rank_total = None
            current_rank_ppg = None
            if perf_row is not None:
                current_rank_total = int(perf_row[f"{key}_pos_rank"])
                current_rank_ppg = int(perf_row[f"{key}_ppg_pos_rank"])

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

    total_games_played = int(perf["games_played"].max()) if not perf.empty else 0

    meta = {
        "season": season,
        "generatedAt": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "defaultScoringFormat": DEFAULT_SCORING_FORMAT,
        "maxGamesPlayed": total_games_played,
        "status": "in_season" if total_games_played > 0 else "pre_season",
        "playersMatched": matched,
        "playersUnmatched": len(unmatched),
    }

    print(f"Matched {matched}/{len(adp)} ADP players to current-season performance "
          "(this count is format-independent -- a player can still be missing ADP "
          "in one particular scoring format even when matched here).")
    if unmatched:
        print(f"Unmatched ({len(unmatched)}) -- these will show ADP rank only, no current rank:")
        for name in unmatched[:25]:
            print(f"  - {name}")
        if len(unmatched) > 25:
            print(f"  ... and {len(unmatched) - 25} more")

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
