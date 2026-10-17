"""Recency bias, 2021-2025: does how a team did against last week's line predict this week?
Results are in BACKTEST.md.

  CFBD_CACHE=.cfbd_cache python scripts/recency_backtest.py

For every FBS team's consecutive regular-season games (same season, both with closing lines,
median across books):
- spreads: last game's ATS margin (points it beat the spread by) vs covering this game, and
- totals: how far its last game went over or under the total vs this game going over.
Backing a "bounce back" or fading a hot team is the bet being tested. Breakeven at -110 is 52.4%.

API cost: none if the season lines and games are cached (they are fetched by
spread_angles_backtest.py); otherwise 2 calls per season.
"""
import math, os
from collections import defaultdict
from cfb_stats.api import CFBDClient
from cfb_stats.totals import consensus_spread, consensus_total

c = CFBDClient(cache_dir=os.environ.get("CFBD_CACHE"))  # run from the repo root
YEARS = (2021, 2022, 2023, 2024, 2025)


def team_games(y):
    """{team: [game dicts in date order]} with ATS margin and over margin from the team's side."""
    lines = {lr["id"]: (consensus_spread(lr)[0], consensus_total(lr)[0])
             for lr in c.get("/lines", year=y, seasonType="regular")}
    out = defaultdict(list)
    for g in sorted(c.games(y, "regular"), key=lambda g: g.get("startDate") or ""):
        if g.get("homePoints") is None or g.get("awayPoints") is None:
            continue
        spread, total = lines.get(g["id"], (None, None))
        margin = g["homePoints"] - g["awayPoints"]
        for team, sign, cls in ((g["homeTeam"], 1, g.get("homeClassification")),
                                (g["awayTeam"], -1, g.get("awayClassification"))):
            if cls != "fbs":
                continue
            out[team].append({
                "id": g["id"], "week": g["week"],
                "ats": None if spread is None else sign * (margin + spread),
                "over": None if total is None else g["homePoints"] + g["awayPoints"] - total,
            })
    return out


def rec(results):
    w = sum(r == 1 for r in results); l = sum(r == 0 for r in results)
    if not w + l:
        return "-"
    z = (w / (w + l) - .5) / math.sqrt(.25 / (w + l))
    return f"{w}-{l} ({w / (w + l):.1%}, {w - 1.1 * l:+.0f}u, z {z:+.1f})"


def main():
    pairs = []  # (season, last, this) per team
    for y in YEARS:
        for team, gs in team_games(y).items():
            for a, b in zip(gs, gs[1:]):
                pairs.append((y, a, b))
    print(f"{len(pairs)} team consecutive-game pairs, 2021-2025\n")

    buckets = [(-99, -14), (-14, -7), (-7, 7), (7, 14), (14, 99)]
    print("SPREADS: cover rate this game, by last game's ATS margin")
    for lo, hi in buckets:
        res = [int(b["ats"] > 0) for _, a, b in pairs
               if a["ats"] is not None and b["ats"] not in (None, 0) and lo <= a["ats"] < hi]
        print(f"  last game {lo:+4d} to {hi:+4d}: covered {rec(res)}")
    # two straight non-covers
    res = []
    for y in YEARS:
        for team, gs in team_games(y).items():
            for a, b, cc in zip(gs, gs[1:], gs[2:]):
                if None not in (a["ats"], b["ats"], cc["ats"]) and a["ats"] < 0 and b["ats"] < 0 and cc["ats"] != 0:
                    res.append(int(cc["ats"] > 0))
    print(f"  after two straight non-covers: covered {rec(res)}")

    print("\nTOTALS: over rate this game, by how far the team's last game went over/under")
    for lo, hi in buckets:
        res = [int(b["over"] > 0) for _, a, b in pairs
               if a["over"] is not None and b["over"] not in (None, 0) and lo <= a["over"] < hi]
        print(f"  last game {lo:+4d} to {hi:+4d}: went over {rec(res)}")
    # both teams' last games went the same way by 10+
    by_game = defaultdict(list)
    for y, a, b in pairs:
        if a["over"] is not None and b["over"] not in (None, 0):
            by_game[b["id"]].append((a["over"], b["over"]))
    both_over = [int(v[0][1] > 0) for v in by_game.values() if len(v) == 2 and min(x[0] for x in v) >= 10]
    both_under = [int(v[0][1] > 0) for v in by_game.values() if len(v) == 2 and max(x[0] for x in v) <= -10]
    print(f"  both teams' last games over by 10+: went over {rec(both_over)}")
    print(f"  both teams' last games under by 10+: went over {rec(both_under)}")


if __name__ == "__main__":
    main()
