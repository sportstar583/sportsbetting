"""Opponent-adjusted EPA (PPA) for teams and players.

Every team-game is one observation of "offense X vs defense Y". For each metric we fit

    value = intercept + home_field * home + offense[X] + defense[Y]

with play-weighted ridge regression over every FBS game of the season. The ridge
penalty shrinks teams with few games toward average. A team's adjusted offense is
intercept + offense[X]: what it would average against an average defense at a neutral
site. Adjusted defense is intercept + defense[Y] (EPA allowed, lower is better).

Players are adjusted game by game: if a running back faces a run defense that allows
0.10 EPA/rush less than average, 0.10 is added to their EPA/rush for that game.
"""

from collections import defaultdict

import numpy as np

# (output name, path into the advanced game stats offense/defense dict, play-count path)
METRICS = [
    ("epa", ("ppa",), None),
    ("success_rate", ("successRate",), None),
    ("explosiveness", ("explosiveness",), None),
    ("rush_epa", ("rushingPlays", "ppa"), "rushingPlays"),
    ("rush_success_rate", ("rushingPlays", "successRate"), "rushingPlays"),
    ("pass_epa", ("passingPlays", "ppa"), "passingPlays"),
    ("pass_success_rate", ("passingPlays", "successRate"), "passingPlays"),
]

# Player averagePPA key -> team defense metric used to adjust it.
PLAYER_METRICS = {"all": "epa", "rush": "rush_epa", "pass": "pass_epa"}

DEFAULT_ALPHA = 150.0  # ridge penalty, in plays (~2-3 games of data)


def _dig(d, path):
    for key in path:
        if not isinstance(d, dict):
            return None
        d = d.get(key)
    return d


def _plays(side, count_key):
    """Play count for a metric. Rush/pass counts are totalPPA / ppa."""
    if count_key:
        sub = side.get(count_key) or {}
        ppa, total = sub.get("ppa"), sub.get("totalPPA")
        if ppa and total is not None:
            return abs(total / ppa)
    return side.get("plays") or 0


def game_home_map(games):
    """gameId -> (home team, neutral site)."""
    return {g["id"]: (g.get("homeTeam"), bool(g.get("neutralSite"))) for g in games if "id" in g}


def observations(game_rows, games):
    """One (game, offense, defense, home, side-dict) per offense-vs-defense matchup.

    Each team-game row holds both its own offense and defense, so a game against an
    FCS team (which has no row of its own) still yields both matchups.
    """
    homes = game_home_map(games)
    seen, obs = set(), []

    def add(game_id, off, dfn, side):
        if not side or (game_id, off) in seen:
            return
        seen.add((game_id, off))
        home_team, neutral = homes.get(game_id, (None, True))
        home = 0 if neutral or home_team is None else (1 if off == home_team else -1)
        obs.append({"game_id": game_id, "offense": off, "defense": dfn, "home": home, "stats": side})

    for r in game_rows:
        add(r["gameId"], r["team"], r["opponent"], r.get("offense"))
    for r in game_rows:
        add(r["gameId"], r["opponent"], r["team"], r.get("defense"))
    return obs


def fit(obs, path, count_key, alpha=DEFAULT_ALPHA, huber_k=None, prior=None):
    """Weighted ridge fit for one metric. Returns (intercept, hfa, off{}, def{}, raw_off{}, raw_def{}).

    prior: {"off": {team: deviation}, "def": {team: deviation}} shrinks each team toward its
    own preseason estimate (deviation from league average) instead of toward average.

    Rows are weighted by play count times o.get("weight", 1). With huber_k set, the fit is
    re-run with games whose result lands more than huber_k robust standard deviations from
    the model's expectation down-weighted (Huber), so a rout of a weak opponent moves a
    rating less than its raw margin would.
    """
    rows = []
    for o in obs:
        val, w = _dig(o["stats"], path), _plays(o["stats"], count_key)
        if val is not None and w > 0:
            rows.append((o["offense"], o["defense"], o["home"], float(val), float(w) * o.get("weight", 1.0)))
    if not rows:
        return None

    teams = sorted({r[0] for r in rows} | {r[1] for r in rows})
    idx = {t: i for i, t in enumerate(teams)}
    n = len(teams)
    X = np.zeros((len(rows), 2 + 2 * n))
    y = np.empty(len(rows))
    w = np.empty(len(rows))
    for i, (off, dfn, home, val, weight) in enumerate(rows):
        X[i, 0] = 1.0
        X[i, 1] = home
        X[i, 2 + idx[off]] = 1.0
        X[i, 2 + n + idx[dfn]] = 1.0
        y[i], w[i] = val, weight

    penalty = np.full(X.shape[1], alpha)
    penalty[:2] = 0.0  # don't shrink intercept or home field

    mu = np.zeros(X.shape[1])
    if prior:
        for t, i in idx.items():
            mu[2 + i] = prior.get("off", {}).get(t, 0.0)
            mu[2 + n + i] = prior.get("def", {}).get(t, 0.0)

    def solve(weights):
        XtW = X.T * weights
        # Ridge toward mu: minimizes weighted squared error + sum(penalty * (beta - mu)^2).
        return np.linalg.solve(XtW @ X + np.diag(penalty), XtW @ y + penalty * mu)

    beta = solve(w)
    if huber_k:
        for _ in range(5):
            # Residual noise shrinks with plays, so scale residuals by sqrt(plays).
            z = (y - X @ beta) * np.sqrt(w)
            scale = 1.4826 * np.median(np.abs(z)) or 1.0
            excess = np.abs(z) / (huber_k * scale)
            beta = solve(w * np.where(excess > 1, 1 / np.maximum(excess, 1e-9), 1.0))

    intercept, hfa = beta[0], beta[1]
    adj_off = {t: intercept + beta[2 + idx[t]] for t in teams}
    adj_def = {t: intercept + beta[2 + n + idx[t]] for t in teams}

    raw_off, raw_def = defaultdict(lambda: [0.0, 0.0]), defaultdict(lambda: [0.0, 0.0])
    for off, dfn, _, val, weight in rows:
        raw_off[off][0] += val * weight
        raw_off[off][1] += weight
        raw_def[dfn][0] += val * weight
        raw_def[dfn][1] += weight
    raw_off = {t: s / c for t, (s, c) in raw_off.items()}
    raw_def = {t: s / c for t, (s, c) in raw_def.items()}
    return {"intercept": intercept, "hfa": hfa, "off": adj_off, "def": adj_def, "raw_off": raw_off, "raw_def": raw_def}


def fit_all(game_rows, games, alpha=DEFAULT_ALPHA, obs_weight=None, huber_k=None, subset_alpha_scale=1.0,
            priors=None):
    """obs_weight(obs) -> multiplier on that matchup's weight (e.g. less for FCS opponents).

    subset_alpha_scale multiplies the ridge penalty for rush-only and pass-only metrics. The
    penalty is in plays, and each of those sees only about half a team's plays, so with the
    same penalty they are shrunk about twice as hard as the overall metrics.
    """
    obs = observations(game_rows, games)
    if obs_weight:
        for o in obs:
            o["weight"] = obs_weight(o)
    models = {}
    for name, path, count_key in METRICS:
        m = fit(obs, path, count_key, alpha * (subset_alpha_scale if count_key else 1.0), huber_k,
                (priors or {}).get(name))
        if m:
            models[name] = m
    return models, obs


def _rank(values, team, higher_is_better):
    if team not in values:
        return None
    ordered = sorted(values.values(), reverse=higher_is_better)
    return ordered.index(values[team]) + 1


def _r(x, digits=3):
    return None if x is None else round(float(x), digits)


def team_table(models, obs, teams, fbs_schools):
    """Adjusted vs raw offense/defense for each team in `teams` ({school: conference}).

    Ranks are among all FBS teams.
    """
    games = defaultdict(set)
    opp_off, opp_def = defaultdict(list), defaultdict(list)
    epa = models.get("epa")
    for o in obs:
        games[o["offense"]].add(o["game_id"])
        if epa:
            opp_def[o["offense"]].append(epa["def"].get(o["defense"]))
            opp_off[o["defense"]].append(epa["off"].get(o["offense"]))

    out = []
    for team in sorted(teams):
        row = {"team": team, "conference": teams[team], "games": len(games.get(team, ()))}
        for side, better_high in (("off", True), ("def", False)):
            for name, m in models.items():
                fbs_vals = {t: v for t, v in m[side].items() if t in fbs_schools}
                # Defense: lower is better for EPA/success/explosiveness allowed.
                row[f"{side}_{name}_raw"] = _r(m[f"raw_{side}"].get(team))
                row[f"{side}_{name}_adj"] = _r(m[side].get(team))
                row[f"{side}_{name}_adj_rank"] = _rank(fbs_vals, team, better_high)
        if epa:
            row["net_epa_adj"] = _r(epa["off"].get(team, 0) - epa["def"].get(team, 0))
            faced_def = [v for v in opp_def.get(team, []) if v is not None]
            faced_off = [v for v in opp_off.get(team, []) if v is not None]
            # Average adjusted EPA allowed by defenses faced: lower = tougher schedule for this offense.
            row["sos_opp_def_epa"] = _r(np.mean(faced_def)) if faced_def else None
            row["sos_opp_off_epa"] = _r(np.mean(faced_off)) if faced_off else None
        out.append(row)
    out.sort(key=lambda r: -(r.get("net_epa_adj") or -99))
    return out


def player_table(player_games, models, teams):
    """Per-player raw vs opponent-adjusted EPA/play for players on `teams`.

    Each game's EPA is shifted by how much better or worse than average the opponent's
    adjusted defense was in that phase (all/rush/pass), then games are averaged.
    """
    agg = {}
    for pg in player_games:
        if pg.get("team") not in teams:
            continue
        key = (pg.get("id") or pg.get("name"), pg["team"])
        p = agg.setdefault(key, {
            "player": pg.get("name"), "position": pg.get("position"), "team": pg["team"],
            "conference": teams[pg["team"]], "games": 0,
            **{f"{k}_{s}": [] for k in PLAYER_METRICS for s in ("raw", "adj")},
        })
        p["games"] += 1
        avg = pg.get("averagePPA") or {}
        for key_ppa, metric in PLAYER_METRICS.items():
            raw = avg.get(key_ppa)
            if raw is None:
                continue
            m = models.get(metric)
            opp_def = m["def"].get(pg.get("opponent")) if m else None
            # Facing a defense better than average (allows less EPA) earns a bonus.
            adj = raw if opp_def is None else raw + (m["intercept"] - opp_def)
            p[f"{key_ppa}_raw"].append(raw)
            p[f"{key_ppa}_adj"].append(adj)

    out = []
    for p in agg.values():
        row = {k: p[k] for k in ("player", "position", "team", "conference", "games")}
        for key_ppa in PLAYER_METRICS:
            raws, adjs = p[f"{key_ppa}_raw"], p[f"{key_ppa}_adj"]
            row[f"{key_ppa}_epa_raw"] = _r(np.mean(raws)) if raws else None
            row[f"{key_ppa}_epa_adj"] = _r(np.mean(adjs)) if adjs else None
            row[f"{key_ppa}_opp_adjustment"] = _r(np.mean(adjs) - np.mean(raws)) if raws else None
        out.append(row)
    out.sort(key=lambda r: -(r["all_epa_adj"] if r["all_epa_adj"] is not None else -99))
    return out
