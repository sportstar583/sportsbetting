"""Penalties: team discipline (flags committed and drawn) and officiating crews by conference.

Per-game penalties come from CFBD /games/teams ("totalPenaltiesYards", e.g. "7-65"), one call
per week. CFBD has no officiating crew data. Conference games are worked by that conference's
crew; for non-conference games the crew is taken to be the home team's conference's (see the
crew test in scripts/penalties_backtest.py, which checks home vs visiting crew against the data).

Ratings (ridge fit, flags or yards per team-game):

    team's flags in a game = league + commit[team] + draw[opponent] + crew[conference] + home

Backtest: scripts/penalties_backtest.py (results in BACKTEST.md).
"""

from collections import defaultdict

import numpy as np

ALPHA = 4.0  # ridge penalty, in games
MAX_FLAGS, MAX_YARDS = 30, 300


def parse(box_games):
    """{game id: {team: (flags, yards)}} from /games/teams responses."""
    out = defaultdict(dict)
    for g in box_games:
        for t in g.get("teams") or []:
            for s in t.get("stats") or []:
                if s.get("category") == "totalPenaltiesYards":
                    try:
                        n, y = (int(x) for x in str(s["stat"]).split("-"))
                    except ValueError:
                        continue
                    if 0 <= n <= MAX_FLAGS and -50 <= y <= MAX_YARDS:  # skip data errors (e.g. "743-37")
                        out[g["id"]][t["team"]] = (n, y)
    return dict(out)


def crew_of(game, crew="home"):
    """Conference whose officials work the game."""
    if game.get("conferenceGame") or game.get("homeConference") == game.get("awayConference"):
        return game.get("homeConference")
    return game.get("homeConference") if crew == "home" else game.get("awayConference")


def observations(games, pens, crew="home"):
    """One row per team-game: team, opponent, home, crew conference, flags, yards."""
    rows = []
    for g in games:
        p = pens.get(g["id"])
        if not p:
            continue
        for team, opp, home in ((g["homeTeam"], g["awayTeam"], 1), (g["awayTeam"], g["homeTeam"], -1)):
            if team in p and opp in p:
                rows.append({"game": g["id"], "team": team, "opp": opp,
                             "home": 0 if g.get("neutralSite") else home,
                             "crew": crew_of(g, crew), "flags": p[team][0], "yards": p[team][1]})
    return rows


def fit(rows, key="flags", alpha=ALPHA):
    """-> {"mean", "home", "commit": {team}, "draw": {team}, "crew": {conference}}."""
    teams = sorted({r["team"] for r in rows} | {r["opp"] for r in rows})
    crews = sorted({r["crew"] for r in rows if r["crew"]})
    ti = {t: i for i, t in enumerate(teams)}
    nt = len(teams)
    ci = {c: 2 * nt + i for i, c in enumerate(crews)}
    k = 2 * nt + len(crews) + 2  # + intercept, home
    X = np.zeros((len(rows), k))
    y = np.array([r[key] for r in rows], dtype=float)
    for j, r in enumerate(rows):
        X[j, ti[r["team"]]] = 1
        X[j, nt + ti[r["opp"]]] = 1
        if r["crew"]:
            X[j, ci[r["crew"]]] = 1
        X[j, -2] = 1
        X[j, -1] = r["home"]
    pen = np.full(k, alpha)
    pen[-2:] = 1e-3  # intercept and home field essentially unshrunk (tiny penalty keeps it solvable)
    b = np.linalg.solve(X.T @ X + np.diag(pen), X.T @ y)
    return {"mean": float(b[-2]), "home": float(b[-1]),
            "commit": {t: float(b[i]) for t, i in ti.items()},
            "draw": {t: float(b[nt + i]) for t, i in ti.items()},
            "crew": {c: float(b[i]) for c, i in ci.items()}}


def expected(model, team, opp, home, crew):
    """Expected flags (or yards) for team against opp with this crew."""
    return (model["mean"] + model["commit"].get(team, 0.0) + model["draw"].get(opp, 0.0)
            + model["crew"].get(crew, 0.0) + model["home"] * home)
