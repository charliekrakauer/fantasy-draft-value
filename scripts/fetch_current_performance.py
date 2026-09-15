#!/usr/bin/env python3
"""
fetch_current_performance.py
=============================
Pulls real, in-season NFL stats and turns them into "current fantasy
position rank" for every player/defense -- the "Now Rk" half of the Value
Board comparison (the other half, "ADP Rk", is the locked preseason data
in data/bos_adp_top250.csv, which this script never touches).

WHERE THE DATA COMES FROM
--------------------------
Everything here comes from nflverse (https://github.com/nflverse/nflverse-data),
a community-maintained, publicly downloadable set of NFL stats built from
official play-by-play data. No login, no API key, no scraping a rankings
website -- just CSV files published as GitHub release assets. We use four
of them:

  1. player_stats.csv          -- weekly offensive stats (QB/RB/WR/TE),
                                   already includes nflverse's own computed
                                   `fantasy_points` (standard) and
                                   `fantasy_points_ppr` (PPR) columns.
  2. player_stats_kicking.csv  -- weekly kicker stats (field goals by
                                   distance bucket, extra points).
  3. player_stats_def.csv      -- weekly *individual* defensive player
                                   stats (sacks, interceptions, fumble
                                   recoveries, defensive TDs, safeties).
                                   We aggregate this up to the TEAM level
                                   to score Defense/Special Teams (D/ST),
                                   since fantasy football drafts a team's
                                   whole defense as one roster slot.
  4. games.csv                 -- the season schedule with final scores,
                                   used to compute each team defense's
                                   "points allowed" score.

WHY WE COMPUTE SCORING OURSELVES FOR K AND D/ST
-------------------------------------------------
nflverse's `fantasy_points` / `fantasy_points_ppr` columns only cover
offensive skill positions. There's no standard "team defense fantasy
points" anywhere in the raw data -- every fantasy site invents its own
D/ST scoring table -- so we apply a commonly-used, ESPN-default-style
table ourselves (see `dst_points_for_week` below). If your actual league
scores kickers or defenses differently, the constants below are the only
things you need to change.

USAGE
-----
    python3 fetch_current_performance.py --season 2026

Output: data/current_performance_<season>.csv, one row per player/defense
with season-to-date fantasy points in all three scoring formats, plus that
player's rank within their position (so it lines up with the "PPR Pos
Rank" style columns already in the BOS ADP file). Ranks are computed two
ways -- by season-total points, and by points-per-game -- since the
dashboard lets you switch between the two.

Raw downloads are cached under data/raw/ so re-running the script doesn't
re-download ~70MB of CSVs every time. Delete that folder (or pass
--refresh) to force a fresh pull once new games have been played.
"""

import argparse
import os
import sys
import pandas as pd

# lib_names.py lives next to this script.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from lib_names import normalize_name, normalize_team, DEFENSE_NAME_TO_TEAM

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(SCRIPT_DIR)
RAW_DIR = os.path.join(PROJECT_ROOT, "data", "raw")
DATA_DIR = os.path.join(PROJECT_ROOT, "data")

# nflverse publishes these as GitHub "release assets" (not regular repo
# files), which is why the URLs point at /releases/download/... rather than
# /raw/... or /blob/....
NFLVERSE_BASE = "https://github.com/nflverse/nflverse-data/releases/download"
SOURCES = {
    "player_stats.csv": f"{NFLVERSE_BASE}/player_stats/player_stats.csv",
    "player_stats_kicking.csv": f"{NFLVERSE_BASE}/player_stats/player_stats_kicking.csv",
    "player_stats_def.csv": f"{NFLVERSE_BASE}/player_stats/player_stats_def.csv",
    "games.csv": f"{NFLVERSE_BASE}/schedules/games.csv",
}

# ---------------------------------------------------------------------------
# D/ST scoring table (standard ESPN-default-style scoring).
# Edit these constants if your league scores defense differently.
# ---------------------------------------------------------------------------
DST_POINTS = {
    "sack": 1,
    "interception": 2,
    "fumble_recovery": 2,   # recovering an opponent's fumble (takeaway)
    "def_or_return_td": 6,  # defensive TD (INT/fumble return) or ST return TD
    "safety": 2,
}

# Points allowed -> fantasy points, as (max_points_allowed_inclusive, fantasy_points),
# checked in order. This is the standard ESPN default tiering.
POINTS_ALLOWED_TIERS = [
    (0, 10),
    (6, 7),
    (13, 4),
    (20, 1),
    (27, 0),
    (34, -1),
    (999, -4),
]

# ---------------------------------------------------------------------------
# Kicker scoring table (standard scoring: distance-tiered field goals + PATs).
# We do not penalize missed kicks -- many "standard" leagues don't either.
# Edit here if yours does.
# ---------------------------------------------------------------------------
def kicker_points_for_row(row) -> float:
    """Standard fantasy points for one kicker's stat line (one game)."""
    pts = 0.0
    pts += 3 * (row.get("fg_made_0_19", 0) or 0)
    pts += 3 * (row.get("fg_made_20_29", 0) or 0)
    pts += 3 * (row.get("fg_made_30_39", 0) or 0)
    pts += 4 * (row.get("fg_made_40_49", 0) or 0)
    pts += 5 * (row.get("fg_made_50_59", 0) or 0)
    pts += 5 * (row.get("fg_made_60_", 0) or 0)
    pts += 1 * (row.get("pat_made", 0) or 0)
    return pts


def points_allowed_to_fantasy_points(points_allowed: float) -> int:
    """Look up the D/ST fantasy points for a given points-allowed total."""
    for max_pts, fantasy_pts in POINTS_ALLOWED_TIERS:
        if points_allowed <= max_pts:
            return fantasy_pts
    return POINTS_ALLOWED_TIERS[-1][1]  # fallback, shouldn't be reached


# ---------------------------------------------------------------------------
# Download / cache helpers
# ---------------------------------------------------------------------------
def fetch_csv(filename: str, refresh: bool) -> pd.DataFrame:
    """Download a source CSV into data/raw/ (or reuse the cached copy)."""
    os.makedirs(RAW_DIR, exist_ok=True)
    local_path = os.path.join(RAW_DIR, filename)
    if refresh or not os.path.exists(local_path):
        url = SOURCES[filename]
        print(f"  downloading {filename} from {url} ...")
        df = pd.read_csv(url, low_memory=False)
        df.to_csv(local_path, index=False)
    else:
        print(f"  using cached {local_path}")
    return pd.read_csv(local_path, low_memory=False)


# ---------------------------------------------------------------------------
# Offense (QB / RB / WR / TE)
# ---------------------------------------------------------------------------
def compute_offense_totals(player_stats: pd.DataFrame, season: int) -> pd.DataFrame:
    """Season-to-date fantasy totals for offensive skill players.

    nflverse already computes per-game `fantasy_points` (standard) and
    `fantasy_points_ppr` for us -- we just sum across the games played this
    season. Half-PPR is the midpoint of standard and PPR: since
    fantasy_points_ppr = fantasy_points + 1 point per reception (verified
    directly against the data), half_ppr = fantasy_points + 0.5 per
    reception = the arithmetic mean of the two nflverse columns.
    """
    df = player_stats[
        (player_stats["season"] == season) & (player_stats["season_type"] == "REG")
    ].copy()

    grouped = (
        df.groupby(["player_display_name", "recent_team", "position"])
        .agg(
            games_played=("week", "nunique"),
            standard_points=("fantasy_points", "sum"),
            ppr_points=("fantasy_points_ppr", "sum"),
        )
        .reset_index()
    )
    grouped["half_ppr_points"] = (grouped["standard_points"] + grouped["ppr_points"]) / 2
    grouped = grouped.rename(
        columns={"player_display_name": "player", "recent_team": "team", "position": "position"}
    )
    # Only keep the four skill positions -- K and DEF are handled separately.
    grouped = grouped[grouped["position"].isin(["QB", "RB", "WR", "TE"])]
    return grouped[["player", "team", "position", "games_played",
                     "standard_points", "half_ppr_points", "ppr_points"]]


# ---------------------------------------------------------------------------
# Kickers
# ---------------------------------------------------------------------------
def compute_kicker_totals(kicking: pd.DataFrame, season: int) -> pd.DataFrame:
    """Season-to-date fantasy totals for kickers.

    Kicker scoring doesn't vary by "PPR" -- there are no receptions -- so
    the same total is reported in all three scoring-format columns. That
    keeps the output shape identical across positions, which simplifies
    the next script (build_dashboard_data.py).
    """
    df = kicking[(kicking["season"] == season) & (kicking["season_type"] == "REG")].copy()
    df["game_points"] = df.apply(kicker_points_for_row, axis=1)

    grouped = (
        df.groupby(["player_display_name", "team"])
        .agg(games_played=("week", "nunique"), total_points=("game_points", "sum"))
        .reset_index()
        .rename(columns={"player_display_name": "player"})
    )
    grouped["position"] = "K"
    grouped["standard_points"] = grouped["total_points"]
    grouped["half_ppr_points"] = grouped["total_points"]
    grouped["ppr_points"] = grouped["total_points"]
    return grouped[["player", "team", "position", "games_played",
                     "standard_points", "half_ppr_points", "ppr_points"]]


# ---------------------------------------------------------------------------
# Team Defense / Special Teams (D/ST)
# ---------------------------------------------------------------------------
def compute_dst_totals(
    player_stats_all: pd.DataFrame,
    player_stats_def: pd.DataFrame,
    games: pd.DataFrame,
    season: int,
) -> pd.DataFrame:
    """Season-to-date fantasy totals for each team's defense/special teams.

    Fantasy football drafts "Seattle Defense" as a single roster slot, but
    nflverse's stats are all per individual player. So we:
      1. Sum individual defensive players' sacks/INTs/fumble recoveries/
         defensive TDs/safeties up to the team level, per week.
      2. Add return TDs (kickoff/punt returns) from the offensive stats
         file's `special_teams_tds` column, also summed to team+week.
      3. Add a "points allowed" score per team per week, from the game
         schedule's final scores.
      4. Sum all of that across the season so far.
    """
    def_df = player_stats_def[
        (player_stats_def["season"] == season) & (player_stats_def["season_type"] == "REG")
    ].copy()

    team_def_weekly = (
        def_df.groupby(["team", "week"])
        .agg(
            sacks=("def_sacks", "sum"),
            interceptions=("def_interceptions", "sum"),
            fumble_recoveries=("def_fumble_recovery_opp", "sum"),
            def_tds=("def_tds", "sum"),
            safeties=("def_safety", "sum"),
        )
        .reset_index()
    )

    # Special-teams return TDs are recorded on the *offense* stats file
    # (they're credited to the returner, who's usually a WR/RB/CB), so we
    # pull them from there and attribute them to that player's team.
    st_df = player_stats_all[
        (player_stats_all["season"] == season) & (player_stats_all["season_type"] == "REG")
    ].copy()
    team_st_weekly = (
        st_df.groupby(["recent_team", "week"])["special_teams_tds"]
        .sum()
        .reset_index()
        .rename(columns={"recent_team": "team", "special_teams_tds": "st_tds"})
    )

    merged = team_def_weekly.merge(team_st_weekly, on=["team", "week"], how="outer").fillna(0)

    # Points allowed: for each team+week, find their game and take the
    # *opponent's* score (i.e. what their defense gave up).
    g = games[(games["season"] == season) & (games["game_type"] == "REG")].copy()
    g = g.dropna(subset=["home_score", "away_score"])  # only completed games
    home_rows = g[["week", "home_team", "away_score"]].rename(
        columns={"home_team": "team", "away_score": "points_allowed"}
    )
    away_rows = g[["week", "away_team", "home_score"]].rename(
        columns={"away_team": "team", "home_score": "points_allowed"}
    )
    points_allowed = pd.concat([home_rows, away_rows], ignore_index=True)

    merged = merged.merge(points_allowed, on=["team", "week"], how="left")
    merged["points_allowed"] = merged["points_allowed"].fillna(0)

    merged["week_points"] = (
        merged["sacks"] * DST_POINTS["sack"]
        + merged["interceptions"] * DST_POINTS["interception"]
        + merged["fumble_recoveries"] * DST_POINTS["fumble_recovery"]
        + (merged["def_tds"] + merged["st_tds"]) * DST_POINTS["def_or_return_td"]
        + merged["safeties"] * DST_POINTS["safety"]
        + merged["points_allowed"].apply(points_allowed_to_fantasy_points)
    )

    season_totals = (
        merged.groupby("team")
        .agg(games_played=("week", "nunique"), total_points=("week_points", "sum"))
        .reset_index()
    )
    season_totals["position"] = "D/ST"
    # DEFENSE_NAME_TO_TEAM maps display name -> FFC/ADP-style team code
    # (e.g. "LA Rams Defense" -> "LAR"), but this function works entirely in
    # nflverse's team-code space (e.g. "LA"), so normalize before reversing.
    nflverse_code_to_name = {
        normalize_team(code): name for name, code in DEFENSE_NAME_TO_TEAM.items()
    }
    season_totals["player"] = season_totals["team"].map(nflverse_code_to_name)
    season_totals["standard_points"] = season_totals["total_points"]
    season_totals["half_ppr_points"] = season_totals["total_points"]
    season_totals["ppr_points"] = season_totals["total_points"]
    return season_totals[["player", "team", "position", "games_played",
                           "standard_points", "half_ppr_points", "ppr_points"]]


# ---------------------------------------------------------------------------
# Ranking
# ---------------------------------------------------------------------------
def add_position_ranks(df: pd.DataFrame) -> pd.DataFrame:
    """Rank every player within their position, separately for each scoring
    format (1 = most fantasy points at that position so far) -- and twice
    over: once by season-total points, once by points-per-game.

    These two views can disagree a lot early in a season. A player who
    exploded for one huge game but has only played once can lead the
    points-per-game ranking while sitting well down the total-points
    ranking, and vice versa for a very good player who missed time with an
    injury. The dashboard lets you pick which one "Now Rk" means; we just
    need to compute both here.
    """
    df = df.copy()
    # Guard against dividing by zero for a row with 0 games played (shouldn't
    # normally happen -- a row only exists here because we saw at least one
    # week of stats for that player -- but this keeps the math safe either way).
    safe_games = df["games_played"].replace(0, pd.NA)

    for fmt in ["standard", "half_ppr", "ppr"]:
        total_col = f"{fmt}_points"
        ppg_col = f"{fmt}_ppg"
        df[ppg_col] = df[total_col] / safe_games

        for value_col, rank_suffix in [(total_col, "pos_rank"), (ppg_col, "ppg_pos_rank")]:
            rank_col = f"{fmt}_{rank_suffix}"
            df[rank_col] = (
                df.groupby("position")[value_col]
                .rank(method="first", ascending=False)
                .astype(int)
            )
    return df


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--season", type=int, required=True,
                         help="NFL season to compute current performance for, e.g. 2026")
    parser.add_argument("--refresh", action="store_true",
                         help="Re-download source CSVs instead of using the data/raw/ cache")
    args = parser.parse_args()

    print(f"Fetching nflverse source data (season={args.season})...")
    player_stats = fetch_csv("player_stats.csv", args.refresh)
    kicking = fetch_csv("player_stats_kicking.csv", args.refresh)
    player_stats_def = fetch_csv("player_stats_def.csv", args.refresh)
    games = fetch_csv("games.csv", args.refresh)

    print("Computing season-to-date fantasy totals...")
    offense = compute_offense_totals(player_stats, args.season)
    kickers = compute_kicker_totals(kicking, args.season)
    dst = compute_dst_totals(player_stats, player_stats_def, games, args.season)

    combined = pd.concat([offense, kickers, dst], ignore_index=True)

    if combined.empty:
        print(
            f"\nNo completed games found for season {args.season}. This is expected if "
            "the season hasn't started yet (or you're pointing at a future season) -- "
            "the dashboard will show ADP rank with no current-rank comparison until "
            "there's at least one week of games to sum up."
        )
        # Still write an (empty, correctly-shaped) file so downstream
        # scripts don't crash -- they handle "no current data" explicitly.
        combined = pd.DataFrame(columns=[
            "player", "team", "position", "games_played",
            "standard_points", "half_ppr_points", "ppr_points",
            "standard_ppg", "half_ppr_ppg", "ppr_ppg",
            "standard_pos_rank", "half_ppr_pos_rank", "ppr_pos_rank",
            "standard_ppg_pos_rank", "half_ppr_ppg_pos_rank", "ppr_ppg_pos_rank",
            "join_name", "join_team",
        ])
    else:
        combined = add_position_ranks(combined)
        # Add normalized join keys used by build_dashboard_data.py.
        combined["join_name"] = combined["player"].apply(normalize_name)
        combined["join_team"] = combined["team"].apply(normalize_team)

    out_path = os.path.join(DATA_DIR, f"current_performance_{args.season}.csv")
    combined.to_csv(out_path, index=False)
    print(f"\nWrote {len(combined)} rows to {out_path}")
    if not combined.empty:
        weeks = sorted(pd.concat([
            player_stats[player_stats["season"] == args.season]["week"],
        ]).unique().tolist())
        print(f"Weeks with data this season: {weeks}")


if __name__ == "__main__":
    main()
