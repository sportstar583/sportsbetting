"""Leave-one-season-out test of rest, travel, altitude and game-script adjustments to the totals
model, using the saved backtest games in data/<year>/. Results are in BACKTEST.md.

  CFBD_CACHE=.cfbd_cache python scripts/situational_backtest.py
"""
import csv, math, datetime, os, zoneinfo
from collections import Counter, defaultdict
import numpy as np
from cfb_stats.api import CFBDClient
from cfb_stats.totals import weekly_card

c = CFBDClient(cache_dir=os.environ.get("CFBD_CACHE"))  # run from the repo root
venues = {v["id"]: v for v in c.get("/venues")}


def ts(s):
    return datetime.datetime.fromisoformat(s.replace("Z", "+00:00"))


def miles(a, b):
    la1, lo1, la2, lo2 = map(math.radians, (a["latitude"], a["longitude"], b["latitude"], b["longitude"]))
    h = math.sin((la2 - la1) / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2
    return 3959 * 2 * math.asin(math.sqrt(h))


def utc_off(v, when):
    try:
        return zoneinfo.ZoneInfo(v["timezone"]).utcoffset(when).total_seconds() / 3600
    except Exception:
        return None


rows = []
for y in (2023, 2024, 2025):
    games = [g for g in c.games(y, "regular") + c.games(y, "postseason") if g.get("startDate")]
    hv = defaultdict(Counter)
    for g in games:
        if not g.get("neutralSite") and g.get("venueId"):
            hv[g["homeTeam"]][g["venueId"]] += 1
    home_venue = {t: cnt.most_common(1)[0][0] for t, cnt in hv.items()}
    last = defaultdict(list)
    for g in sorted(games, key=lambda g: g["startDate"]):
        for t in (g["homeTeam"], g["awayTeam"]):
            last[t].append((ts(g["startDate"]), g["id"]))
    prev = {}
    for t, lst in last.items():
        for i, (when, gid) in enumerate(lst):
            prev[(t, gid)] = (when - lst[i - 1][0]).days if i else None
    by_id = {g["id"]: g for g in games}
    spreads = {int(r["game_id"]): r for r in csv.DictReader(open(f"data/{y}/spreads_backtest_games.csv"))}
    for r in csv.DictReader(open(f"data/{y}/totals_backtest_games.csv")):
        g = by_id.get(int(r["game_id"]))
        v = venues.get(g.get("venueId")) if g else None
        if not g or not v or v.get("latitude") is None:
            continue
        rest = [prev.get((t, g["id"])) for t in (g["homeTeam"], g["awayTeam"])]
        rest = [x if x is not None else 14 for x in rest]  # season opener ~ fully rested
        away_home = venues.get(home_venue.get(g["awayTeam"]))
        travel = miles(away_home, v) if away_home and away_home.get("latitude") is not None else 0.0
        when = ts(g["startDate"])
        tz = abs((utc_off(v, when) or 0) - (utc_off(away_home, when) or 0)) if away_home else 0
        sp = spreads.get(int(r["game_id"]), {}).get("market_spread")
        elev = float(v.get("elevation") or 0)
        rows.append({
            "season": y, "week": int(r["week"]), "p4": r["p4_game"] == "True",
            "act": float(r["actual_total"]), "proj": float(r["proj_total"]), "mkt": float(r["market_total"]),
            "short": sum(x < 6 for x in rest), "bye": sum(x >= 13 for x in rest) - (int(r["week"]) <= 1) * 2,
            "travel": travel / 1000, "tz": tz, "alt": max(elev - 1000, 0) / 1000,
            "blowout": max(abs(float(sp)) - 14, 0) / 10 if sp not in (None, "") else 0.0,
        })

act = np.array([r["act"] for r in rows]); proj = np.array([r["proj"] for r in rows]); mkt = np.array([r["mkt"] for r in rows])
season = np.array([r["season"] for r in rows])
FEATS = ["short", "bye", "travel", "tz", "alt", "blowout"]
F = np.array([[r[f] for f in FEATS] for r in rows], dtype=float)
print(f"{len(rows)} games; short-week teams {int(F[:,0].sum())}, off-bye teams {int(F[:,1].sum())}, "
      f"altitude games {int((F[:,4] > 0).sum())}, spread>14 games {int((F[:,5] > 0).sum())}")
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
evaluate([0, 1], "rest (short week, bye)")
evaluate([2, 3], "travel + time zones")
evaluate([4], "altitude")
evaluate([5], "game script (spread>14)")
evaluate(list(range(6)), "all situational")
