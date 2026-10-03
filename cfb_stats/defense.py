"""Calibrate the defensive injury scale (cfb_stats.injuries.DEF_SCALE) from who actually played.

There are no historical availability reports, so, like the QB test in cfb_stats.totals, this
uses box scores after the fact: a defender with a real share of his team's season-to-date
production (tackles, TFL, sacks, pass breakups, INTs, hurries; see injuries.DEF_WEIGHTS) who
records nothing in a game is treated as out for it. Starters at that share almost never
finish a game without a tackle, so the false-positive rate is low. Suspensions and benchings
are counted too; the question is what happens when the player sits, not why.

For each week from first_week on, the model is built from earlier weeks only, once healthy
and once with the missing shares applied at scale 1.0. The difference is the points the
adjustment would add per unit of DEF_SCALE, and the slope of (actual - healthy projection)
on it is the calibrated scale. Games where a team's season-to-date starting QB had no
attempts are dropped, so the QB effect doesn't contaminate the defensive one. Leave-one-
season-out: the scale is fit on two seasons and tested on the third.

Result (BACKTEST.md): the fitted scale is 0.04 counting every regular with 3%+ of team
production and 0.08 counting starters with 5%+ (the lower floor adds quiet games as false
absences, which pulls the slope toward zero). DEF_SCALE is set to 0.08. Accuracy and betting
results barely move either way: the adjustment is usually under 2 points.

Usage:
  python -m cfb_stats.defense --backtest --cache .cfbd_cache
"""

import os
from collections import defaultdict

import numpy as np

from . import injuries as inj

MIN_SHARE = 0.03  # a defender below this share of team production is depth; ignore
MIN_APPEARANCES = 0.6  # ...and he must have had a stat line in this share of his team's boxed games


def box_defense_rows(box_games):
    """/games/players box scores -> rows shaped like /stats/player/season (team, playerId,
    player, category, statType, stat), defensive and interceptions categories only."""
    rows = []
    for g in box_games:
        for t in g.get("teams") or []:
            for cat in t.get("categories") or []:
                if cat.get("name") not in ("defensive", "interceptions"):
                    continue
                for typ in cat.get("types") or []:
                    for a in typ.get("athletes") or []:
                        rows.append({"gameId": g["id"], "team": t["team"], "playerId": a["id"], "player": a["name"],
                                     "category": cat["name"], "statType": typ["name"], "stat": a["stat"]})
    return rows


def defenders_in_game(box_games, min_lines=11):
    """{(game id, team): set of player ids with a defensive stat line}, only for teams whose
    box has a full defense (min_lines tackle lines); some boxes carry no defensive stats."""
    seen = defaultdict(set)
    for r in box_defense_rows(box_games):
        if r["category"] == "defensive":
            seen[(r["gameId"], r["team"])].add(r["playerId"])
    return {k: v for k, v in seen.items() if len(v) >= min_lines}


def starting_qbs(box_games):
    """{team: player id} season-to-date starter by pass attempts (needs 2+ games of 10+ attempts)."""
    att, games = defaultdict(int), defaultdict(int)
    for g in box_games:
        for t in g.get("teams") or []:
            for cat in t.get("categories") or []:
                if cat.get("name") != "passing":
                    continue
                for typ in cat.get("types") or []:
                    if typ.get("name") != "C/ATT":
                        continue
                    for a in typ.get("athletes") or []:
                        try:
                            n = int(str(a["stat"]).split("/")[1])
                        except (IndexError, ValueError):
                            continue
                        att[(t["team"], a["id"])] += n
                        games[(t["team"], a["id"])] += n >= 10
    best = {}
    for (team, pid), n in att.items():
        if games[(team, pid)] >= 2 and n > best.get(team, (None, 0))[1]:
            best[team] = (pid, n)
    return {team: pid for team, (pid, _) in best.items()}


def qb_played(box_games):
    """{(game id, team): set of player ids with a pass attempt}."""
    seen = defaultdict(set)
    for g in box_games:
        for t in g.get("teams") or []:
            for cat in t.get("categories") or []:
                if cat.get("name") != "passing":
                    continue
                for typ in cat.get("types") or []:
                    if typ.get("name") != "C/ATT":
                        continue
                    for a in typ.get("athletes") or []:
                        try:
                            if int(str(a["stat"]).split("/")[1]) >= 5:
                                seen[(g["id"], t["team"])].add(a["id"])
                        except (IndexError, ValueError):
                            continue
    return seen


def missing_defenders(prior_box, week_box, games, min_share=MIN_SHARE, min_appearances=MIN_APPEARANCES):
    """-> {team: missing share}, {game id: [notes]} for a week's games.

    prior_box: box scores from earlier weeks (season to date); week_box: this week's. A
    defender counts when his share is at least min_share and he had a stat line in at least
    min_appearances of his team's boxed games so far (a regular, not a rotational player)."""
    values = inj.defender_values(box_defense_rows(prior_box))
    appearances, team_games = defaultdict(int), defaultdict(int)
    for (gid, team), pids in defenders_in_game(prior_box).items():
        team_games[team] += 1
        for pid in pids:
            appearances[(team, pid)] += 1
    by_team = defaultdict(list)
    for (team, key), v in values.items():
        if not key.startswith("id:") or v["share"] < min_share:
            continue
        pid = key[3:]
        if team_games[team] and appearances[(team, pid)] / team_games[team] >= min_appearances:
            by_team[team].append((pid, v["share"]))
    played = defenders_in_game(week_box)
    shares, notes = defaultdict(float), defaultdict(list)
    for g in games:
        for team in (g["homeTeam"], g["awayTeam"]):
            if (g["id"], team) not in played:
                continue  # no defensive box score for this team: can't tell who sat
            for pid, share in by_team.get(team, []):
                if pid not in played[(g["id"], team)]:
                    shares[team] += share
                    notes[g["id"]].append(f"{team}: {pid} ({share:.1%})")
    return dict(shares), dict(notes)


def load_seasons(client, years):
    """{year: data} with games, advanced-stat rows, drives, lines and box scores per week."""
    from .totals import load_season_lite
    return {y: load_season_lite(client, y) for y in years}


def backtest_rows(data, year, first_week=4, min_games=3, model_kw=None, min_share=MIN_SHARE):
    """One row per game with a market total and a result: healthy projection, the points the
    defensive adjustment adds at scale 1.0 (adj_unit), and the missing shares."""
    from .totals import board, build_model, _before
    model_kw = model_kw or {}
    out = []
    for w in data["weeks"]:
        if w < first_week:
            continue
        prior_box = _before(data, "box", w)
        week_box = data["box"].get(w, [])
        wk_games = [g for g in data["games"] if g.get("week") == w]
        shares, notes = missing_defenders(prior_box, week_box, wk_games, min_share)
        starters, threw = starting_qbs(prior_box), qb_played(week_box)
        qb_out = {g["id"] for g in wk_games for t in (g["homeTeam"], g["awayTeam"])
                  if t in starters and (g["id"], t) in threw and starters[t] not in threw[(g["id"], t)]}
        model = build_model(data, w, def_offsets=shares, **model_kw)
        for r in board(model, wk_games, data["lines"][w], min_games):
            if r["actual_total"] is None or not r["enough_data"]:
                continue
            out.append({
                "season": year, "week": w, "game_id": r["game_id"], "away": r["away"], "home": r["home"],
                "p4_game": r["p4_game"], "market_total": r["market_total"],
                "proj_healthy": round(r["proj_total"] - r["injury_adj"], 1),
                "adj_unit": r["injury_adj"],  # points added per 1.0 of DEF_SCALE
                "share_home": round(shares.get(r["home"], 0.0), 3), "share_away": round(shares.get(r["away"], 0.0), 3),
                "actual_total": r["actual_total"], "qb_out": r["game_id"] in qb_out,
                "missing": "; ".join(notes.get(r["game_id"], [])),
            })
    return out


def fit_scale(rows):
    """Least-squares slope of (actual - healthy) on adj_unit, with an intercept for model bias."""
    x = np.array([r["adj_unit"] for r in rows], dtype=float)
    y = np.array([r["actual_total"] - r["proj_healthy"] for r in rows], dtype=float)
    A = np.column_stack([np.ones(len(x)), x])
    coef = np.linalg.lstsq(A, y, rcond=None)[0]
    return float(coef[1]), float(coef[0])


def evaluate(rows, years, current=inj.DEF_SCALE, seed=0):
    """Leave-one-season-out: fit the scale on the other seasons, score this one. -> summary rows"""
    from .totals import weekly_card
    rows = [r for r in rows if not r["qb_out"]]
    season = np.array([r["season"] for r in rows])

    def record(rs):
        w = l = 0
        for r, side in rs:
            d = r["actual_total"] - r["market_total"]
            if d:
                w += (d > 0) == (side == "OVER")
                l += (d > 0) != (side == "OVER")
        return w, l

    def scored(rs, scale):
        out = []
        for r in rs:
            proj = r["proj_healthy"] + scale * r["adj_unit"]
            out.append({**r, "proj_total": proj, "edge": proj - r["market_total"],
                        "week": (r["season"], r["week"]), "enough_data": True})
        return out

    rm = lambda rs: float(np.sqrt(np.mean([(r["actual_total"] - r["proj_total"]) ** 2 for r in rs])))  # noqa: E731
    out = []
    print(f"{len(rows)} games (starting-QB-out games dropped), "
          f"{sum(1 for r in rows if r['adj_unit'])} with a notable defender missing")
    print("season  fit scale   RMSE healthy / current / fit   edge>=3 healthy -> fit   P4 card healthy -> fit")
    for y in years:
        train = [r for r in rows if r["season"] != y]
        test = [r for r in rows if r["season"] == y]
        if not train or not test:
            continue
        scale, _ = fit_scale(train)
        res = {"season": y, "games": len(test), "fit_scale": round(scale, 3),
               "games_with_missing": sum(1 for r in test if r["adj_unit"])}
        for name, s in (("healthy", 0.0), ("current", current), ("fit", scale)):
            rs = scored(test, s)
            e3 = record([(r, "OVER" if r["edge"] > 0 else "UNDER") for r in rs if abs(r["edge"]) >= 3])
            card = record(weekly_card(rs, 3, p4_only=True))
            res.update({f"rmse_{name}": round(rm(rs), 2), f"edge3_{name}_w": e3[0], f"edge3_{name}_l": e3[1],
                        f"card_{name}_w": card[0], f"card_{name}_l": card[1]})
        print(f"{y}  {scale:+9.3f}   {res['rmse_healthy']:.2f} / {res['rmse_current']:.2f} / {res['rmse_fit']:.2f}"
              f"        {res['edge3_healthy_w']}-{res['edge3_healthy_l']} -> {res['edge3_fit_w']}-{res['edge3_fit_l']}"
              f"            {res['card_healthy_w']}-{res['card_healthy_l']} -> {res['card_fit_w']}-{res['card_fit_l']}")
        out.append(res)
    scale, bias = fit_scale(rows)
    rng = np.random.default_rng(seed)
    boots = []
    for _ in range(500):
        idx = rng.integers(0, len(rows), len(rows))
        boots.append(fit_scale([rows[i] for i in idx])[0])
    lo, hi = np.percentile(boots, [5, 95])
    print(f"all seasons: scale {scale:+.3f} (90% bootstrap {lo:+.3f} to {hi:+.3f}), model bias {bias:+.2f} pts")
    x = np.array([r["adj_unit"] for r in rows])
    mk = np.array([r["actual_total"] - r["market_total"] for r in rows])
    resid = np.array([r["actual_total"] - r["proj_healthy"] for r in rows])
    for lo_u, hi_u in ((0, 0.001), (0.001, 10), (10, 25), (25, 999)):
        m = (x >= lo_u) & (x < hi_u)
        if m.sum():
            print(f"adj_unit {lo_u:g}-{hi_u:g} pts: {int(m.sum())} games, actual - healthy model {resid[m].mean():+.1f}, "
                  f"actual - market {mk[m].mean():+.1f}")
    out.append({"season": "all", "games": len(rows), "fit_scale": round(scale, 3),
                "games_with_missing": int((x > 0).sum()), "boot_lo": round(float(lo), 3), "boot_hi": round(float(hi), 3)})
    return out


def main(argv=None):
    import argparse
    from .api import CFBDClient
    from .collect import write_csv
    from .totals import DEFAULT_FCS_WEIGHT, DEFAULT_HUBER_K, DEFAULT_MATCHUP, TOTALS_ALPHA
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--backtest", action="store_true")
    p.add_argument("--years", default="2023,2024,2025")
    p.add_argument("--api-key", default=None)
    p.add_argument("--cache", default=None)
    p.add_argument("--out", default="data")
    p.add_argument("--min-share", default="0.03,0.05",
                   help="production share floors to test, comma separated (per-game CSV uses the last)")
    args = p.parse_args(argv)
    if not args.backtest:
        p.print_help()
        return
    years = tuple(int(y) for y in args.years.split(","))
    client = CFBDClient(api_key=args.api_key, cache_dir=args.cache)
    chosen = {"alpha": TOTALS_ALPHA, "tempo": True, "fcs_weight": DEFAULT_FCS_WEIGHT,
              "huber_k": DEFAULT_HUBER_K, "matchup": DEFAULT_MATCHUP}
    seasons = load_seasons(client, years)
    summary = []
    for floor in (float(x) for x in args.min_share.split(",")):
        print(f"\n--- defenders with at least {floor:.0%} of team production")
        rows = []
        for y, data in seasons.items():
            rows += backtest_rows(data, y, model_kw=chosen, min_share=floor)
        summary += [{"min_share": floor, **r} for r in evaluate(rows, years)]
    write_csv(os.path.join(args.out, "defense_backtest.csv"), summary)
    write_csv(os.path.join(args.out, "defense_backtest_games.csv"), rows)


if __name__ == "__main__":
    main()
