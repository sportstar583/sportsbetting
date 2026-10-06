"""Leave-one-season-out test of special teams and 4th-down aggressiveness as adjustments to the
totals model, using the saved backtest games in data/<year>/. Results are in BACKTEST.md.

  CFBD_CACHE=.cfbd_cache python scripts/special_teams_backtest.py

Every team rating uses only games from earlier weeks (walk-forward), shrunk toward league average:
- Field goals: makes minus expected makes (league rate by kick distance) per attempt, times the
  team's FG attempts per game, times 3 = points per game the kicker adds over an average one.
- Net punting: opponent's next drive start minus where an average punt from that spot would
  leave them (yards per punt, coverage and blocks included), times punts per game.
- 4th down: share of 4th-down decisions where the team went for it
  (attempts / (attempts + punts + FG attempts)).
API cost: per season, 1 call for games, 1 for drives, 1 per FBS conference for team box scores.
"""
import csv, math, os
from collections import defaultdict
import numpy as np
from cfb_stats.api import CFBDClient
from cfb_stats.totals import weekly_card

c = CFBDClient(cache_dir=os.environ.get("CFBD_CACHE"))  # run from the repo root
FG_RESULTS = {"FG": 1, "MISSED FG": 0, "MISSED FG TD": 0}
FG_PRIOR, PUNT_PRIOR, GO_PRIOR, VOL_PRIOR = 15, 25, 30, 3  # attempts / punts / decisions / games


def bin_rate(pairs, width):
    """League average of y by x bin: {bin: mean}."""
    acc = defaultdict(lambda: [0.0, 0])
    for x, y in pairs:
        acc[x // width][0] += y
        acc[x // width][1] += 1
    return {b: s / n for b, (s, n) in acc.items() if n >= 20}


def lookup(table, x, width):
    b = x // width
    while b not in table and b > 0:
        b -= 1
    return table.get(b, sum(table.values()) / len(table))


def season_events(y):
    """Per (team, week) special-teams events for season y."""
    games = c.games(y, "regular")
    week_of = {g["id"]: g["week"] for g in games}
    drives = [d for d in c.get("/drives", year=y, seasonType="regular") if d.get("gameId") in week_of]
    by_game = defaultdict(list)
    for d in drives:
        by_game[d["gameId"]].append(d)
    kicks, punts = [], []  # (team, week, distance or spot, made or net yards)
    for gid, ds in by_game.items():
        ds.sort(key=lambda d: d["driveNumber"])
        for i, d in enumerate(ds):
            res, ytg = d.get("driveResult"), d.get("endYardsToGoal")
            if ytg is None:
                continue
            if res in FG_RESULTS:
                kicks.append((d["offense"], week_of[gid], ytg + 17, FG_RESULTS[res]))
            elif res == "PUNT" and i + 1 < len(ds):
                nxt = ds[i + 1]
                if nxt["offense"] == d["defense"] and nxt.get("startYardsToGoal") is not None \
                        and nxt["startPeriod"] == d["endPeriod"]:
                    punts.append((d["offense"], week_of[gid], ytg, ytg - (100 - nxt["startYardsToGoal"])))
    confs = sorted({g[f"{s}Conference"] for g in games for s in ("home", "away")
                    if g.get(f"{s}Classification") == "fbs" and g.get(f"{s}Conference")})
    fourth, seen = [], set()  # (team, week, attempts)
    for conf in confs:
        for g in c.get("/games/teams", year=y, seasonType="regular", conference=conf):
            if g["id"] in seen or g["id"] not in week_of:
                continue
            seen.add(g["id"])
            for t in g["teams"]:
                st = {s["category"]: s["stat"] for s in t["stats"]}
                if "-" in st.get("fourthDownEff", ""):
                    fourth.append((t["team"], week_of[g["id"]], int(st["fourthDownEff"].split("-")[1])))
    played = defaultdict(set)
    for g in games:
        if g.get("homePoints") is not None:
            played[g["homeTeam"]].add(g["week"])
            played[g["awayTeam"]].add(g["week"])
    return kicks, punts, fourth, played


def ratings_before(week, kicks, punts, fourth, played, fg_exp, net_exp):
    """Each team's FG points/game over average, punt yards/game over average and go rate."""
    k = defaultdict(lambda: [0.0, 0])
    for t, w, dist, made in kicks:
        if w < week:
            k[t][0] += made - lookup(fg_exp, dist, 5)
            k[t][1] += 1
    p = defaultdict(lambda: [0.0, 0])
    for t, w, spot, net in punts:
        if w < week:
            p[t][0] += net - lookup(net_exp, spot, 10)
            p[t][1] += 1
    att = defaultdict(int)
    for t, w, a in fourth:
        if w < week:
            att[t] += a
    g = {t: sum(x < week for x in ws) for t, ws in played.items()}
    lg_k = sum(v[1] for v in k.values()) / max(sum(g.values()), 1)
    lg_p = sum(v[1] for v in p.values()) / max(sum(g.values()), 1)
    dec = {t: att[t] + p[t][1] + k[t][1] for t in g}
    lg_go = sum(att.values()) / max(sum(dec.values()), 1)
    out = {}
    for t, n in g.items():
        fg_pg = (k[t][1] + lg_k * VOL_PRIOR) / (n + VOL_PRIOR)
        punt_pg = (p[t][1] + lg_p * VOL_PRIOR) / (n + VOL_PRIOR)
        out[t] = {
            "fg": 3 * fg_pg * k[t][0] / (k[t][1] + FG_PRIOR),
            "punt": punt_pg * p[t][0] / (p[t][1] + PUNT_PRIOR),
            "go": (att[t] + lg_go * GO_PRIOR) / (dec[t] + GO_PRIOR) - lg_go,
        }
    return out


rows = []
for y in (2023, 2024, 2025):
    kicks, punts, fourth, played = season_events(y)
    fg_exp = bin_rate([(d, m) for _, _, d, m in kicks], 5)
    net_exp = bin_rate([(s, n) for _, _, s, n in punts], 10)
    print(f"{y}: {len(kicks)} FG attempts ({sum(m for *_, m in kicks) / len(kicks):.1%} made), "
          f"{len(punts)} punts (avg net {sum(n for *_, n in punts) / len(punts):.1f}), "
          f"{sum(a for *_, a in fourth)} 4th-down attempts")
    cache = {}
    for r in csv.DictReader(open(f"data/{y}/totals_backtest_games.csv")):
        w = int(r["week"])
        if w not in cache:
            cache[w] = ratings_before(w, kicks, punts, fourth, played, fg_exp, net_exp)
        rt, zero = cache[w], {"fg": 0.0, "punt": 0.0, "go": 0.0}
        a, h = rt.get(r["away"], zero), rt.get(r["home"], zero)
        rows.append({
            "season": y, "week": w, "p4": r["p4_game"] == "True",
            "act": float(r["actual_total"]), "proj": float(r["proj_total"]), "mkt": float(r["market_total"]),
            "fg": a["fg"] + h["fg"], "punt": (a["punt"] + h["punt"]) / 10, "go": (a["go"] + h["go"]) * 10,
        })

act = np.array([r["act"] for r in rows]); proj = np.array([r["proj"] for r in rows]); mkt = np.array([r["mkt"] for r in rows])
season = np.array([r["season"] for r in rows])
FEATS = ["fg", "punt", "go"]
F = np.array([[r[f] for f in FEATS] for r in rows], dtype=float)
print(f"\n{len(rows)} games. Per game, sum of both teams: FG pts over average sd {F[:,0].std():.2f}, "
      f"punt net yds over average (x10) sd {F[:,1].std():.2f}, 4th-down go rate over average (x10) sd {F[:,2].std():.2f}")
print("(units: fg = points/game, punt = 10 yards/game, go = 10 percentage points)")
for target, lab in ((act - proj, "actual - model"), (act - mkt, "actual - market")):
    A = np.column_stack([np.ones(len(rows)), F]); b, *_ = np.linalg.lstsq(A, target, rcond=None)
    se = np.sqrt(np.diag(np.linalg.inv(A.T @ A)) * (target - A @ b).var())
    print(f"{lab:<16}" + "  ".join(f"{f} {b[i+1]:+.2f}({se[i+1]:.2f})" for i, f in enumerate(FEATS)))


def evaluate(cols, label):
    tot = defaultdict(lambda: [0, 0])
    rm = []
    for s in (2023, 2024, 2025):
        tr, te = season != s, season == s
        X = F[:, cols]
        A = np.column_stack([np.ones(tr.sum()), X[tr]]); b, *_ = np.linalg.lstsq(A, (act - proj)[tr], rcond=None)
        adj = proj + X @ b[1:]
        rm.append((math.sqrt(((act - proj)[te] ** 2).mean()), math.sqrt(((act - adj)[te] ** 2).mean())))
        for name, p in (("base", proj), ("adj", adj)):
            e = p - mkt; d = act - mkt
            k = te & (np.abs(e) >= 3) & (d != 0)
            tot[f"edge3 {name}"][0] += int(((e > 0) == (d > 0))[k].sum()); tot[f"edge3 {name}"][1] += int(k.sum())
            rs = [{"week": (rows[i]["season"], rows[i]["week"]), "edge": e[i], "p4_game": rows[i]["p4"], "enough_data": True,
                   "actual_total": act[i], "market_total": mkt[i]} for i in np.where(te)[0]]
            for r, side in weekly_card(rs, 3, p4_only=True):
                dd = r["actual_total"] - r["market_total"]
                if dd:
                    tot[f"card {name}"][0] += (dd > 0) == (side == "OVER"); tot[f"card {name}"][1] += 1
    print(f"{label:<22} RMSE " + " ".join(f"{a:.2f}->{b:.2f}" for a, b in rm) + " | " +
          " | ".join(f"{k} {w}-{n-w} {w/n:.1%}" for k, (w, n) in tot.items()))


print("\nleave-one-season-out:")
evaluate([0], "FG kicking")
evaluate([1], "net punting")
evaluate([2], "4th-down go rate")
evaluate([0, 1, 2], "all three")
