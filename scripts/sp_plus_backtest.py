"""Leave-one-season-out test of last season's final SP+ (CFBD /ratings/sp) as an adjustment to
the totals and spread models, using the saved backtest games in data/<year>/. Results are in
BACKTEST.md.

  CFBD_CACHE=.cfbd_cache python scripts/sp_plus_backtest.py

CFBD has only final SP+ for past seasons (no weekly snapshots), so in-season SP+ can't be
tested without look-ahead; last season's final SP+ is known before the season starts. Totals
use offense + defense rating over the national average for both teams (points a game); spreads
use the overall rating difference. FCS teams have no SP+, so only FBS games are tested.
API cost: 4 calls the first time (2022-2025).
"""
import csv, math, os
from collections import defaultdict
import numpy as np
from cfb_stats.api import CFBDClient
from cfb_stats.totals import weekly_card

c = CFBDClient(cache_dir=os.environ.get("CFBD_CACHE"))  # run from the repo root
sp = {}
for y in (2022, 2023, 2024, 2025):
    rows = [r for r in c.get("/ratings/sp", year=y) if r.get("ranking") and r["year"] == y]
    off = np.mean([r["offense"]["rating"] for r in rows]); dfn = np.mean([r["defense"]["rating"] for r in rows])
    sp[y] = {r["team"]: (r["rating"], r["offense"]["rating"] - off, r["defense"]["rating"] - dfn) for r in rows}
    print(y, len(rows), f"avg off {off:.1f} avg def {dfn:.1f}")
rows = []
for y in (2023, 2024, 2025):
    prev = sp[y - 1]
    spr = {r["game_id"]: r for r in csv.DictReader(open(f"data/{y}/spreads_backtest_games.csv"))}
    for r in csv.DictReader(open(f"data/{y}/totals_backtest_games.csv")):
        a, h = prev.get(r["away"]), prev.get(r["home"])
        if not a or not h:
            continue
        s = spr.get(r["game_id"], {})
        rows.append({"season": y, "week": int(r["week"]), "p4": r["p4_game"] == "True",
            "act": float(r["actual_total"]), "proj": float(r["proj_total"]), "mkt": float(r["market_total"]),
            "tot": a[1] + a[2] + h[1] + h[2],  # last season SP+ points over average, both sides
            "mar": h[0] - a[0],
            "pm": float(s["proj_margin"]) if s.get("proj_margin") else None,
            "am": float(s["actual_margin"]) if s.get("actual_margin") else None,
            "ms": float(s["market_spread"]) if s.get("market_spread") else None})
print(len(rows), "games with last-season SP+ for both teams")
season = np.array([r["season"] for r in rows]); week = np.array([r["week"] for r in rows])
act, proj, mkt, tot = (np.array([r[k] for r in rows]) for k in ("act", "proj", "mkt", "tot"))
for lab, t in (("actual - model", act - proj), ("actual - market", act - mkt)):
    A = np.column_stack([np.ones(len(t)), tot]); b, *_ = np.linalg.lstsq(A, t, rcond=None)
    se = math.sqrt(np.linalg.inv(A.T @ A)[1, 1] * (t - A @ b).var())
    print(f"totals {lab}: {b[1]:+.3f} ({se:.3f}) per point of last-season SP+ total")
tw = defaultdict(lambda: [0, 0]); rm = []
for s in (2023, 2024, 2025):
    tr, te = season != s, season == s
    A = np.column_stack([np.ones(tr.sum()), tot[tr]]); b, *_ = np.linalg.lstsq(A, (act - proj)[tr], rcond=None)
    adj = proj + tot * b[1]
    rm.append((math.sqrt(((act - proj)[te] ** 2).mean()), math.sqrt(((act - adj)[te] ** 2).mean())))
    for name, p in (("base", proj), ("adj", adj)):
        e = p - mkt; d = act - mkt; k = te & (np.abs(e) >= 3) & (d != 0)
        tw[f"edge3 {name}"][0] += int(((e > 0) == (d > 0))[k].sum()); tw[f"edge3 {name}"][1] += int(k.sum())
        rs = [{"week": (rows[i]["season"], rows[i]["week"]), "edge": e[i], "p4_game": rows[i]["p4"], "enough_data": True,
               "actual_total": act[i], "market_total": mkt[i]} for i in np.where(te)[0]]
        for r, side in weekly_card(rs, 3, p4_only=True):
            dd = r["actual_total"] - r["market_total"]
            if dd: tw[f"card {name}"][0] += (dd > 0) == (side == "OVER"); tw[f"card {name}"][1] += 1
print("totals RMSE " + " ".join(f"{a:.2f}->{b:.2f}" for a, b in rm) + " | " + " | ".join(f"{k} {w}-{n-w} {w/n:.1%}" for k, (w, n) in tw.items()))
# spreads
sr = [r for r in rows if None not in (r["pm"], r["am"], r["ms"])]
ss = np.array([r["season"] for r in sr]); pm, am, ms, mar = (np.array([r[k] for r in sr]) for k in ("pm", "am", "ms", "mar"))
wk = np.array([r["week"] for r in sr])
out = []; ats = defaultdict(lambda: [0, 0])
for s in (2023, 2024, 2025):
    tr, te = ss != s, ss == s
    A = np.column_stack([np.ones(tr.sum()), mar[tr]]); b, *_ = np.linalg.lstsq(A, (am - pm)[tr], rcond=None)
    adj = pm + mar * b[1]
    early = te & (wk <= 6)
    out.append(f"{math.sqrt(((am-pm)[te]**2).mean()):.2f}->{math.sqrt(((am-adj)[te]**2).mean()):.2f} (wk4-6 {math.sqrt(((am-pm)[early]**2).mean()):.2f}->{math.sqrt(((am-adj)[early]**2).mean()):.2f}) coef {b[1]:+.2f}")
    for name, p in (("base", pm), ("adj", adj)):
        e = p + ms; d = am + ms; k = te & (np.abs(e) >= 3) & (d != 0)  # home covers when margin + spread > 0
        ats[name][0] += int(((e > 0) == (d > 0))[k].sum()); ats[name][1] += int(k.sum())
print("spread margin RMSE " + " | ".join(out))
print("ATS edge>=3 " + " | ".join(f"{k} {w}-{n-w} {w/n:.1%}" for k, (w, n) in ats.items()))
