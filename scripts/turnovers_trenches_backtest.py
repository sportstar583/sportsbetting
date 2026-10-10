"""Turnover luck and line-of-scrimmage matchups, tested walk-forward on the saved backtests in
data/<year>/. Uses the /games/teams box scores cached by penalties_backtest.py (about 16 calls
per season if not cached).

  python scripts/turnovers_trenches_backtest.py --years 2024 2025
"""
import argparse
import csv
import math
import os
import sys
from collections import defaultdict

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from cfb_stats import boxscore as bx  # noqa: E402
from cfb_stats.api import CFBDClient  # noqa: E402
from cfb_stats.totals import weekly_card  # noqa: E402

PTS_PER_TO = 4.5  # rough value of a turnover, for display only


def slope(x, y):
    b = float(x @ y / (x @ x))
    return b, math.sqrt(((y - b * x) ** 2).mean() / (x @ x))


def load(client, year):
    games = [g for g in client.games(year, "regular") if g.get("completed")]
    weeks = sorted({g["week"] for g in games})
    box = {}
    for w in weeks:
        box.update(bx.parse(client.get("/games/teams", year=year, week=w, seasonType="regular")))
    return games, box


def int_share(games, box):
    """League share of passes defended (INT + PBU) that are intercepted."""
    i = d = 0
    for g in games:
        for s in (box.get(g["id"]) or {}).values():
            if s["passesIntercepted"] is not None and s["passesDeflected"] is not None:
                i += s["passesIntercepted"]
                d += s["passesDeflected"]
    return i / (i + d)


def luck_rows(games, box, r_int):
    """Per team-game turnover luck (turnovers, + = lucky) for offense and defense."""
    out = []
    for g in games:
        b = box.get(g["id"]) or {}
        teams = (g["homeTeam"], g["awayTeam"])
        if not all(t in b for t in teams):
            continue
        for t, o in (teams, teams[::-1]):
            s, os_ = b[t], b[o]
            off = de = 0.0
            ok = True
            if None not in (s["totalFumbles"], s["fumblesLost"], os_["totalFumbles"], s["fumblesRecovered"]):
                off += 0.5 * s["totalFumbles"] - s["fumblesLost"]
                de += s["fumblesRecovered"] - 0.5 * os_["totalFumbles"]
            else:
                ok = False
            if None not in (s["passesIntercepted"], s["passesDeflected"], s["interceptions"],
                            os_["passesIntercepted"], os_["passesDeflected"]):
                de += s["passesIntercepted"] - r_int * (s["passesIntercepted"] + s["passesDeflected"])
                off += r_int * (os_["passesIntercepted"] + os_["passesDeflected"]) - s["interceptions"]
            else:
                ok = False
            if ok:
                out.append({"game": g["id"], "week": g["week"], "team": t, "off": off, "def": de,
                            "to_margin": (os_["turnovers"] or 0) - (s["turnovers"] or 0)})
    return out


def trench_rows(games, box):
    """Per offense-game: sacks allowed / dropbacks, pressures / dropbacks, run stuffs / rushes."""
    out = []
    for g in games:
        b = box.get(g["id"]) or {}
        for t, o, h in ((g["homeTeam"], g["awayTeam"], 1), (g["awayTeam"], g["homeTeam"], -1)):
            if t not in b or o not in b:
                continue
            s, d = b[t], b[o]
            if None in (s["pass_att"], d["sacks"], d["tacklesForLoss"], s["rushingAttempts"]):
                continue
            h = 0 if g.get("neutralSite") else h
            drop = s["pass_att"] + d["sacks"]
            rush = max(s["rushingAttempts"] - d["sacks"], 1)  # college stats count sacks as runs
            out.append({"game": g["id"], "week": g["week"], "team": t, "opp": o, "home": h,
                        "sack": (d["sacks"], drop), "press": (d["sacks"] + (d["qbHurries"] or 0), drop),
                        "stuff": (max(d["tacklesForLoss"] - d["sacks"], 0), rush)})
    return out


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--cache", default=".cfbd_cache")
    p.add_argument("--years", type=int, nargs="+", default=[2024, 2025])
    args = p.parse_args()
    client = CFBDClient(cache_dir=args.cache)
    years = args.years
    data = {y: load(client, y) for y in years}

    # ------------------------------------------------------------- turnover luck
    print("TURNOVER LUCK (turnovers per game vs expected; fumbles: 50% recovery, INTs: league share of passes defended)")
    luck = {}
    for y in years:
        games, box = data[y]
        r = int_share(games, box)
        luck[y] = luck_rows(games, box, r)
        print(f"  {y}: INTs are {r:.0%} of passes defended; {len(luck[y])} team-games")
    # Does luck repeat? First half of season vs second half, per team.
    for kind in ("off", "def"):
        cs = []
        for y in years:
            a, b2 = defaultdict(list), defaultdict(list)
            for r in luck[y]:
                (a if r["week"] <= 7 else b2)[r["team"]].append(r[kind])
            common = [t for t in a if len(a[t]) >= 4 and len(b2.get(t, [])) >= 4]
            cs.append(np.corrcoef([np.mean(a[t]) for t in common], [np.mean(b2[t]) for t in common])[0, 1])
        print(f"  {kind} luck, weeks 1-7 vs 8+: correlation {np.mean(cs):+.2f} (0 = pure luck)")
    T, M, LW = [], [], []
    for y in years:
        games, box = data[y]
        by_id = {g["id"]: g for g in games}
        sp = {int(r["game_id"]): r for r in csv.DictReader(open(f"data/{y}/spreads_backtest_games.csv"))}
        team_games = defaultdict(list)
        for r in luck[y]:
            team_games[r["team"]].append(r)
        for r in csv.DictReader(open(f"data/{y}/totals_backtest_games.csv")):
            w, gid = int(r["week"]), int(r["game_id"])
            if gid not in by_id:
                continue
            h, a = r["home"], r["away"]

            def to_date(t):
                rs = [x for x in team_games[t] if x["week"] < w]
                n = max(len(rs), 1)
                return (sum(x["off"] for x in rs) / n, sum(x["def"] for x in rs) / n,
                        max(rs, key=lambda x: x["week"])["to_margin"] if rs else 0)
            oh, dh, lh = to_date(h)
            oa, da, la = to_date(a)
            T.append((y, w, r["p4_game"] == "True", -(oh + oa) + (dh + da), float(r["actual_total"]),
                      float(r["proj_total"]), float(r["market_total"])))
            s = sp.get(gid)
            if s and s["market_spread"] and s["actual_margin"]:
                act, proj, line = float(s["actual_margin"]), float(s["proj_margin"]), float(s["market_spread"])
                M.append(((oa + da) - (oh + dh), act - proj, act + line))
                LW.append((lh, la, act + line))
    T = np.array(T, dtype=float); M = np.array(M)
    x = T[:, 3]
    b1, s1 = slope(x, T[:, 4] - T[:, 5]); b2, s2 = slope(x, T[:, 4] - T[:, 6])
    print(f"  totals: luck that flatters defenses minus offenses (sd {x.std():.2f} TO/game): "
          f"vs model {b1:+.2f} ({s1:.2f}) pts per TO, vs market {b2:+.2f} ({s2:.2f})")
    x = M[:, 0]
    b1, s1 = slope(x, M[:, 1]); b2, s2 = slope(x, M[:, 2])
    print(f"  margin: away minus home luck (sd {x.std():.2f}): vs model {b1:+.2f} ({s1:.2f}) pts per TO, "
          f"vs market {b2:+.2f} ({s2:.2f})")
    print("  ATS: fade a team coming off a big turnover win (last game margin +3 or more), opponent not:")
    for thr in (2, 3, 4):
        w = l = 0
        for lh, la, d in LW:
            if d == 0:
                continue
            if lh >= thr and la < thr:
                w += d < 0; l += d > 0
            elif la >= thr and lh < thr:
                w += d > 0; l += d < 0
        print(f"    last-game TO margin >= +{thr}: fading went {w}-{l} ({w / max(w + l, 1):.1%})")

    # ------------------------------------------------------------- trenches
    print("\nLINE OF SCRIMMAGE (opponent-adjusted rates; walk-forward)")
    tr = {y: trench_rows(*data[y]) for y in years}
    for k, lab in (("sack", "sack rate (pass pro vs pass rush)"), ("press", "pressure rate (sacks + hurries)"),
                   ("stuff", "run stuff rate (run blocking vs run stopping)")):
        cs = []
        for y in years:
            a = [dict(r, num=r[k][0], den=r[k][1]) for r in tr[y] if r["week"] <= 7]
            b2 = [dict(r, num=r[k][0], den=r[k][1]) for r in tr[y] if r["week"] > 7]
            fa, fb = bx.rate_fit(a), bx.rate_fit(b2)
            for side in ("off", "def"):
                common = [t for t in fa[side] if t in fb[side]]
                cs.append((side, np.corrcoef([fa[side][t] for t in common], [fb[side][t] for t in common])[0, 1]))
        print(f"  {lab}: weeks 1-7 vs 8+ correlation offense {np.mean([c for s, c in cs if s == 'off']):+.2f}, "
              f"defense {np.mean([c for s, c in cs if s == 'def']):+.2f}")
    FT, FM = [], []
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
                prior = [x for x in tr[y] if x["week"] < w]
                cache[w] = {k: bx.rate_fit([dict(x, num=x[k][0], den=x[k][1]) for x in prior])
                            for k in ("sack", "press", "stuff")}
            f = cache[w]
            h, a = g["homeTeam"], g["awayTeam"]
            feats = {}
            for k, m in f.items():
                # Deviation from league for each offense vs the other defense, and the interaction
                # (a weak line facing a strong front does worse than the two ratings add up to?).
                for side, (o, d) in (("h", (h, a)), ("a", (a, h))):
                    feats[f"{k}_{side}"] = m["off"].get(o, 0.0) + m["def"].get(d, 0.0)
                    feats[f"{k}_x_{side}"] = m["off"].get(o, 0.0) * m["def"].get(d, 0.0)
            FT.append((y, w, r["p4_game"] == "True", feats, float(r["actual_total"]), float(r["proj_total"]),
                       float(r["market_total"])))
            s = sp.get(gid)
            if s and s["market_spread"] and s["actual_margin"]:
                act, proj, line = float(s["actual_margin"]), float(s["proj_margin"]), float(s["market_spread"])
                FM.append((y, feats, act - proj, act + line, proj, line, act))
    for k, lab in (("sack", "sack rate"), ("press", "pressure rate"), ("stuff", "run stuff rate")):
        xt = np.array([f[f"{k}_h"] + f[f"{k}_a"] for _, _, _, f, *_ in FT])
        it = np.array([f[f"{k}_x_h"] + f[f"{k}_x_a"] for _, _, _, f, *_ in FT])
        rt = np.array([a - p for *_, a, p, m in FT]); rm = np.array([a - m for *_, a, p, m in FT])
        xm = np.array([f[f"{k}_a"] - f[f"{k}_h"] for _, f, *_ in FM])
        im = np.array([f[f"{k}_x_a"] - f[f"{k}_x_h"] for _, f, *_ in FM])
        mr = np.array([x[2] for x in FM]); mm = np.array([x[3] for x in FM])
        print(f"  {lab}:")
        for lab2, x, y1, y2, unit in (("totals, both offenses' expected rate", xt, rt, rm, "pts per 10 pct pts"),
                                      ("totals, weak line x strong front", it, rt, rm, "per 0.01"),
                                      ("margin, away minus home expected rate", xm, mr, mm, "pts per 10 pct pts"),
                                      ("margin, weak line x strong front", im, mr, mm, "per 0.01")):
            sc = 0.1 if "pct" in unit else 0.01
            b1, s1 = slope(x - x.mean(), y1); b2, s2 = slope(x - x.mean(), y2)
            print(f"    {lab2:<40} sd {x.std():.3f}  vs model {b1 * sc:+.2f} ({s1 * sc:.2f})  "
                  f"vs market {b2 * sc:+.2f} ({s2 * sc:.2f})  [{unit}]")


if __name__ == "__main__":
    main()
