"""Lookahead and letdown spots against the spread, 2024-2025, on the saved spread backtest games.

A "big game" is one against an AP top-25 opponent (poll at the time). For a team in game G:
  lookahead: its next game is a big game, and G's opponent is unranked
  letdown:   its previous game was a big game, and G's opponent is unranked
Reports the team's record against the closing spread (betting against it in the spot) and how
it did against the model's projected margin. 2 API calls per season (games + AP polls), cached.

  python scripts/spots_backtest.py
"""
import argparse
import csv
import os
import sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from cfb_stats.api import CFBDClient  # noqa: E402
from cfb_stats.spots import ap_polls, ranked_at  # noqa: E402


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--cache", default=".cfbd_cache")
    p.add_argument("--years", type=int, nargs="+", default=[2024, 2025])
    args = p.parse_args()
    client = CFBDClient(cache_dir=args.cache)
    rows = []
    for y in args.years:
        polls = ap_polls(client, y)
        games = sorted((g for g in client.games(y, "regular")), key=lambda g: (g["week"], g.get("startDate") or ""))
        sched = defaultdict(list)
        for g in games:
            for t, o in ((g["homeTeam"], g["awayTeam"]), (g["awayTeam"], g["homeTeam"])):
                won = None
                if g.get("homePoints") is not None:
                    hp, ap = g["homePoints"], g["awayPoints"]
                    won = (hp > ap) if t == g["homeTeam"] else (ap > hp)
                sched[t].append({"id": g["id"], "week": g["week"], "opp": o, "won": won})
        nxt, prv = {}, {}
        for t, gs in sched.items():
            for i, s in enumerate(gs):
                prv[(t, s["id"])] = gs[i - 1] if i else None
                nxt[(t, s["id"])] = gs[i + 1] if i + 1 < len(gs) else None
        for r in csv.DictReader(open(f"data/{y}/spreads_backtest_games.csv")):
            if not r["market_spread"] or not r["actual_margin"]:
                continue
            gid, w = int(r["game_id"]), int(r["week"])
            line, act, proj = float(r["market_spread"]), float(r["actual_margin"]), float(r["proj_margin"])
            now = ranked_at(polls, w)
            for t, o, sign in ((r["home"], r["away"], 1), (r["away"], r["home"], -1)):
                n, pv = nxt.get((t, gid)), prv.get((t, gid))
                spot = {
                    "team": t, "season": y, "p4": r["p4_game"] == "True", "ranked": t in now,
                    "fav": sign * line < 0,  # team's own line negative = favored
                    "opp_unranked": o not in now,
                    # Next opponent ranked in this week's poll: known before kickoff.
                    "lookahead": bool(n and n["opp"] in now),
                    "letdown": bool(pv and pv["opp"] in ranked_at(polls, pv["week"])),
                    "letdown_won": bool(pv and pv["opp"] in ranked_at(polls, pv["week"]) and pv["won"]),
                    "cover": sign * (act + line),  # > 0: team covered
                    "vs_model": sign * (act - proj),  # > 0: team beat the model's projection
                }
                rows.append(spot)

    def report(label, sel):
        c = [r["cover"] for r in sel if r["cover"] != 0]
        w = sum(x < 0 for x in c)
        m = sum(r["vs_model"] for r in sel) / max(len(sel), 1)
        mk = sum(r["cover"] for r in sel) / max(len(sel), 1)
        print(f"  {label:<58} n={len(sel):>4}  betting against: {w}-{len(c) - w} ({w / max(len(c), 1):.1%})  "
              f"team vs line {mk:+.1f}  vs model {m:+.1f}")

    base = [r for r in rows if r["opp_unranked"]]
    print("Team's result vs the closing spread (and vs the model's margin), current opponent unranked:")
    report("all teams (baseline)", base)
    report("LOOKAHEAD: next opponent ranked", [r for r in base if r["lookahead"] and not r["letdown"]])
    report("  ... and the team is ranked", [r for r in base if r["lookahead"] and not r["letdown"] and r["ranked"]])
    report("  ... and the team is favored", [r for r in base if r["lookahead"] and not r["letdown"] and r["fav"]])
    report("  ... P4 only", [r for r in base if r["lookahead"] and not r["letdown"] and r["p4"]])
    report("LETDOWN: last opponent ranked", [r for r in base if r["letdown"] and not r["lookahead"]])
    report("  ... and won that game", [r for r in base if r["letdown_won"] and not r["lookahead"]])
    report("  ... and lost that game", [r for r in base if r["letdown"] and not r["letdown_won"] and not r["lookahead"]])
    report("  ... won it, and favored now", [r for r in base if r["letdown_won"] and not r["lookahead"] and r["fav"]])
    report("SANDWICH: ranked opponent before and after", [r for r in base if r["letdown"] and r["lookahead"]])
    for y in args.years:
        print(f" {y}:")
        report("  lookahead", [r for r in base if r["season"] == y and r["lookahead"] and not r["letdown"]])
        report("  letdown after a win", [r for r in base if r["season"] == y and r["letdown_won"] and not r["lookahead"]])
        report("  after losing to a ranked team", [r for r in base if r["season"] == y and r["letdown"]
                                                  and not r["letdown_won"] and not r["lookahead"]])
        report("  sandwich", [r for r in base if r["season"] == y and r["letdown"] and r["lookahead"]])


if __name__ == "__main__":
    main()
