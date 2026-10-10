"""Team box scores (CFBD /games/teams, one call per week) and opponent-adjusted rate ratings.

Used for turnover luck and line-of-scrimmage (pass protection, pass rush, run blocking, run
stopping) ratings. See scripts/turnovers_trenches_backtest.py and BACKTEST.md.
"""

from collections import defaultdict

import numpy as np

OFFENSE = ("interceptions", "totalFumbles", "fumblesLost", "turnovers", "rushingAttempts", "rushingYards")
DEFENSE = ("passesIntercepted", "passesDeflected", "fumblesRecovered", "sacks", "qbHurries", "tacklesForLoss")
FIELDS = OFFENSE + DEFENSE


def _int(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def parse(box_games):
    """{game id: {team: {field: int or None, "pass_att": int}}}."""
    out = defaultdict(dict)
    for g in box_games:
        for t in g.get("teams") or []:
            s = {x.get("category"): x.get("stat") for x in t.get("stats") or []}
            # CFBD leaves a stat out when it's zero. Defensive stats are only recorded for some
            # games (FBS box scores have a tackles line); without them, missing means unknown.
            has_def = "tackles" in s
            row = {k: _int(s.get(k, 0)) for k in OFFENSE}
            row.update({k: _int(s.get(k, 0 if has_def else None)) for k in DEFENSE})
            ca = str(s.get("completionAttempts") or "")
            row["pass_att"] = _int(ca.split("-")[1]) if "-" in ca else None
            out[g["id"]][t["team"]] = row
    return dict(out)


def rate_fit(rows, alpha=30.0):
    """Opponent-adjusted rate: num/den for an offense = league + off[team] + def[opp] + home.

    rows: dicts with team, opp, home, num, den. Weighted ridge (weight = den); alpha is in
    denominator units (e.g. dropbacks). -> {"mean", "home", "off", "def"}."""
    rows = [r for r in rows if r["den"]]
    teams = sorted({r["team"] for r in rows} | {r["opp"] for r in rows})
    ti = {t: i for i, t in enumerate(teams)}
    n = len(teams)
    X = np.zeros((len(rows), 2 * n + 2))
    y = np.array([r["num"] / r["den"] for r in rows])
    w = np.array([r["den"] for r in rows], dtype=float)
    for j, r in enumerate(rows):
        X[j, ti[r["team"]]] = 1
        X[j, n + ti[r["opp"]]] = 1
        X[j, -2] = 1
        X[j, -1] = r["home"]
    pen = np.full(2 * n + 2, float(alpha))
    pen[-2:] = 1e-3  # intercept and home field essentially unshrunk (tiny penalty keeps it solvable)
    Xw = X * w[:, None]
    b = np.linalg.solve(X.T @ Xw + np.diag(pen), Xw.T @ y)
    return {"mean": float(b[-2]), "home": float(b[-1]),
            "off": {t: float(b[i]) for t, i in ti.items()}, "def": {t: float(b[n + i]) for t, i in ti.items()}}
