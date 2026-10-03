"""Leave-one-season-out test of team-specific home field (spreads) and home-stadium scoring
effects (totals), using the saved backtest games in data/<year>/. Results are in BACKTEST.md.

  python scripts/home_field_backtest.py          # test
  python scripts/home_field_backtest.py --save   # write data/priors/team_home_field.csv from all seasons
"""
import csv
import math
from collections import defaultdict

import numpy as np

from cfb_stats.totals import weekly_card

YEARS = (2023, 2024, 2025)
PRIOR_GAMES = 12  # shrink team effects toward zero by this many games


def load():
    tot, spr = [], []
    for y in YEARS:
        for r in csv.DictReader(open(f"data/{y}/totals_backtest_games.csv")):
            if r["neutral"] == "True":
                continue
            tot.append({"season": y, "week": (y, int(r["week"])), "home": r["home"], "p4_game": r["p4_game"] == "True",
                        "enough_data": True, "act": float(r["actual_total"]), "proj": float(r["proj_total"]),
                        "mkt": float(r["market_total"])})
        for r in csv.DictReader(open(f"data/{y}/spreads_backtest_games.csv")):
            if r["neutral"] == "True" or not r["market_spread"] or not r["actual_margin"]:
                continue
            spr.append({"season": y, "home": r["home"], "away": r["away"], "act": float(r["actual_margin"]),
                        "proj": float(r["proj_margin"]), "line": float(r["market_spread"])})
    return tot, spr


def effects(rows, key):
    s, n = defaultdict(float), defaultdict(int)
    for r in rows:
        s[r["home"]] += r["act"] - r[key]
        n[r["home"]] += 1
    return {t: s[t] / (n[t] + PRIOR_GAMES) for t in s}


def home_minus_road(rows):
    """Team home field beyond the league's: (home residual - road residual) / 2, shrunk.

    Residuals are from the team's own perspective, so a team the model simply under-rates
    (positive residual home and away) nets out to zero."""
    h, a = defaultdict(list), defaultdict(list)
    for r in rows:
        res = r["act"] - r["proj"]
        h[r["home"]].append(res)
        a[r["away"]].append(-res)
    out = {}
    for t in set(h) & set(a):
        nh, na = len(h[t]), len(a[t])
        out[t] = (sum(h[t]) / (nh + PRIOR_GAMES) - sum(a[t]) / (na + PRIOR_GAMES)) / 2
    return out


def main():
    tot, spr = load()
    print("SPREADS: team home field (actual - model margin in home games, from other seasons)")
    w0 = w1 = n0 = n1 = 0
    for y in YEARS:
        train = [r for r in spr if r["season"] != y]
        test = [r for r in spr if r["season"] == y]
        eff = effects(train, "proj")
        rm0 = math.sqrt(np.mean([(r["act"] - r["proj"]) ** 2 for r in test]))
        rm1 = math.sqrt(np.mean([(r["act"] - r["proj"] - eff.get(r["home"], 0)) ** 2 for r in test]))
        # does last-seasons' team home effect predict this season's home residual?
        x = np.array([eff.get(r["home"], 0) for r in test]); res = np.array([r["act"] - r["proj"] for r in test])
        for r in test:
            for adj in (0, 1):
                p = r["proj"] + (eff.get(r["home"], 0) if adj else 0)
                e = p + r["line"]; d = r["act"] + r["line"]
                if abs(e) >= 3 and d != 0:
                    ok = (e > 0) == (d > 0)
                    if adj: w1 += ok; n1 += 1
                    else: w0 += ok; n0 += 1
        print(f"  {y}: margin RMSE {rm0:.2f} -> {rm1:.2f}; corr(team effect, home residual) {np.corrcoef(x, res)[0,1]:+.3f}")
    print(f"  ATS edge>=3: {w0}-{n0-w0} {w0/n0:.1%} -> {w1}-{n1-w1} {w1/n1:.1%}")

    print("SPREADS: true team home field = (home residual - road residual) / 2, from other seasons")
    w0 = w1 = n0 = n1 = 0
    for y in YEARS:
        train = [r for r in spr if r["season"] != y]
        test = [r for r in spr if r["season"] == y]
        hfa = home_minus_road(train)
        adj = lambda r: hfa.get(r["home"], 0)  # noqa: E731
        rm0 = math.sqrt(np.mean([(r["act"] - r["proj"]) ** 2 for r in test]))
        rm1 = math.sqrt(np.mean([(r["act"] - r["proj"] - adj(r)) ** 2 for r in test]))
        x = np.array([adj(r) for r in test]); res = np.array([r["act"] - r["proj"] for r in test])
        for r in test:
            for a in (0, 1):
                e = r["proj"] + a * adj(r) + r["line"]; d = r["act"] + r["line"]
                if abs(e) >= 3 and d != 0:
                    ok = (e > 0) == (d > 0)
                    if a: w1 += ok; n1 += 1
                    else: w0 += ok; n0 += 1
        top = sorted(hfa.items(), key=lambda kv: -kv[1])[:4]
        print(f"  {y}: margin RMSE {rm0:.2f} -> {rm1:.2f}; corr {np.corrcoef(x, res)[0,1]:+.3f}; "
              f"biggest home edges (pts beyond league): {', '.join(f'{t} {v:+.1f}' for t, v in top)}")
    print(f"  ATS edge>=3: {w0}-{n0-w0} {w0/n0:.1%} -> {w1}-{n1-w1} {w1/n1:.1%}")

    print("TOTALS: home-stadium scoring effect (actual - model total in home games, from other seasons)")
    rec = defaultdict(lambda: [0, 0])
    for y in YEARS:
        train = [r for r in tot if r["season"] != y]
        test = [r for r in tot if r["season"] == y]
        eff = effects(train, "proj")
        mk = effects(train, "mkt")
        rm0 = math.sqrt(np.mean([(r["act"] - r["proj"]) ** 2 for r in test]))
        rm1 = math.sqrt(np.mean([(r["act"] - r["proj"] - eff.get(r["home"], 0)) ** 2 for r in test]))
        x = np.array([eff.get(r["home"], 0) for r in test]); res = np.array([r["act"] - r["proj"] for r in test])
        xm = np.array([mk.get(r["home"], 0) for r in test]); resm = np.array([r["act"] - r["mkt"] for r in test])
        print(f"  {y}: RMSE {rm0:.2f} -> {rm1:.2f}; corr(stadium effect, model residual) {np.corrcoef(x, res)[0,1]:+.3f}, "
              f"vs market residual {np.corrcoef(xm, resm)[0,1]:+.3f}")
        for name, shift in (("base", 0), ("stadium", 1)):
            rows = [dict(r, edge=r["proj"] + shift * eff.get(r["home"], 0) - r["mkt"]) for r in test]
            for r in rows:
                if abs(r["edge"]) >= 3 and r["act"] != r["mkt"]:
                    rec[f"edge3 {name}"][0] += (r["edge"] > 0) == (r["act"] > r["mkt"]); rec[f"edge3 {name}"][1] += 1
            for r, side in weekly_card(rows, 3, p4_only=True):
                if r["act"] != r["mkt"]:
                    rec[f"card {name}"][0] += (side == "OVER") == (r["act"] > r["mkt"]); rec[f"card {name}"][1] += 1
    for k, (w, n) in rec.items():
        print(f"  {k:<14} {w}-{n-w} {w/n:.1%}")


def save(path="data/priors/team_home_field.csv"):
    _, spr = load()
    hfa = home_minus_road(spr)
    games = defaultdict(lambda: [0, 0])
    for r in spr:
        games[r["home"]][0] += 1
        games[r["away"]][1] += 1
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["team", "home_edge_pts", "home_games", "road_games"])
        for t, v in sorted(hfa.items(), key=lambda kv: -kv[1]):
            w.writerow([t, round(v, 2), *games[t]])
    print(f"wrote {path} ({len(hfa)} teams)")


if __name__ == "__main__":
    import sys
    save() if "--save" in sys.argv else main()
