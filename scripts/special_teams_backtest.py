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
- Coach 4th down: the head coach's go rate over his previous 3 seasons (at any school; saved
  coach list in data/priors/), blended with this season's. "Adjusted" removes the part explained
  by scoring margin, since teams that trail go for it more whatever the coach's habits.
API cost: per season, 1 call for games, 1 for drives, 1 per FBS conference for team box scores
(2022 too, for the 2023 coach history).
"""
import csv, math, os
from collections import defaultdict
import numpy as np
from cfb_stats.api import CFBDClient
from cfb_stats.priors import coaches
from cfb_stats.totals import weekly_card

c = CFBDClient(cache_dir=os.environ.get("CFBD_CACHE"))  # run from the repo root
FG_RESULTS = {"FG": 1, "MISSED FG": 0, "MISSED FG TD": 0}
# /games/teams matches conference abbreviations only ("B12"); games list full names ("Big 12").
CONF_ABBR = {x["name"]: x["abbreviation"] for x in c.get("/conferences") if x.get("abbreviation")}
FG_PRIOR, PUNT_PRIOR, GO_PRIOR, VOL_PRIOR = 15, 25, 30, 3  # attempts / punts / decisions / games
COACH_WEIGHT = 60  # decisions of this season's data that the coach's history counts as (~8 games)


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
        for g in c.get("/games/teams", year=y, seasonType="regular", conference=CONF_ABBR.get(conf, conf)):
            if g["id"] in seen or g["id"] not in week_of:
                continue
            seen.add(g["id"])
            for t in g["teams"]:
                st = {s["category"]: s["stat"] for s in t["stats"]}
                if "-" in st.get("fourthDownEff", ""):
                    fourth.append((t["team"], week_of[g["id"]], int(st["fourthDownEff"].split("-")[1])))
    played, margins = defaultdict(set), []  # margins: (team, week, points for - against)
    for g in games:
        if g.get("homePoints") is not None and g.get("awayPoints") is not None:
            played[g["homeTeam"]].add(g["week"])
            played[g["awayTeam"]].add(g["week"])
            d = g["homePoints"] - g["awayPoints"]
            margins += [(g["homeTeam"], g["week"], d), (g["awayTeam"], g["week"], -d)]
    return kicks, punts, fourth, played, margins


def go_counts(week, kicks, punts, fourth, margins):
    """{team: [4th-down attempts, decisions, total margin, games]} from games before `week`."""
    out = defaultdict(lambda: [0, 0, 0.0, 0])
    boxed = {(t, w) for t, w, _ in fourth}  # count punts and kicks only in games with a box score
    for t, w, *_ in kicks + punts:
        if w < week and (t, w) in boxed:
            out[t][1] += 1
    for t, w, a in fourth:
        if w < week:
            out[t][0] += a
            out[t][1] += a
    for t, w, d in margins:
        if w < week:
            out[t][2] += d
            out[t][3] += 1
    return out


def coach_history(seasons, coach_map, slope, lg_go):
    """{(coach, year): (raw go rate, margin-adjusted go rate, decisions)} over the 3 prior seasons."""
    per = defaultdict(list)  # coach -> [(year, att, dec, margin/game)]
    for y, ev in seasons.items():
        for t, (a, n, m, g) in go_counts(99, ev[0], ev[1], ev[2], ev[4]).items():
            if (t, y) in coach_map and n:
                per[coach_map[(t, y)]].append((y, a, n, m / max(g, 1)))
    out = {}
    for coach, lst in per.items():
        for y in seasons:
            prev = [x for x in lst if y - 3 <= x[0] < y]
            if prev:
                a, n = sum(x[1] for x in prev), sum(x[2] for x in prev)
                adj = sum(x[1] - slope * x[3] * x[2] for x in prev)
                out[(coach, y)] = ((a + lg_go * GO_PRIOR) / (n + GO_PRIOR),
                                   (adj + lg_go * GO_PRIOR) / (n + GO_PRIOR), n)
    return out


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
    go = go_counts(week, kicks, punts, fourth, [])
    g = {t: sum(x < week for x in ws) for t, ws in played.items()}
    lg_k = sum(v[1] for v in k.values()) / max(sum(g.values()), 1)
    lg_p = sum(v[1] for v in p.values()) / max(sum(g.values()), 1)
    lg_go = sum(v[0] for v in go.values()) / max(sum(v[1] for v in go.values()), 1)
    out = {}
    for t, n in g.items():
        fg_pg = (k[t][1] + lg_k * VOL_PRIOR) / (n + VOL_PRIOR)
        punt_pg = (p[t][1] + lg_p * VOL_PRIOR) / (n + VOL_PRIOR)
        out[t] = {
            "fg": 3 * fg_pg * k[t][0] / (k[t][1] + FG_PRIOR),
            "punt": punt_pg * p[t][0] / (p[t][1] + PUNT_PRIOR),
            "go": (go[t][0] + lg_go * GO_PRIOR) / (go[t][1] + GO_PRIOR) - lg_go,
        }
    return out


seasons = {y: season_events(y) for y in (2022, 2023, 2024, 2025)}
coach_map = coaches(c)
# How much scoring margin drives go rate: team-seasons, go rate on margin per game.
ts = [(a / n, m / g) for ev in seasons.values() for a, n, m, g in go_counts(99, ev[0], ev[1], ev[2], ev[4]).values()
      if n >= 50 and g >= 6]
slope = float(np.polyfit([x[1] for x in ts], [x[0] for x in ts], 1)[0])
lg_go_all = float(np.mean([x[0] for x in ts]))
history = coach_history(seasons, coach_map, slope, lg_go_all)
gr = [r for (_, y), r in history.items() if y in (2023, 2024, 2025)]
print(f"go rate vs margin: {slope * 100:+.2f} points of go rate per point of margin/game; "
      f"coach history for {len(gr)} coach-seasons")


def coach_go(t, y, week, counts, lg_go):
    """This coach's go rate entering `week`: prior-season history blended with this season."""
    a, n, m, g = counts.get(t, (0, 0, 0.0, 0))
    hist = history.get((coach_map.get((t, y)), y))
    raw_p, adj_p = (hist[0], hist[1]) if hist else (lg_go, lg_go)
    raw = (a + raw_p * COACH_WEIGHT) / (n + COACH_WEIGHT)
    adj = (a - slope * (m / max(g, 1)) * n + adj_p * COACH_WEIGHT) / (n + COACH_WEIGHT)
    return raw - lg_go, adj - lg_go


rows = []
for y in (2023, 2024, 2025):
    kicks, punts, fourth, played, margins = seasons[y]
    fg_exp = bin_rate([(d, m) for _, _, d, m in kicks], 5)
    net_exp = bin_rate([(s, n) for _, _, s, n in punts], 10)
    print(f"{y}: {len(kicks)} FG attempts ({sum(m for *_, m in kicks) / len(kicks):.1%} made), "
          f"{len(punts)} punts (avg net {sum(n for *_, n in punts) / len(punts):.1f}), "
          f"{sum(a for *_, a in fourth)} 4th-down attempts in {len({(t, w) for t, w, _ in fourth})} team box scores")
    cache = {}
    for r in csv.DictReader(open(f"data/{y}/totals_backtest_games.csv")):
        w = int(r["week"])
        if w not in cache:
            cache[w] = (ratings_before(w, kicks, punts, fourth, played, fg_exp, net_exp),
                        go_counts(w, kicks, punts, fourth, margins))
        (rt, counts), zero = cache[w], {"fg": 0.0, "punt": 0.0, "go": 0.0}
        a, h = rt.get(r["away"], zero), rt.get(r["home"], zero)
        ca, ch = coach_go(r["away"], y, w, counts, lg_go_all), coach_go(r["home"], y, w, counts, lg_go_all)
        rows.append({
            "season": y, "week": w, "p4": r["p4_game"] == "True",
            "act": float(r["actual_total"]), "proj": float(r["proj_total"]), "mkt": float(r["market_total"]),
            "fg": a["fg"] + h["fg"], "punt": (a["punt"] + h["punt"]) / 10, "go": (a["go"] + h["go"]) * 10,
            "coach": (ca[0] + ch[0]) * 10, "coach_adj": (ca[1] + ch[1]) * 10,
        })

act = np.array([r["act"] for r in rows]); proj = np.array([r["proj"] for r in rows]); mkt = np.array([r["mkt"] for r in rows])
season = np.array([r["season"] for r in rows])
FEATS = ["fg", "punt", "go", "coach", "coach_adj"]
F = np.array([[r[f] for f in FEATS] for r in rows], dtype=float)
print(f"\n{len(rows)} games. Per game, sum of both teams: FG pts over average sd {F[:,0].std():.2f}, "
      f"punt net yds over average (x10) sd {F[:,1].std():.2f}, 4th-down go rate over average (x10) sd {F[:,2].std():.2f}")
print(f"coach go rate (x10) sd {F[:,3].std():.2f}, margin-adjusted sd {F[:,4].std():.2f}")
print("(units: fg = points/game, punt = 10 yards/game, go/coach = 10 percentage points)")
for cols in ([0, 1, 2], [3], [4]):  # coach terms one at a time (they overlap with go and each other)
    for target, lab in ((act - proj, "actual - model"), (act - mkt, "actual - market")):
        A = np.column_stack([np.ones(len(rows)), F[:, cols]]); b, *_ = np.linalg.lstsq(A, target, rcond=None)
        se = np.sqrt(np.diag(np.linalg.inv(A.T @ A)) * (target - A @ b).var())
        print(f"{lab:<16}" + "  ".join(f"{FEATS[c]} {b[i+1]:+.2f}({se[i+1]:.2f})" for i, c in enumerate(cols)))


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
evaluate([3], "coach go rate")
evaluate([4], "coach go rate, adjusted")
evaluate([0, 1, 2], "all three")
