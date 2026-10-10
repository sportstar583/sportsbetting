"""Leave-one-season-out test of team game-script tendencies (foot on the gas with a big lead,
prevent defense) on the saved backtest games in data/<year>/. Results are in BACKTEST.md.

Needs every regular-season week's drives for 2023-2025 (about 50 API calls the first time;
cached after that):

  python scripts/game_script_backtest.py --cache .cfbd_cache
"""
import argparse
import csv
import math
from collections import defaultdict

import numpy as np

from cfb_stats import game_script as gs
from cfb_stats.api import CFBDClient
from cfb_stats.collect import write_csv
from cfb_stats.totals import weekly_card

YEARS = (2023, 2024, 2025)


def load_drives(client, year):
    games = client.games(year, "regular")
    weeks = sorted({g["week"] for g in games if g.get("completed")})
    return {w: client.drives(year, w, "regular") for w in weeks}


def before(drives, week):
    return [d for w, ds in drives.items() if w < week for d in ds]


def lead_counts(drives):
    """{(game id, team): 2nd-half offensive drives started up LEAD+}."""
    out = defaultdict(int)
    for d in drives:
        if gs.situation(d) == "lead":
            out[(d["gameId"], d["offense"])] += 1
    return out


def fit_lead_curve(pairs):
    """Grid-search LEAD_MAX/MID/SD for (spread for team, lead drives) pairs."""
    x = np.array([p[0] for p in pairs]); y = np.array([p[1] for p in pairs])
    best = None
    for mid in np.arange(4, 30, 1.0):
        for sd in np.arange(4, 30, 1.0):
            f = 0.5 * (1 + np.vectorize(math.erf)((x - mid) / (sd * math.sqrt(2))))
            mx = float(f @ y / (f @ f))
            err = float(((y - mx * f) ** 2).mean())
            if best is None or err < best[0]:
                best = (err, mx, mid, sd)
    return best[1:]


def card_record(rows, key):
    w = n = 0
    rs = [{"week": r["week"], "edge": r[key] - r["mkt"], "p4_game": r["p4"], "enough_data": True,
           "act": r["act"], "mkt": r["mkt"]} for r in rows]
    for r, side in weekly_card(rs, 3, p4_only=True):
        d = r["act"] - r["mkt"]
        if d:
            w += (d > 0) == (side == "OVER"); n += 1
    return w, n


def edge_record(act, proj, line, t=3):
    e, d = proj - line, act - line
    k = (np.abs(e) >= t) & (d != 0)
    return int(((e > 0) == (d > 0))[k].sum()), int(k.sum())


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--cache", default=".cfbd_cache")
    args = p.parse_args()
    client = CFBDClient(cache_dir=args.cache)

    tot, spr, pairs, season_tend = [], [], [], {}
    for y in YEARS:
        drives = load_drives(client, y)
        all_d = [d for ds in drives.values() for d in ds]
        if not any(d.get("startOffenseScore") is not None for d in all_d):
            raise SystemExit(f"{y}: drives carry no start scores")
        season_tend[y] = gs.tendencies(all_d, prior=0)
        counts = lead_counts(all_d)
        spreads = {int(r["game_id"]): r for r in csv.DictReader(open(f"data/{y}/spreads_backtest_games.csv"))}
        for r in spreads.values():
            if r["market_spread"]:
                fav = -float(r["market_spread"])
                pairs.append((fav, counts.get((int(r["game_id"]), r["home"]), 0)))
                pairs.append((-fav, counts.get((int(r["game_id"]), r["away"]), 0)))
        tend_by_week = {}
        for r in csv.DictReader(open(f"data/{y}/totals_backtest_games.csv")):
            w = int(r["week"])
            if w not in tend_by_week:
                tend_by_week[w] = gs.tendencies(before(drives, w))
            s = spreads.get(int(r["game_id"]), {})
            fav = -float(s["market_spread"]) if s.get("market_spread") else 0.0
            t_adj, m_adj = gs.adjustments(tend_by_week[w], r["home"], r["away"], fav, 1.0, 1.0)
            tot.append({"season": y, "week": (y, w), "game_id": r["game_id"], "home": r["home"], "away": r["away"],
                        "p4": r["p4_game"] == "True", "act": float(r["actual_total"]),
                        "proj": float(r["proj_total"]), "mkt": float(r["market_total"]), "raw": t_adj})
            if s.get("market_spread") and s.get("actual_margin"):
                spr.append({"season": y, "act": float(s["actual_margin"]), "proj": float(s["proj_margin"]),
                            "line": float(s["market_spread"]), "raw": m_adj})

    mx, mid, sd = fit_lead_curve(pairs)
    print(f"lead-drive curve: LEAD_MAX {mx:.2f}  LEAD_MID {mid:.0f}  LEAD_SD {sd:.0f}  "
          f"(mean lead drives {np.mean([p[1] for p in pairs]):.2f} per team-game)")

    # Is it a trait? Same team, consecutive seasons (unshrunk, teams with 15+ lead drives both years).
    for name, nk in (("gas", "lead_drives"), ("prevent", "prevent_drives")):
        for a, b in zip(YEARS, YEARS[1:]):
            ta, tb = season_tend[a], season_tend[b]
            common = [t for t in ta if t in tb and ta[t][nk] >= 15 and tb[t][nk] >= 15]
            r = np.corrcoef([ta[t][name] for t in common], [tb[t][name] for t in common])[0, 1]
            print(f"  {name:<8} {a}->{b}: year-to-year correlation {r:+.2f} ({len(common)} teams)")

    summary = []
    for label, rows, line_key in (("totals", tot, "mkt"), ("spreads", spr, "line")):
        season = np.array([r["season"] for r in rows])
        act = np.array([r["act"] for r in rows]); proj = np.array([r["proj"] for r in rows])
        raw = np.array([r["raw"] for r in rows]); line = np.array([r[line_key] for r in rows])
        if label == "spreads":  # compare margin to -spread: home covers when margin + spread > 0
            line = -line
        print(f"\n{label}: {len(rows)} games, raw adjustment sd {raw.std():.2f} pts, "
              f"|adj| >= 1 in {(np.abs(raw) >= 1).mean():.0%}")
        for target, lab in ((act - proj, "actual - model"), (act - line, "actual - market")):
            b = float(raw @ target / (raw @ raw))
            se = math.sqrt(((target - b * raw) ** 2).mean() / (raw @ raw))
            print(f"  slope on {lab:<16} {b:+.2f} ({se:.2f})")
        adj = proj.copy()
        for s in YEARS:
            tr, te = season != s, season == s
            k = max(float(raw[tr] @ (act - proj)[tr] / (raw[tr] @ raw[tr])), 0.0)
            adj[te] = proj[te] + k * raw[te]
            for r, a in zip((r for r, m in zip(rows, te) if m), adj[te]):
                r["adj"] = a
            row = {"model": label, "season": s, "scale": round(k, 2),
                   "rmse_base": round(math.sqrt(((act - proj)[te] ** 2).mean()), 2),
                   "rmse_adj": round(math.sqrt(((act - adj)[te] ** 2).mean()), 2)}
            for name, pr in (("base", proj), ("adj", adj)):
                w, n = edge_record(act[te], pr[te], line[te])
                row[f"edge3_{name}"] = f"{w}-{n - w}"
            if label == "totals":
                for name, key in (("base", "proj"), ("adj", "adj")):
                    w, n = card_record([r for r in rows if r["season"] == s], key)
                    row[f"card_{name}"] = f"{w}-{n - w}"
            summary.append(row)
            print("  " + "  ".join(f"{k} {v}" for k, v in row.items()))
        k_all = max(float(raw @ (act - proj) / (raw @ raw)), 0.0)
        print(f"  scale fit on all three seasons: {k_all:.2f}")
    write_csv("data/game_script_backtest.csv", summary)
    write_csv("data/game_script_backtest_games.csv",
              [{k: (round(v, 2) if isinstance(v, float) else v) for k, v in r.items() if k != "week"} for r in tot])


if __name__ == "__main__":
    main()
