"""
lib_names.py
============
Small shared helpers for matching a player between two different data
sources that don't spell names or team codes identically.

Why this file exists
---------------------
Our "Beginning Of Season" (BOS) ADP data comes from FantasyFootballCalculator
(FFC). Our in-season performance data comes from nflverse (a community project
that publishes weekly NFL stats derived from play-by-play data). Both sources
describe the same real-world players and teams, but they don't always use the
exact same text for them:

  * Names: "A.J. Brown" vs "AJ Brown", "D'Andre Swift" vs "DAndre Swift",
    "Kenneth Walker III" vs "Kenneth Walker" (suffix dropped), etc.
  * Team codes: FFC calls the Rams "LAR", nflverse calls them "LA".

Rather than hand-maintain a giant lookup table, we normalize both sides down
to a "join key" that strips out the parts most likely to differ (punctuation,
generational suffixes, whitespace differences) and match on that. This isn't
bulletproof -- two different players could theoretically collide -- but for a
few hundred fantasy-relevant players it works well in practice, and the build
script reports anything it couldn't match so you can eyeball the misses.
"""

import re

# nflverse and FFC disagree on a small number of team abbreviations.
# Left side = FFC / ESPN-style code (what's in our BOS ADP file).
# Right side = nflverse's code (what's in the nflverse stats files).
TEAM_CODE_TO_NFLVERSE = {
    "LAR": "LA",     # LA Rams
    # Older/alternate codes some sources still use, kept here for safety
    # even though the current season shouldn't need them:
    "WSH": "WAS",    # Washington
    "JAC": "JAX",    # Jacksonville
    "OAK": "LV",     # Raiders (relocated)
    "SD": "LAC",     # Chargers (relocated)
    "STL": "LAR",    # Rams (relocated) -- note: maps to the FFC code above,
                      # so if this ever fires, run the value through
                      # TEAM_CODE_TO_NFLVERSE a second time.
}

# Suffixes that show up inconsistently across sources and should be ignored
# for matching purposes (we still display the player's real name elsewhere --
# this is only used to build the comparison key).
_SUFFIXES = {"jr", "sr", "ii", "iii", "iv", "v"}


def normalize_team(code: str) -> str:
    """Map a team code onto nflverse's convention.

    Example: normalize_team("LAR") -> "LA"
             normalize_team("KC")  -> "KC"  (unchanged, already matches)
    """
    if code is None:
        return code
    code = code.strip().upper()
    return TEAM_CODE_TO_NFLVERSE.get(code, code)


def normalize_name(name: str) -> str:
    """Collapse a player's display name into a loose join key.

    Steps: lowercase -> drop periods/apostrophes -> replace remaining
    punctuation with a space -> drop generational suffixes (Jr, III, ...)
    -> collapse whitespace.

    Example: "A.J. Brown"        -> "aj brown"
             "D'Andre Swift"     -> "dandre swift"
             "Kenneth Walker III"-> "kenneth walker"
    """
    if name is None:
        return name
    n = name.lower()
    n = n.replace(".", "").replace("'", "")
    n = re.sub(r"[^a-z0-9\s]", " ", n)
    tokens = [t for t in n.split() if t not in _SUFFIXES]
    return " ".join(tokens).strip()


def player_key(name: str, team: str) -> str:
    """The full join key we match players on: normalized name + team.

    Team is included (rather than matching on name alone) because a handful
    of common last names repeat across the league every year, and the team
    disambiguates them cheaply.
    """
    return f"{normalize_name(name)}|{normalize_team(team)}"


# Defense/Special Teams "players" are listed by team in ADP data
# (e.g. "Seattle Defense") but by team code in the stats data. This maps
# a defense's ADP display name to its team code so it can be joined the
# same way as an individual player.
DEFENSE_NAME_TO_TEAM = {
    "Arizona Defense": "ARI", "Atlanta Defense": "ATL", "Baltimore Defense": "BAL",
    "Buffalo Defense": "BUF", "Carolina Defense": "CAR", "Chicago Defense": "CHI",
    "Cincinnati Defense": "CIN", "Cleveland Defense": "CLE", "Dallas Defense": "DAL",
    "Denver Defense": "DEN", "Detroit Defense": "DET", "Green Bay Defense": "GB",
    "Houston Defense": "HOU", "Indianapolis Defense": "IND", "Jacksonville Defense": "JAX",
    "Kansas City Defense": "KC", "LA Chargers Defense": "LAC", "LA Rams Defense": "LAR",
    "Las Vegas Defense": "LV", "Miami Defense": "MIA", "Minnesota Defense": "MIN",
    "New England Defense": "NE", "New Orleans Defense": "NO", "NY Giants Defense": "NYG",
    "NY Jets Defense": "NYJ", "Philadelphia Defense": "PHI", "Pittsburgh Defense": "PIT",
    "Seattle Defense": "SEA", "San Francisco Defense": "SF", "Tampa Bay Defense": "TB",
    "Tennessee Defense": "TEN", "Washington Defense": "WAS",
}
