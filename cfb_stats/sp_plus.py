"""Bill Connelly's SP+ as a reference next to the model (not used for picks).

CFBD's free tier only has final SP+ for past seasons, so current ratings come from a CSV saved
each week at data/<year>/sp_plus_week<N>.csv:

  team,sp,off,def,st          (other columns are ignored)

SP+ offense and defense ratings are points per game against an average opponent, so

  team points = offense + opponent defense - national average (offense and defense averaged)
  margin      = home rating - away rating + home field

Checked on 2024-2025 final ratings: the implied totals average within about 1 point of the
actual totals, and home teams beat the rating difference by 2.5-3 points. It was not
backtested with in-season ratings (CFBD has no weekly snapshots); see BACKTEST.md.
"""

import csv
import os
import statistics

HOME_FIELD = 2.5
# National averages, used when the file doesn't cover most of FBS (2022-2025 finals: 27.0-27.8
# offense, 26.5-27.0 defense).
DEFAULT_OFF, DEFAULT_DEF = 27.2, 26.6
MIN_TEAMS_FOR_AVERAGE = 100

# SP+ tables (ESPN, spreadsheets) name some schools differently from CFBD.
ALIASES = {
    "Miami-FL": "Miami", "Miami (FL)": "Miami", "Miami-OH": "Miami (OH)", "Miami (Ohio)": "Miami (OH)",
    "Hawaii": "Hawai'i", "San Jose State": "San José State", "UMass": "Massachusetts",
    "Appalachian State": "App State", "Sam Houston State": "Sam Houston", "Louisiana-Lafayette": "Louisiana",
    "Louisiana-Monroe": "UL Monroe", "ULM": "UL Monroe", "Southern Mississippi": "Southern Miss",
    "Connecticut": "UConn", "Florida International": "FIU", "Texas-San Antonio": "UTSA",
    "Texas-El Paso": "UTEP", "Central Florida": "UCF", "Southern California": "USC",
    "Brigham Young": "BYU", "Mississippi": "Ole Miss", "North Carolina State": "NC State",
}


def path_for(out_dir, week):
    return os.path.join(out_dir, f"sp_plus_week{week}.csv")


def load(path):
    """{"teams": {team: {"sp", "off", "def"}}, "off_avg", "def_avg"} or None if there's no file."""
    if not path or not os.path.exists(path):
        return None
    teams = {}
    with open(path, newline="") as f:
        for r in csv.DictReader(f):
            name = (r.get("team") or "").strip()
            try:
                vals = {k: float(r[k]) for k in ("sp", "off", "def")}
            except (KeyError, TypeError, ValueError):
                continue
            teams[ALIASES.get(name, name)] = vals
    full = len(teams) >= MIN_TEAMS_FOR_AVERAGE
    return {
        "teams": teams,
        "off_avg": statistics.mean(t["off"] for t in teams.values()) if full else DEFAULT_OFF,
        "def_avg": statistics.mean(t["def"] for t in teams.values()) if full else DEFAULT_DEF,
    }


def project(sp, home, away, neutral=False):
    """(SP+ total, SP+ home margin), or (None, None) if either team isn't rated."""
    if not sp:
        return None, None
    h, a = sp["teams"].get(home), sp["teams"].get(away)
    if not h or not a:
        return None, None
    lg = (sp["off_avg"] + sp["def_avg"]) / 2
    home_pts = h["off"] + a["def"] - lg
    away_pts = a["off"] + h["def"] - lg
    margin = h["sp"] - a["sp"] + (0 if neutral else HOME_FIELD)
    return round(home_pts + away_pts, 1), round(margin, 1)
