"""Turn an injury list into adjustments to a team's expected EPA/play.

The CFBD API has no injury data, so the list comes from you: a CSV with columns

    team,player,status[,side,epa_delta]

status is out / doubtful / questionable / probable (or a probability 0-1 that the
player misses the game). For offensive players (side blank or "off") the impact is
estimated from data:

    impact = P(out) x usage share x (player EPA/play - replacement EPA/play) x credit x IMPACT_SCALE

- player EPA/play is their season average, shrunk toward their position's average by
  sample size (SHRINK_PLAYS pseudo-plays) so a hot two-game start isn't taken at face value.
- replacement is a backup-level player at the position: the REPLACEMENT_PCTL percentile
  of players at that position with real playing time.
- credit is 1 for QBs and NON_QB_CREDIT for others: a pass play's EPA is credited to both
  the passer and the receiver, so counting receivers fully would double count.

Defensive players have no per-player EPA in the API, so for side "def" give epa_delta
yourself: the change in EPA/play the defense allows with the player out (e.g. 0.02 for a
star pass rusher). epa_delta, when given, also overrides the estimate for offense.
"""

import csv
from collections import defaultdict

import numpy as np

STATUS_PROB = {"out": 1.0, "doubtful": 0.75, "questionable": 0.4, "probable": 0.1}
SHRINK_PLAYS = 60
REPLACEMENT_PCTL = 20
MIN_PLAYS_FOR_REPLACEMENT = {"QB": 60}
DEFAULT_MIN_PLAYS = 20
NON_QB_CREDIT = 0.5
# Calibrated on 2025: in 181 games where a team's season-to-date starting QB sat, the raw
# estimate overshot (actual totals fell ~1 pt vs a raw estimate of ~6). Least squares put
# the right scale at 0.36 (90% bootstrap interval 0.14-0.56). Backups play better than a
# 20th-percentile QB and offenses adapt, so data-driven impacts are scaled by this.
IMPACT_SCALE = 0.35


def _prob(status):
    s = str(status).strip().lower()
    if s in STATUS_PROB:
        return STATUS_PROB[s]
    try:
        return min(max(float(s), 0.0), 1.0)
    except ValueError:
        raise ValueError(f"unknown injury status {status!r}; use {', '.join(STATUS_PROB)} or 0-1")


def load_injuries(path):
    with open(path, newline="") as f:
        rows = [r for r in csv.DictReader(f) if (r.get("team") or "").strip()]
    out = []
    for r in rows:
        delta = (r.get("epa_delta") or "").strip()
        out.append({
            "team": r["team"].strip(),
            "player": (r.get("player") or "").strip(),
            "prob_out": _prob(r.get("status", "out")),
            "side": (r.get("side") or "off").strip().lower() or "off",
            "epa_delta": float(delta) if delta else None,
        })
    return out


def player_values(season_ppa, usage):
    """(team, lower-case name) -> {position, plays, epa, usage}, plus per-position stats."""
    usage_by_id = {u["id"]: (u.get("usage") or {}).get("overall") for u in usage}
    players = {}
    for p in season_ppa:
        avg = (p.get("averagePPA") or {}).get("all")
        total = (p.get("totalPPA") or {}).get("all")
        if not avg or total is None:
            continue
        players[(p["team"], p["name"].lower())] = {
            "name": p["name"],
            "position": p.get("position"),
            "plays": abs(total / avg),
            "epa": avg,
            "usage": usage_by_id.get(p["id"]) or 0.0,
        }
    by_pos = defaultdict(list)
    for v in players.values():
        by_pos[v["position"]].append(v)
    positions = {}
    for pos, vals in by_pos.items():
        min_plays = MIN_PLAYS_FOR_REPLACEMENT.get(pos, DEFAULT_MIN_PLAYS)
        regulars = [v for v in vals if v["plays"] >= min_plays] or vals
        epas = np.array([v["epa"] for v in regulars])
        plays = np.array([v["plays"] for v in regulars])
        positions[pos] = {
            "mean": float(np.average(epas, weights=plays)),
            "replacement": float(np.percentile(epas, REPLACEMENT_PCTL)),
        }
    return players, positions


def player_impact(value, positions):
    """Change in team offensive EPA/play if this player sits (negative = offense worse)."""
    pos = positions.get(value["position"], {"mean": 0.0, "replacement": 0.0})
    shrunk = (value["epa"] * value["plays"] + pos["mean"] * SHRINK_PLAYS) / (value["plays"] + SHRINK_PLAYS)
    credit = 1.0 if value["position"] == "QB" else NON_QB_CREDIT
    return -value["usage"] * (shrunk - pos["replacement"]) * credit * IMPACT_SCALE


def team_offsets(injuries, players, positions):
    """-> ({team: offense EPA/play delta}, {team: defense EPA/play-allowed delta}, detail rows)."""
    off, dfn, detail = defaultdict(float), defaultdict(float), []
    for inj in injuries:
        team, side = inj["team"], inj["side"]
        if inj["epa_delta"] is not None:
            per_game = inj["epa_delta"]
            how = "manual"
        elif side == "def":
            detail.append({**inj, "impact": None, "note": "defense needs epa_delta; skipped"})
            continue
        else:
            value = players.get((team, inj["player"].lower()))
            if value is None:
                detail.append({**inj, "impact": None, "note": "player not found for team; check spelling"})
                continue
            per_game = player_impact(value, positions)
            how = f"{value['position']} usage {value['usage']:.2f}, epa {value['epa']:+.3f} over {value['plays']:.0f} plays"
        impact = inj["prob_out"] * per_game
        (dfn if side == "def" else off)[team] += impact
        detail.append({**inj, "impact": round(impact, 4), "note": how})
    return dict(off), dict(dfn), detail
