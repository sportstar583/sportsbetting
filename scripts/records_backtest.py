"""Season-to-date ATS and over/under records: does the market shade lines toward teams with good
records, and is it right to? 2023-2025 saved backtests (lines from week 4 on, so a record counts
games with a line from week 4). No API calls beyond the cached schedules.

For each game: each team's season ATS cover % and over % so far (4+ graded games), then
  shading: market line minus the model's number (spread: how many more points the market gives
           the team than the model does; total: market total minus model total)
  result:  this game against the closing line

  python scripts/records_backtest.py
"""
import argparse
import csv
import math
import os
import sys
from collections import defaultdict

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from cfb_stats.api import CFBDClient  # noqa: E402

MIN_GAMES = 4


def slope(x, y):
    x = x - x.mean()
    b = float(x @ y / (x @ x))
    return b, math.sqrt(((y - y.mean() - b * x) ** 2).mean() / (x @ x))


def rec(vals):
    w, lo = sum(v > 0 for v in vals), sum(v < 0 for v in vals)
    return f"{w}-{lo} ({w / max(w + lo, 1):.1%})"


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--cache", default=".cfbd_cache")
    p.add_argument("--years", type=int, nargs="+", default=[2023, 2024, 2025])
    args = p.parse_args()
    client = CFBDClient(cache_dir=args.cache)
    S, T = [], []
    for y in args.years:
        games = sorted(client.games(y, "regular"), key=lambda g: (g["week"], g.get("startDate") or ""))
        sp = {int(r["game_id"]): r for r in csv.DictReader(open(f"data/{y}/spreads_backtest_games.csv"))
              if r["market_spread"] and r["actual_margin"]}
        tt = {int(r["game_id"]): r for r in csv.DictReader(open(f"data/{y}/totals_backtest_games.csv"))}
        ats, ou = defaultdict(list), defaultdict(list)  # team -> results so far (+1 / -1)
        for g in games:
            gid = g["id"]
            s, t = sp.get(gid), tt.get(gid)
            h, a = g["homeTeam"], g["awayTeam"]
            if s:
                line, act, proj = float(s["market_spread"]), float(s["actual_margin"]), float(s["proj_margin"])
                if len(ats[h]) >= MIN_GAMES and len(ats[a]) >= MIN_GAMES:
                    ph = sum(v > 0 for v in ats[h]) / len(ats[h])
                    pa = sum(v > 0 for v in ats[a]) / len(ats[a])
                    # shading toward home: market expects home to win by -line, model by proj
                    S.append({"season": y, "rec_diff": ph - pa, "ph": ph, "pa": pa,
                              "shade": -line - proj, "cover": act + line})
                c = act + line
                if c:
                    ats[h].append(1 if c > 0 else -1)
                    ats[a].append(-1 if c > 0 else 1)
            if t:
                mkt, act_t, proj_t = float(t["market_total"]), float(t["actual_total"]), float(t["proj_total"])
                if len(ou[h]) >= MIN_GAMES and len(ou[a]) >= MIN_GAMES:
                    oh = sum(v > 0 for v in ou[h]) / len(ou[h])
                    oa = sum(v > 0 for v in ou[a]) / len(ou[a])
                    T.append({"season": y, "over_pct": (oh + oa) / 2, "oh": oh, "oa": oa,
                              "shade": mkt - proj_t, "over": act_t - mkt})
                d = act_t - mkt
                if d:
                    for team in (h, a):
                        ou[team].append(1 if d > 0 else -1)

    print(f"SPREADS ({len(S)} games, both teams {MIN_GAMES}+ graded games): home ATS % minus away ATS %")
    x = np.array([r["rec_diff"] for r in S])
    b, se = slope(x, np.array([r["shade"] for r in S]))
    print(f"  shading: market favors the better-ATS team by {b / 10:+.2f} pts per 10 points of ATS % "
          f"beyond the model (SE {se / 10:.2f})")
    b, se = slope(x, np.array([r["cover"] for r in S]))
    print(f"  result:  better-ATS team covers by {b / 10:+.2f} pts per 10 points of ATS % (SE {se / 10:.2f})")
    for hi, lo in ((0.7, 0.5), (0.75, 0.4), (0.8, 0.5)):
        fade = []
        for r in S:
            if r["ph"] >= hi and r["pa"] <= lo:
                fade.append(-r["cover"])
            elif r["pa"] >= hi and r["ph"] <= lo:
                fade.append(r["cover"])
        yr = {yy: rec([(-r["cover"] if r["ph"] >= hi else r["cover"]) for r in S if r["season"] == yy and
                       ((r["ph"] >= hi and r["pa"] <= lo) or (r["pa"] >= hi and r["ph"] <= lo))]) for yy in args.years}
        print(f"  ATS {hi:.0%}+ team vs {lo:.0%}- team: fading the hot team {rec(fade)}  ["
              + ", ".join(f"{k} {v}" for k, v in yr.items()) + "]")

    print(f"\nTOTALS ({len(T)} games): average of both teams' over %")
    x = np.array([r["over_pct"] for r in T])
    b, se = slope(x, np.array([r["shade"] for r in T]))
    print(f"  shading: market total vs model total {b / 10:+.2f} pts per 10 points of over % (SE {se / 10:.2f})")
    b, se = slope(x, np.array([r["over"] for r in T]))
    print(f"  result:  actual vs market total {b / 10:+.2f} pts per 10 points of over % (SE {se / 10:.2f})")
    for thr in (0.65, 0.7, 0.75):
        ov = [r["over"] for r in T if min(r["oh"], r["oa"]) >= thr]
        un = [-r["over"] for r in T if max(r["oh"], r["oa"]) <= 1 - thr]
        yo = {yy: rec([r["over"] for r in T if r["season"] == yy and min(r["oh"], r["oa"]) >= thr]) for yy in args.years}
        yu = {yy: rec([-r["over"] for r in T if r["season"] == yy and max(r["oh"], r["oa"]) <= 1 - thr]) for yy in args.years}
        print(f"  both teams over {thr:.0%}+: over {rec(ov)}  [" + ", ".join(f"{k} {v}" for k, v in yo.items()) + "]")
        print(f"  both teams under {thr:.0%}+: under {rec(un)}  [" + ", ".join(f"{k} {v}" for k, v in yu.items()) + "]")


if __name__ == "__main__":
    main()
