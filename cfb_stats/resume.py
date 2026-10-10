"""Résumé metrics: strength of schedule, strength of record and strength of victory.

All three come from a points rating fit on every completed game (FBS and FCS):

    capped margin (home - away) = home field + rating[home] - rating[away]

a ridge fit with margins capped at MARGIN_CAP so routs don't dominate. A rating is points
better than an average team on a neutral field. Win chances use a normal curve with the fit's
residual spread.

- Strength of schedule (sos): average opponent rating, adjusted for where each game was
  played (a road game counts the opponent's home field). Also opponents' combined win % and
  the same for games still to play (sos_remaining).
- Strength of record (sor): the chance an average top-25 team (the 25th best FBS rating)
  would have won at least as many games against this schedule. Lower = more impressive
  record; ranked on it. wins_above_top25 = wins minus that team's expected wins.
- Strength of victory (sov): average rating of the teams it beat, and their combined win %.

These describe what a team has done, not how good it is: the ratings and projections live in
cfb_stats.totals. Usage:

  python -m cfb_stats.resume            # current season -> data/<year>/resume.csv
"""

import argparse
import math
import os
from collections import defaultdict

import numpy as np

from .collect import P4_CONFERENCES, default_year, write_csv

MARGIN_CAP = 28
ALPHA = 2.0  # ridge penalty in games: pulls teams with few games toward average
TOP_N = 25


def _done(g):
    return g.get("homePoints") is not None and g.get("awayPoints") is not None and g.get("completed", True)


def power_ratings(games, cap=MARGIN_CAP, alpha=ALPHA):
    """-> ({team: rating}, home field points, residual sd)."""
    played = [g for g in games if _done(g)]
    teams = sorted({t for g in played for t in (g["homeTeam"], g["awayTeam"])})
    if not teams:
        return {}, 0.0, 14.0
    idx = {t: i for i, t in enumerate(teams)}
    X = np.zeros((len(played), len(teams) + 1))
    y = np.zeros(len(played))
    for k, g in enumerate(played):
        X[k, idx[g["homeTeam"]]] = 1
        X[k, idx[g["awayTeam"]]] = -1
        X[k, -1] = 0 if g.get("neutralSite") else 1
        y[k] = max(min(g["homePoints"] - g["awayPoints"], cap), -cap)
    pen = np.full(X.shape[1], float(alpha))
    pen[-1] = 1e-6  # home field essentially unshrunk (a tiny penalty keeps all-neutral data solvable)
    beta = np.linalg.solve(X.T @ X + np.diag(pen), X.T @ y)
    resid = y - X @ beta
    sd = float(np.sqrt((resid ** 2).sum() / max(len(y) - 1, 1))) or 14.0
    r = beta[:-1] - beta[:-1].mean()
    return {t: float(r[i]) for t, i in idx.items()}, float(beta[-1]), sd


def _win_prob(diff, sd):
    return 0.5 * (1 + math.erf(diff / (sd * math.sqrt(2))))


def _at_least(probs, k):
    """P(at least k wins) for independent games with these win chances."""
    dist = [1.0]
    for p in probs:
        nxt = [0.0] * (len(dist) + 1)
        for w, q in enumerate(dist):
            nxt[w] += q * (1 - p)
            nxt[w + 1] += q * p
        dist = nxt
    return sum(dist[k:]) if k < len(dist) else 0.0


def resume_table(games, fbs_teams=None, top_n=TOP_N):
    """One row per FBS team (or every team with a game if fbs_teams is None)."""
    ratings, hfa, sd = power_ratings(games)
    conf = {}
    for g in games:
        conf.setdefault(g["homeTeam"], g.get("homeConference"))
        conf.setdefault(g["awayTeam"], g.get("awayConference"))
    teams = sorted(fbs_teams if fbs_teams is not None else ratings)
    record = defaultdict(lambda: [0, 0])
    for g in games:
        if _done(g) and g["homePoints"] != g["awayPoints"]:
            win = g["homeTeam"] if g["homePoints"] > g["awayPoints"] else g["awayTeam"]
            lose = g["awayTeam"] if win == g["homeTeam"] else g["homeTeam"]
            record[win][0] += 1
            record[lose][1] += 1

    def pct(opps):
        w = sum(record[o][0] for o in opps)
        n = w + sum(record[o][1] for o in opps)
        return round(w / n, 3) if n else None

    fbs_r = sorted((ratings[t] for t in teams if t in ratings), reverse=True)
    ref = fbs_r[min(top_n, len(fbs_r)) - 1] if fbs_r else 0.0
    rows = []
    for t in teams:
        faced, beaten, todo, probs = [], [], [], []
        wins = losses = 0
        for g in games:
            if t not in (g["homeTeam"], g["awayTeam"]):
                continue
            home = g["homeTeam"] == t
            opp = g["awayTeam"] if home else g["homeTeam"]
            site = 0 if g.get("neutralSite") else (1 if home else -1)
            opp_r = ratings.get(opp, min(ratings.values(), default=0.0))
            if not _done(g):
                todo.append((opp, opp_r - site * hfa))
                continue
            mine, theirs = (g["homePoints"], g["awayPoints"]) if home else (g["awayPoints"], g["homePoints"])
            faced.append((opp, opp_r - site * hfa))
            probs.append(_win_prob(ref - opp_r + site * hfa, sd))
            if mine > theirs:
                wins += 1
                beaten.append((opp, opp_r))
            elif mine < theirs:
                losses += 1
        exp_wins = sum(probs)
        rows.append({
            "team": t, "conference": conf.get(t), "wins": wins, "losses": losses,
            "rating": round(ratings.get(t, 0.0), 1),
            "sos": round(float(np.mean([r for _, r in faced])), 2) if faced else None,
            "sos_opp_win_pct": pct([o for o, _ in faced]),
            "sor": round(_at_least(probs, wins), 3) if faced else None,
            "wins_above_top25": round(wins - exp_wins, 2) if faced else None,
            "sov": round(float(np.mean([r for _, r in beaten])), 2) if beaten else None,
            "sov_opp_win_pct": pct([o for o, _ in beaten]),
            "sos_remaining": round(float(np.mean([r for _, r in todo])), 2) if todo else None,
            "games_remaining": len(todo),
        })
    for key, reverse in (("rating", True), ("sos", True), ("sor", False), ("sov", True), ("sos_remaining", True)):
        ranked = sorted((r for r in rows if r[key] is not None),
                        key=lambda r: ((r[key], -(r["wins_above_top25"] or 0)) if key == "sor"
                                       else (-r[key] if reverse else r[key])))
        for i, r in enumerate(ranked, 1):
            r[f"{key}_rank"] = i
    rows.sort(key=lambda r: r.get("sor_rank") or 999)
    return rows


def main(argv=None):
    from .api import CFBDClient

    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--year", type=int, default=default_year())
    p.add_argument("--out", default="data")
    p.add_argument("--api-key", default=None)
    p.add_argument("--p4", action="store_true", help="print P4 teams only")
    args = p.parse_args(argv)
    client = CFBDClient(api_key=args.api_key)
    games = [dict(g, seasonType=s) for s in ("regular", "postseason") for g in client.games(args.year, s)]
    fbs = {t["school"] for t in client.fbs_teams(args.year)}
    rows = resume_table(games, fbs)
    path = os.path.join(args.out, str(args.year), "resume.csv")
    write_csv(path, rows)
    print(f"wrote {path} ({len(rows)} teams)")
    print(f"{'SOR':>4} {'team':<20} {'W-L':>5} {'rating':>7} {'SOS rk':>6} {'SOV rk':>6} {'wins vs top-25 exp':>18}")
    for r in rows[:25] if not args.p4 else [r for r in rows if r["conference"] in P4_CONFERENCES][:25]:
        print(f"{r['sor_rank']:>4} {r['team']:<20} {r['wins']:>2}-{r['losses']:<2} {r['rating']:>7} "
              f"{r.get('sos_rank', '-'):>6} {r.get('sov_rank', '-'):>6} {r['wins_above_top25']:>+18}")


if __name__ == "__main__":
    main()
