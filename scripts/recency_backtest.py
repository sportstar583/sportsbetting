"""Recency bias: does the market (or the model) overreact to a team's last few games?

For each game in the saved backtests (data/<year>/, 2023-2025), each team's recent results vs the
closing line (ATS cover margin, and the game total vs the closing total) and vs the model are
compared with how it does in this game. A negative slope on recent covers means the market
overreacts (teams that just covered big are overpriced next time). No API calls beyond the
cached schedules.

  python scripts/recency_backtest.py
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


def slope(x, y):
    x = x - x.mean()
    b = float(x @ y / (x @ x))
    return b, math.sqrt(((y - y.mean() - b * x) ** 2).mean() / (x @ x))


def record(pairs):
    """pairs: (bet side wins if > 0 value) -> 'W-L (pct)'."""
    w = sum(v > 0 for v in pairs)
    lo = sum(v < 0 for v in pairs)
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
        order = defaultdict(list)
        for g in games:
            order[g["homeTeam"]].append(g["id"])
            order[g["awayTeam"]].append(g["id"])
        spreads = {int(r["game_id"]): r for r in csv.DictReader(open(f"data/{y}/spreads_backtest_games.csv"))
                   if r["market_spread"] and r["actual_margin"]}
        totals = {int(r["game_id"]): r for r in csv.DictReader(open(f"data/{y}/totals_backtest_games.csv"))}

        def team_view(gid, team):
            """(cover margin, margin vs model, total vs closing total) for team in game gid, or None."""
            s = spreads.get(gid)
            if s is None:
                return None
            sign = 1 if s["home"] == team else -1
            act, line, proj = float(s["actual_margin"]), float(s["market_spread"]), float(s["proj_margin"])
            t = totals.get(gid)
            over = float(t["actual_total"]) - float(t["market_total"]) if t else None
            return sign * (act + line), sign * (act - proj), over

        def recent(team, gid, k):
            ids = order[team]
            i = ids.index(gid)
            out = []
            for j in range(i - 1, -1, -1):
                v = team_view(ids[j], team)
                if v is None:
                    break  # only consecutive games with lines
                out.append(v)
                if len(out) == k:
                    break
            return out

        for gid, s in spreads.items():
            h, a = s["home"], s["away"]
            rh1, ra1 = recent(h, gid, 1), recent(a, gid, 1)
            rh3, ra3 = recent(h, gid, 3), recent(a, gid, 3)
            if not rh1 or not ra1:
                continue
            cover_h = float(s["actual_margin"]) + float(s["market_spread"])
            resid_h = float(s["actual_margin"]) - float(s["proj_margin"])
            S.append({"season": y, "cover": cover_h, "resid": resid_h,
                      "last_cover": (rh1[0][0], ra1[0][0]), "last_resid": (rh1[0][1], ra1[0][1]),
                      "avg3": (np.mean([v[0] for v in rh3]), np.mean([v[0] for v in ra3])) if len(rh3) == 3 and len(ra3) == 3 else None})
            t = totals.get(gid)
            if t and rh1[0][2] is not None and ra1[0][2] is not None:
                T.append({"season": y, "over": float(t["actual_total"]) - float(t["market_total"]),
                          "resid": float(t["actual_total"]) - float(t["proj_total"]),
                          "last_over": (rh1[0][2], ra1[0][2]),
                          "avg3": (np.mean([v[2] for v in rh3 if v[2] is not None]), np.mean([v[2] for v in ra3 if v[2] is not None]))
                          if len(rh3) == 3 and len(ra3) == 3 and all(v[2] is not None for v in rh3 + ra3) else None})

    print(f"SPREADS: {len(S)} games where both teams' last game had a line")
    cover = np.array([r["cover"] for r in S]); resid = np.array([r["resid"] for r in S])
    for key, lab in (("last_cover", "last game ATS cover margin"), ("last_resid", "last game margin vs model")):
        x = np.array([r[key][0] - r[key][1] for r in S])
        b1, s1 = slope(x, cover); b2, s2 = slope(x, resid)
        print(f"  {lab:<32} home minus away -> this game vs line {b1:+.3f} ({s1:.3f}) per pt, vs model {b2:+.3f} ({s2:.3f})")
    S3 = [r for r in S if r["avg3"] is not None]
    x = np.array([r["avg3"][0] - r["avg3"][1] for r in S3])
    b1, s1 = slope(x, np.array([r["cover"] for r in S3])); b2, s2 = slope(x, np.array([r["resid"] for r in S3]))
    print(f"  {'last 3 games average cover':<32} home minus away -> this game vs line {b1:+.3f} ({s1:.3f}) per pt, "
          f"vs model {b2:+.3f} ({s2:.3f})  ({len(S3)} games)")
    print("  Betting against a team that covered big last game (opponent didn't), and on one that missed big:")
    for thr in (10, 14, 21):
        fade = []
        for r in S:
            ch, ca = r["last_cover"]
            if ch >= thr and ca < thr:
                fade.append(-r["cover"])
            elif ca >= thr and ch < thr:
                fade.append(r["cover"])
        back = []
        for r in S:
            ch, ca = r["last_cover"]
            if ch <= -thr and ca > -thr:
                back.append(r["cover"])
            elif ca <= -thr and ch > -thr:
                back.append(-r["cover"])
        by_y = {y: record([-r["cover"] if r["last_cover"][0] >= thr > r["last_cover"][1] else r["cover"]
                           for r in S if r["season"] == y and ((r["last_cover"][0] >= thr > r["last_cover"][1])
                                                               or (r["last_cover"][1] >= thr > r["last_cover"][0]))])
                for y in args.years}
        by_y2 = {y: record([r["cover"] if r["last_cover"][0] <= -thr < r["last_cover"][1] else -r["cover"]
                            for r in S if r["season"] == y and ((r["last_cover"][0] <= -thr < r["last_cover"][1])
                                                                or (r["last_cover"][1] <= -thr < r["last_cover"][0]))])
                 for y in args.years}
        print(f"    covered by {thr}+: fade {record(fade)}  [" + ", ".join(f"{y} {v}" for y, v in by_y.items())
              + f"]\n    missed by {thr}+: back {record(back)}  [" + ", ".join(f"{y} {v}" for y, v in by_y2.items()) + "]")

    print(f"\nTOTALS: {len(T)} games")
    over = np.array([r["over"] for r in T]); tres = np.array([r["resid"] for r in T])
    x = np.array([sum(r["last_over"]) for r in T])
    b1, s1 = slope(x, over); b2, s2 = slope(x, tres)
    print(f"  both teams' last game total vs its closing total (sum) -> this game vs line {b1:+.3f} ({s1:.3f}), "
          f"vs model {b2:+.3f} ({s2:.3f})")
    T3 = [r for r in T if r["avg3"] is not None]
    x = np.array([sum(r["avg3"]) for r in T3])
    b1, s1 = slope(x, np.array([r["over"] for r in T3])); b2, s2 = slope(x, np.array([r["resid"] for r in T3]))
    print(f"  last 3 games (sum of both teams' averages) -> vs line {b1:+.3f} ({s1:.3f}), vs model {b2:+.3f} ({s2:.3f})")
    for thr in (7, 10, 14):
        under = [-r["over"] for r in T if min(r["last_over"]) >= thr]
        ov = [r["over"] for r in T if max(r["last_over"]) <= -thr]
        yo = {y: record([r["over"] for r in T if r["season"] == y and min(r["last_over"]) >= thr]) for y in args.years}
        yu = {y: record([-r["over"] for r in T if r["season"] == y and max(r["last_over"]) <= -thr]) for y in args.years}
        print(f"    both teams' last games went over by {thr}+: over again {record([-v for v in under])}  ["
              + ", ".join(f"{y} {v}" for y, v in yo.items()) + "]")
        print(f"    both teams' last games went under by {thr}+: under again {record([-v for v in ov])}  ["
              + ", ".join(f"{y} {v}" for y, v in yu.items()) + "]")


if __name__ == "__main__":
    main()
