"""Walk-forward test of special teams ratings (cfb_stats.special_teams) on the saved spread and
totals backtests in data/<year>/. Uses the drive data cached by game_script_backtest.py (about
17 API calls per season if not cached). Results are in BACKTEST.md.

  python scripts/special_teams_backtest.py --years 2024 2025
"""
import argparse
import csv
import math
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from cfb_stats import special_teams as st  # noqa: E402
from cfb_stats.api import CFBDClient  # noqa: E402
from cfb_stats.collect import write_csv  # noqa: E402


def load_drives(client, year):
    games = client.games(year, "regular")
    weeks = sorted({g["week"] for g in games if g.get("completed")})
    return {w: client.drives(year, w, "regular") for w in weeks}


def before(drives, week):
    return [d for w, ds in drives.items() if w < week for d in ds]


def slope(x, y):
    b = float(x @ y / (x @ x))
    return b, math.sqrt(((y - b * x) ** 2).mean() / (x @ x))


def ats(act, proj, line, t=3):
    """Home line convention: home covers when act + line > 0; model edge = proj + line."""
    e, d = proj + line, act + line
    k = (np.abs(e) >= t) & (d != 0)
    w = int(((e > 0) == (d > 0))[k].sum())
    return w, int(k.sum()) - w


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--cache", default=".cfbd_cache")
    p.add_argument("--years", type=int, nargs="+", default=[2024, 2025])
    p.add_argument("--priors", type=float, nargs="+", default=[2, 4, 8, 16])
    args = p.parse_args()
    client = CFBDClient(cache_dir=args.cache)
    years = args.years
    drives = {y: load_drives(client, y) for y in years}

    full = {y: st.ratings([d for ds in drives[y].values() for d in ds], prior=0) for y in years}
    print("year-to-year correlation of full-season ratings (teams with 10+ games both years):")
    for a, b in zip(years, years[1:]):
        common = [t for t in full[a] if t in full[b] and full[a][t]["games"] >= 10 and full[b][t]["games"] >= 10]
        print("  " + "  ".join(f"{k} {np.corrcoef([full[a][t][k] for t in common], [full[b][t][k] for t in common])[0, 1]:+.2f}"
                               for k in ("fg", "punt", "kick", "total")) + f"  ({len(common)} teams)")
    print("within-season split (weeks 1-7 vs 8+), average over seasons:")
    cs = {k: [] for k in ("fg", "punt", "kick", "total")}
    for y in years:
        a = st.ratings([d for w, ds in drives[y].items() if w <= 7 for d in ds], prior=0)
        b = st.ratings([d for w, ds in drives[y].items() if w > 7 for d in ds], prior=0)
        common = [t for t in a if t in b and a[t]["games"] >= 5 and b[t]["games"] >= 4]
        for k in cs:
            cs[k].append(np.corrcoef([a[t][k] for t in common], [b[t][k] for t in common])[0, 1])
    print("  " + "  ".join(f"{k} {np.mean(v):+.2f}" for k, v in cs.items()))

    summary = []
    for prior in args.priors:
        spr, tot = [], []
        for y in years:
            sp = {int(r["game_id"]): r for r in csv.DictReader(open(f"data/{y}/spreads_backtest_games.csv"))}
            cache = {}
            for r in csv.DictReader(open(f"data/{y}/totals_backtest_games.csv")):
                w = int(r["week"])
                if w not in cache:
                    cache[w] = st.ratings(before(drives[y], w), prior=prior)
                h, a = cache[w].get(r["home"], {}), cache[w].get(r["away"], {})
                comp = {k: h.get(k, 0.0) - a.get(k, 0.0) for k in ("fg", "punt", "kick", "total")}
                tot.append({"season": y, "act": float(r["actual_total"]), "proj": float(r["proj_total"]),
                            "line": float(r["market_total"]), "raw": h.get("fg", 0.0) + a.get("fg", 0.0)})
                s = sp.get(int(r["game_id"]))
                if s and s["market_spread"] and s["actual_margin"]:
                    spr.append({"season": y, "act": float(s["actual_margin"]), "proj": float(s["proj_margin"]),
                                "line": float(s["market_spread"]), "p4": s["p4_game"] == "True", **comp})
        act = np.array([r["act"] for r in spr]); proj = np.array([r["proj"] for r in spr])
        line = np.array([r["line"] for r in spr]); season = np.array([r["season"] for r in spr])
        p4 = np.array([r["p4"] for r in spr])
        print(f"\nprior {prior:g} games: {len(spr)} spread games")
        for k in ("fg", "punt", "kick", "total"):
            x = np.array([r[k] for r in spr])
            b1, s1 = slope(x, act - proj); b2, s2 = slope(x, act + line)
            print(f"  margin: {k:<5} sd {x.std():.2f}  slope vs model {b1:+.2f} ({s1:.2f})  vs market {b2:+.2f} ({s2:.2f})")
        x = np.array([r["total"] for r in spr])
        for s in years:
            tr, te = season != s, season == s
            k = max(float(x[tr] @ (act - proj)[tr] / (x[tr] @ x[tr])), 0.0)
            adj = proj + k * x
            rmse = lambda pr: math.sqrt(((act - pr)[te] ** 2).mean())  # noqa: E731
            row = {"prior_games": prior, "season": s, "scale": round(k, 2),
                   "margin_rmse_base": round(rmse(proj), 2), "margin_rmse_adj": round(rmse(adj), 2)}
            for name, pr in (("base", proj), ("adj", adj)):
                for lab, m in (("all", te), ("p4", te & p4)):
                    w, l = ats(act[m], pr[m], line[m])
                    row[f"ats3_{lab}_{name}"] = f"{w}-{l} ({w / (w + l):.1%})"
            summary.append(row)
            print("  " + "  ".join(f"{k} {v}" for k, v in row.items()))
        xt = np.array([r["raw"] for r in tot]); at = np.array([r["act"] for r in tot])
        b1, s1 = slope(xt, at - np.array([r["proj"] for r in tot]))
        b2, s2 = slope(xt, at - np.array([r["line"] for r in tot]))
        print(f"  totals: kickers (fg home + away) sd {xt.std():.2f}  slope vs model {b1:+.2f} ({s1:.2f})  vs market {b2:+.2f} ({s2:.2f})")
    write_csv("data/special_teams_backtest.csv", summary)


if __name__ == "__main__":
    main()
