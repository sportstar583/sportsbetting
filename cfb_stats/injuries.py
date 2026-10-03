"""Turn an injury list into adjustments to a team's expected EPA/play.

The CFBD API has no injury data, so the list comes from you, or from a conference
availability report via cfb_stats.availability. CSV columns:

    team,player,status[,side,epa_delta,player_id,position]

status is out / doubtful / questionable / probable / game time decision / out - (1st half),
or a probability 0-1 that the player misses the game. player_id (CFBD athlete id), when
present, is used for matching instead of the name.

Offense (side blank or "off"), estimated from data:

    impact = P(out) x usage share x (player EPA/play - replacement EPA/play) x credit x IMPACT_SCALE

- player EPA/play is their season average, shrunk toward their position's average by
  sample size (SHRINK_PLAYS pseudo-plays) so a hot two-game start isn't taken at face value.
- replacement is a backup-level player at the position: the REPLACEMENT_PCTL percentile
  of players at that position with real playing time.
- credit is 1 for QBs and NON_QB_CREDIT for others: a pass play's EPA is credited to both
  the passer and the receiver, so counting receivers fully would double count.
- Offensive linemen have no individual stats, so they get no estimate (give epa_delta).

Defense (side "def"), estimated from box-score production:

    impact = P(out) x DEF_SCALE x player's share of team defensive production

production = tackles + 2 x TFL + 3 x sacks + 2 x pass breakups + 3 x INTs + 0.5 x QB hurries.
A defender who hasn't recorded stats counts as zero. DEF_SCALE is calibrated on who actually
played 2023-2025 (cfb_stats.defense, see BACKTEST.md). Giving epa_delta overrides the
estimate for any player.
"""

import csv
import re
from collections import defaultdict

import numpy as np

STATUS_PROB = {
    "out": 1.0,
    "doubtful": 0.75,
    "game time decision": 0.5,
    "out - (1st half)": 0.5,  # misses about half the game
    "questionable": 0.4,
    "probable": 0.1,
    "available": 0.0,
}
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
# EPA/play allowed if a defense lost 100% of its production to replacement players. Fit on
# 2023-2025 box scores (a regular with no stat line = out, see cfb_stats.defense): 0.08 for
# starters with 5%+ of team production (90% bootstrap 0.02-0.14), 0.04 if 3%+ regulars count
# too. A defender with a 10% share (a top starter) -> +0.008 EPA/play, under a point a game.
DEF_SCALE = 0.08
DEF_WEIGHTS = {"TOT": 1.0, "TFL": 2.0, "SACKS": 3.0, "PD": 2.0, "QB HUR": 0.5, "INT": 3.0}
OFFENSE_POSITIONS = {"QB", "RB", "FB", "WR", "TE", "OL", "OT", "OG", "C", "IOL", "ATH"}


def _prob(status):
    s = re.sub(r"\s+", " ", str(status).strip().lower())
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
            "player_id": (r.get("player_id") or "").strip() or None,
            "position": (r.get("position") or "").strip() or None,
        })
    return out


def _keys(team, name, pid):
    keys = [(team, name.lower())]
    if pid:
        keys.insert(0, (team, f"id:{pid}"))
    return keys


def _lookup(table, inj):
    for key in _keys(inj["team"], inj["player"], inj.get("player_id")):
        if key in table:
            return table[key]
    return None


def player_values(season_ppa, usage):
    """(team, name or id) -> {position, plays, epa, usage}, plus per-position stats."""
    usage_by_id = {u["id"]: (u.get("usage") or {}).get("overall") for u in usage}
    players, unique = {}, []
    for p in season_ppa:
        avg = (p.get("averagePPA") or {}).get("all")
        total = (p.get("totalPPA") or {}).get("all")
        if not avg or total is None:
            continue
        value = {
            "name": p["name"],
            "position": p.get("position"),
            "plays": abs(total / avg),
            "epa": avg,
            "usage": usage_by_id.get(p.get("id")) or 0.0,
        }
        unique.append(value)
        for key in _keys(p["team"], p["name"], p.get("id")):
            players[key] = value
    by_pos = defaultdict(list)
    for v in unique:
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


def defender_values(stat_rows):
    """(team, name or id) -> {share, production} from /stats/player/season rows
    (categories "defensive" and "interceptions")."""
    prod, names = defaultdict(float), {}
    for r in stat_rows:
        w = DEF_WEIGHTS.get(r.get("statType"))
        if w is None or (r.get("category") == "interceptions") != (r.get("statType") == "INT"):
            continue
        try:
            val = float(r["stat"])
        except (TypeError, ValueError):
            continue
        key = (r["team"], r["playerId"])
        prod[key] += w * val
        names[key] = r["player"]
    team_total = defaultdict(float)
    for (team, _), v in prod.items():
        team_total[team] += v
    out = {}
    for (team, pid), v in prod.items():
        value = {"share": v / team_total[team] if team_total[team] else 0.0, "production": v}
        for key in _keys(team, names[(team, pid)], pid):
            out[key] = value
    return out


def player_impact(value, positions):
    """Change in team offensive EPA/play if this player sits (negative = offense worse)."""
    pos = positions.get(value["position"], {"mean": 0.0, "replacement": 0.0})
    shrunk = (value["epa"] * value["plays"] + pos["mean"] * SHRINK_PLAYS) / (value["plays"] + SHRINK_PLAYS)
    credit = 1.0 if value["position"] == "QB" else NON_QB_CREDIT
    return -value["usage"] * (shrunk - pos["replacement"]) * credit * IMPACT_SCALE


def team_offsets(injuries, players, positions, defenders=None):
    """-> ({team: offense EPA/play delta}, {team: defense EPA/play-allowed delta}, detail rows)."""
    off, dfn, detail = defaultdict(float), defaultdict(float), []
    for inj in injuries:
        team, side = inj["team"], inj["side"]
        if inj["prob_out"] <= 0:
            continue
        if inj["epa_delta"] is not None:
            per_game, how = inj["epa_delta"], "manual"
        elif side == "def":
            value = _lookup(defenders, inj) if defenders else None
            if defenders is None:
                detail.append({**inj, "impact": None, "note": "defense needs epa_delta; skipped"})
                continue
            if value is None:
                detail.append({**inj, "impact": 0.0, "note": "no defensive stats this season (depth player)"})
                continue
            per_game = DEF_SCALE * value["share"]
            how = f"{value['share']:.1%} of team defensive production"
        else:
            value = _lookup(players, inj)
            if value is None:
                note = ("offensive line: no individual stats, give epa_delta to count it"
                        if (inj.get("position") or "").upper() in {"OL", "OT", "OG", "C", "IOL"}
                        else "no offensive EPA this season (depth player or name mismatch)")
                detail.append({**inj, "impact": None if "line" in note else 0.0, "note": note})
                continue
            per_game = player_impact(value, positions)
            how = f"{value['position']} usage {value['usage']:.2f}, epa {value['epa']:+.3f} over {value['plays']:.0f} plays"
        impact = inj["prob_out"] * per_game
        (dfn if side == "def" else off)[team] += impact
        detail.append({**inj, "impact": round(impact, 4), "note": how})
    return dict(off), dict(dfn), detail
