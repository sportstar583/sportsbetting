"""Penalties test: officiating crews by conference (and whose crew works non-conference games),
team discipline, and whether expected penalties help totals or spreads. Uses the saved backtest
games in data/<year>/. Downloads /games/teams once per week (about 16 calls per season, cached).

  python scripts/penalties_backtest.py --years 2024 2025
"""
import argparse
import csv
import math
import os
import sys
from collections import defaultdict

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from cfb_stats import penalties as pen  # noqa: E402
from cfb_stats.api import CFBDClient  # noqa: E402
from cfb_stats.collect import write_csv  # noqa: E402


def slope(x, y):
    b = float(x @ y / (x @ x))
    return b, math.sqrt(((y - b * x) ** 2).mean() / (x @ x))


def corr(a, b):
    return float(np.corrcoef(a, b)[0, 1])


def load(client, year):
    games = [g for g in client.games(year, "regular") if g.get("completed")]
    weeks = sorted({g["week"] for g in games})
    box = {w: pen.parse(client.get("/games/teams", year=year, week=w, seasonType="regular")) for w in weeks}
    return games, box


def crew_test(games, pens):
    """Non-conference games: does a team's flag count move with the home or the visiting crew?"""
    conf_flags, team_conf = defaultdict(list), defaultdict(list)
    for g in games:
        p = pens.get(g["id"])
        if not p or not g.get("conferenceGame"):
            continue
        for t in (g["homeTeam"], g["awayTeam"]):
            if t in p:
                conf_flags[g["homeConference"]].append(p[t][0])
                team_conf[t].append(p[t][0])
    S = {c: np.mean(v) for c, v in conf_flags.items() if len(v) >= 40}
    rate = {t: np.mean(v) for t, v in team_conf.items() if len(v) >= 3}
    out = {"home_team": [], "away_team": []}
    for g in games:
        p = pens.get(g["id"])
        hc, ac = g.get("homeConference"), g.get("awayConference")
        if not p or g.get("conferenceGame") or g.get("neutralSite") or hc not in S or ac not in S or hc == ac:
            continue
        for t, side in ((g["homeTeam"], "home_team"), (g["awayTeam"], "away_team")):
            if t in p and t in rate:
                own = hc if side == "home_team" else ac
                other = ac if side == "home_team" else hc
                out[side].append((S[other] - S[own], p[t][0] - rate[t]))
    return S, {k: np.array(v) for k, v in out.items()}


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--cache", default=".cfbd_cache")
    p.add_argument("--years", type=int, nargs="+", default=[2024, 2025])
    args = p.parse_args()
    client = CFBDClient(cache_dir=args.cache)
    years = args.years
    data = {y: load(client, y) for y in years}

    # 1. Whose crew works non-conference games?
    print("1. Non-conference games (true road games between FBS conferences): flags vs the team's own")
    print("   conference-game rate, against the other conference's crew strictness minus its own")
    print("   (slope ~1 = that team saw the other conference's crew):")
    for y in years:
        games, box = data[y]
        pens = {k: v for wk in box.values() for k, v in wk.items()}
        S, res = crew_test(games, pens)
        for side, arr in res.items():
            b, se = slope(arr[:, 0] - arr[:, 0].mean(), arr[:, 1] - arr[:, 1].mean())
            print(f"   {y} {side:<10} {len(arr):>4} team-games  slope {b:+.2f} ({se:.2f})")

    # 2. Which assumption predicts flag counts better (fit on one season, test on the other)?
    print("\n2. Predicting each team's flags in non-conference games, fit on even weeks, tested on odd weeks (RMSE):")
    obs = {}
    for crew in ("home", "away"):
        for y in years:
            games, box = data[y]
            pens = {k: v for wk in box.values() for k, v in wk.items()}
            obs[(crew, y)] = pen.observations(games, pens, crew)
    for crew in ("home", "away"):
        errs = []
        for y in years:
            # Within a season (teams change between seasons): fit on even weeks, test on odd weeks.
            games = {g["id"]: g for g in data[y][0]}
            tr = [r for r in obs[(crew, y)] if games[r["game"]]["week"] % 2 == 0]
            te = [r for r in obs[(crew, y)] if games[r["game"]]["week"] % 2 == 1
                  and not games[r["game"]].get("conferenceGame")]
            m = pen.fit(tr)
            errs += [r["flags"] - pen.expected(m, r["team"], r["opp"], r["home"], r["crew"]) for r in te]
        print(f"   crew = {crew} team's conference: non-conference RMSE {math.sqrt(np.mean(np.square(errs))):.3f} "
              f"({len(errs)} team-games)")

    # 3. Crew and team tendencies, by season, and whether they repeat.
    fits = {y: pen.fit(obs[("home", y)]) for y in years}
    yfits = {y: pen.fit(obs[("home", y)], "yards") for y in years}
    fbs_conf = {g.get("homeConference") for y in years for g in data[y][0] if g.get("homeClassification") == "fbs"}
    crews = sorted(c for c in fbs_conf if c and all(c in fits[y]["crew"] for y in years))
    print("\n3. Crew effect by conference, flags per team-game (both teams: x2), yards per team-game:")
    rows = []
    for c in sorted(crews, key=lambda c: -np.mean([fits[y]["crew"][c] for y in years])):
        row = {"conference": c}
        for y in years:
            row[f"flags_{y}"] = round(fits[y]["crew"][c], 2)
            row[f"yards_{y}"] = round(yfits[y]["crew"][c], 1)
        rows.append(row)
        print("   " + "  ".join(f"{k} {v}" for k, v in row.items()))
    write_csv("data/penalties_crews.csv", rows)
    for a, b in zip(years, years[1:]):
        print(f"   crew effects {a}->{b}: correlation flags {corr([fits[a]['crew'][c] for c in crews], [fits[b]['crew'][c] for c in crews]):+.2f}"
              f"  yards {corr([yfits[a]['crew'][c] for c in crews], [yfits[b]['crew'][c] for c in crews]):+.2f}")
        common = [t for t in fits[a]["commit"] if t in fits[b]["commit"]]
        for k in ("commit", "draw"):
            print(f"   team {k:<6} {a}->{b}: flags {corr([fits[a][k][t] for t in common], [fits[b][k][t] for t in common]):+.2f}"
                  f"  yards {corr([yfits[a][k][t] for t in common], [yfits[b][k][t] for t in common]):+.2f}")

    # 4. Do expected penalties help totals or spreads? Walk-forward, ratings from earlier weeks only.
    T, M = [], []
    for y in years:
        games, box = data[y]
        by_id = {g["id"]: g for g in games}
        sp = {int(r["game_id"]): r for r in csv.DictReader(open(f"data/{y}/spreads_backtest_games.csv"))}
        cache = {}
        for r in csv.DictReader(open(f"data/{y}/totals_backtest_games.csv")):
            w, gid = int(r["week"]), int(r["game_id"])
            g = by_id.get(gid)
            if g is None:
                continue
            if w not in cache:
                pens = {k: v for wk, b in box.items() if wk < w for k, v in b.items()}
                prior = [x for x in obs[("home", y)] if x["game"] in pens]
                cache[w] = (pen.fit(prior), pen.fit(prior, "yards"))
            mf, my = cache[w]
            h = 0 if g.get("neutralSite") else 1
            crew = pen.crew_of(g)
            fh = pen.expected(mf, g["homeTeam"], g["awayTeam"], h, crew)
            fa = pen.expected(mf, g["awayTeam"], g["homeTeam"], -h, crew)
            yh = pen.expected(my, g["homeTeam"], g["awayTeam"], h, crew)
            ya = pen.expected(my, g["awayTeam"], g["homeTeam"], -h, crew)
            T.append((fh + fa, 2 * mf["crew"].get(crew, 0.0), float(r["actual_total"]) - float(r["proj_total"]),
                      float(r["actual_total"]) - float(r["market_total"])))
            s = sp.get(gid)
            if s and s["market_spread"] and s["actual_margin"]:
                M.append((ya - yh, float(s["actual_margin"]) - float(s["proj_margin"]),
                          float(s["actual_margin"]) + float(s["market_spread"])))
    T, M = np.array(T), np.array(M)
    print(f"\n4. Walk-forward, {len(T)} games (slope per expected flag or yard; SE in parentheses):")
    for i, lab in ((0, "expected flags, both teams"), (1, "crew only (x2 teams)")):
        x = T[:, i] - T[:, i].mean()
        b1, s1 = slope(x, T[:, 2]); b2, s2 = slope(x, T[:, 3])
        print(f"   totals on {lab:<27} sd {x.std():.2f}  vs model {b1:+.2f} ({s1:.2f}) pts/flag  vs market {b2:+.2f} ({s2:.2f})")
    x = M[:, 0]
    b1, s1 = slope(x, M[:, 1]); b2, s2 = slope(x, M[:, 2])
    print(f"   margin on penalty yards (away - home)  sd {x.std():.1f}  vs model {b1:+.3f} ({s1:.3f}) pts/yd  "
          f"vs market {b2:+.3f} ({s2:.3f})")


if __name__ == "__main__":
    main()
