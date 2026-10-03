"""Preseason priors: start each team from an informed rating instead of league average.

Early in the season a team's rating rests on 3-4 games, and the ridge fit pulls every team
toward average. With priors it pulls each team toward its own preseason estimate instead:

  offense prior = a * last season's final rating + b * last season x returning production
                  + c * roster talent
  defense prior = a * last season's final rating + c * roster talent

for overall, rush and pass EPA. Returning production (CFBD /player/returning, share of last
season's offensive PPA that came back) only exists for offense. Talent is the 247 team talent
composite (CFBD /talent), as a z-score among the season's teams. The weights are fit by
regressing each season's final ratings on the previous season's predictors.

Everything that doesn't change once a season starts (final ratings, returning production,
talent) is saved to data/priors/ so the weekly runs don't spend API calls on it.

Usage:
  python -m cfb_stats.priors --backtest            # leave-one-season-out test, 2023-2025
  python -m cfb_stats.priors --build 2026          # save the priors the weekly runs use
"""

import argparse
import csv
import json
import math
import os
import statistics

import numpy as np

from . import adjust

METRICS = ("epa", "rush_epa", "pass_epa")
USE_COACHES = True  # new-head-coach term in the priors; see BACKTEST.md
USE_TRANSFERS = False  # net transfer-portal talent term; see BACKTEST.md
RATING_ALPHA = 150.0  # full-season "final" ratings: plenty of data, light shrinkage
PRIOR_DIR = os.path.join("data", "priors")


def _cached_csv(path, fetch, fields):
    if os.path.exists(path):
        with open(path, newline="") as f:
            return list(csv.DictReader(f))
    rows = [{k: r.get(k) for k in fields} for r in fetch()]
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)
    return rows


def returning(client, year, out_dir=PRIOR_DIR):
    """{team: share of last season's offensive PPA returning (0-1)}."""
    rows = _cached_csv(os.path.join(out_dir, f"returning_{year}.csv"),
                       lambda: client.get("/player/returning", year=year),
                       ["team", "percentPPA", "percentPassingPPA", "percentRushingPPA", "percentReceivingPPA", "usage"])
    return {r["team"]: float(r["percentPPA"]) for r in rows if r.get("percentPPA") not in (None, "")}


def talent(client, year, out_dir=PRIOR_DIR):
    """{team: talent z-score within the season}."""
    rows = _cached_csv(os.path.join(out_dir, f"talent_{year}.csv"),
                       lambda: client.get("/talent", year=year), ["team", "talent"])
    vals = {r["team"]: float(r["talent"]) for r in rows if r.get("talent") not in (None, "")}
    if len(vals) < 2:
        return {}
    mu, sd = statistics.mean(vals.values()), statistics.pstdev(vals.values()) or 1.0
    return {t: (v - mu) / sd for t, v in vals.items()}


def season_ratings(client, year, out_dir=PRIOR_DIR):
    """Final regular-season ratings {metric: {"off"|"def": {team: deviation from average}}}."""
    path = os.path.join(out_dir, f"ratings_{year}.json")
    if os.path.exists(path):
        with open(path) as f:
            return json.load(f)
    games = [dict(g, seasonType="regular") for g in client.games(year, "regular")]
    weeks = sorted({g["week"] for g in games if g.get("completed")})
    rows = [r for w in weeks for r in client.game_advanced_stats(year, w, "regular", True)]
    fbs = {g[f"{s}Team"] for g in games for s in ("home", "away") if g.get(f"{s}Classification") == "fbs"}
    weight = lambda o: 1.0 if o["offense"] in fbs and o["defense"] in fbs else 0.5  # noqa: E731
    models, _ = adjust.fit_all(rows, [g for g in games if g.get("completed")], RATING_ALPHA, weight,
                               huber_k=1.5, subset_alpha_scale=0.5)
    out = {}
    for m in METRICS:
        i = models[m]["intercept"]
        out[m] = {side: {t: v - i for t, v in models[m][side].items() if t in fbs} for side in ("off", "def")}
    from .collect import default_year
    if year < default_year():  # only save finished seasons (a few never-played games stay "not completed")
        os.makedirs(out_dir, exist_ok=True)
        with open(path, "w") as f:
            json.dump(out, f)
    return out


def coaches(client, out_dir=PRIOR_DIR, first=2021, last=None):
    """{(team, year): primary head coach} (most games that season), from one CFBD /coaches call."""
    from .collect import default_year
    last = last or default_year()
    path = os.path.join(out_dir, f"coaches_{first}_{last}.csv")
    if not os.path.exists(path):
        rows = []
        for c in client.get("/coaches", minYear=first, maxYear=last):
            for s in c.get("seasons") or []:
                rows.append({"team": s["school"], "year": s["year"], "coach": f"{c['firstName']} {c['lastName']}",
                             "games": s.get("games") or 0})
        os.makedirs(out_dir, exist_ok=True)
        with open(path, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=["team", "year", "coach", "games"])
            w.writeheader()
            w.writerows(rows)
    with open(path, newline="") as f:
        rows = list(csv.DictReader(f))
    best = {}
    for r in rows:
        key, g = (r["team"], int(r["year"])), int(r["games"] or 0)
        if key not in best or g > best[key][1]:
            best[key] = (r["coach"], g)
    return {k: v[0] for k, v in best.items()}


def new_coach_teams(coach_map, year):
    """Teams whose head coach for `year` differs from last season's primary head coach."""
    return {t for (t, y), c in coach_map.items() if y == year and coach_map.get((t, y - 1)) not in (None, c)}


OFFENSE_POS = {"QB", "RB", "FB", "WR", "TE", "OL", "OT", "OG", "C", "IOL", "ATH"}
STAR_RATING = {2: 0.78, 3: 0.84, 4: 0.90, 5: 0.97}  # fallback when a transfer has stars but no rating


def portal(client, year, out_dir=PRIOR_DIR):
    """Transfer portal entries for players moving before `year`'s season (CFBD /player/portal)."""
    return _cached_csv(os.path.join(out_dir, f"portal_{year}.csv"),
                       lambda: client.get("/player/portal", year=year),
                       ["season", "firstName", "lastName", "position", "origin", "destination", "rating", "stars"])


def transfer_net(rows):
    """{team: {"off": z, "def": z}}: talent gained minus lost through the portal, z-scored.

    A transfer's value is how far his transfer rating sits above a typical 3-star (0.80),
    floored at zero, so a few good transfers outweigh many depth players."""
    net = {}
    for r in rows:
        try:
            rating = float(r["rating"]) if r.get("rating") not in (None, "") else \
                STAR_RATING.get(int(float(r.get("stars") or 0)), 0.0)
        except ValueError:
            continue
        value = max(rating - 0.80, 0.0)
        if not value:
            continue
        side = "off" if (r.get("position") or "").upper() in OFFENSE_POS else "def"
        for team, sign in ((r.get("destination"), 1), (r.get("origin"), -1)):
            if team:
                net.setdefault(team, {"off": 0.0, "def": 0.0})[side] += sign * value
    out = {t: {} for t in net}
    for side in ("off", "def"):
        vals = [v[side] for v in net.values()]
        mu, sd = statistics.mean(vals), statistics.pstdev(vals) or 1.0
        for t, v in net.items():
            out[t][side] = (v[side] - mu) / sd
    return out


def features(prev, ret, tal, metric, side, team, new=None, trans=None):
    """new: set of teams with a new head coach; trans: transfer_net() (None leaves a term out)."""
    p = prev.get(metric, {}).get(side, {}).get(team, 0.0)
    t = tal.get(team, 0.0)
    extra = [] if new is None else [p * (team in new)]
    if trans is not None:
        extra.append(trans.get(team, {}).get(side, 0.0))
    if side == "off":
        r = ret.get(team, 0.5)
        return [p, p * (r - 0.5), t] + extra
    return [p, t] + extra


def fit_weights(pairs):
    """pairs: [(prev ratings, returning, talent, this season's ratings)] -> {(metric, side): coefs}."""
    coefs = {}
    for m in METRICS:
        for side in ("off", "def"):
            X, y = [], []
            for prev, ret, tal, new, trans, cur in pairs:
                for team, val in cur[m][side].items():
                    X.append(features(prev, ret, tal, m, side, team, new, trans))
                    y.append(val)
            coefs[(m, side)] = np.linalg.lstsq(np.array(X), np.array(y), rcond=None)[0].tolist()
    return coefs


def make_priors(prev, ret, tal, coefs, teams, new=None, trans=None):
    return {m: {side: {t: float(np.dot(coefs[(m, side)], features(prev, ret, tal, m, side, t, new, trans)))
                       for t in teams}
                for side in ("off", "def")} for m in METRICS}


def season_inputs(client, year, use_coaches=False, use_transfers=False):
    new = new_coach_teams(coaches(client), year) if use_coaches else None
    trans = transfer_net(portal(client, year)) if use_transfers else None
    return season_ratings(client, year - 1), returning(client, year), talent(client, year), new, trans


def build(client, year, train_years, use_coaches=USE_COACHES, use_transfers=None):
    """Priors for `year`, with weights fit on (y-1 -> y) for each y in train_years."""
    use_transfers = USE_TRANSFERS if use_transfers is None else use_transfers
    pairs = [(*season_inputs(client, y, use_coaches, use_transfers), season_ratings(client, y)) for y in train_years]
    coefs = fit_weights(pairs)
    prev, ret, tal, new, trans = season_inputs(client, year, use_coaches, use_transfers)
    teams = set(tal) | set(prev.get("epa", {}).get("off", {}))
    return make_priors(prev, ret, tal, coefs, teams, new, trans), coefs


def save_priors(priors, coefs, year, out_dir=PRIOR_DIR):
    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, f"priors_{year}.json"), "w") as f:
        json.dump({"priors": priors, "weights": {f"{m}|{s}": c for (m, s), c in coefs.items()}}, f, indent=1)


def load_priors(year, out_dir=PRIOR_DIR):
    path = os.path.join(out_dir, f"priors_{year}.json")
    if not os.path.exists(path):
        return None
    with open(path) as f:
        return json.load(f)["priors"]


def backtest(client, years=(2023, 2024, 2025), first_week=4):
    """Leave-one-season-out: weights fit on the other seasons, walk-forward totals backtest."""
    from .totals import TOTALS_ALPHA, backtest as totals_backtest, load_season, _record, weekly_card

    base = {"alpha": TOTALS_ALPHA, "tempo": True, "fcs_weight": 0.5, "huber_k": 1.5, "matchup": 1.0}
    variants = {"current": base, "priors": dict(base, use_priors=True)}
    out = []
    for y in years:
        priors, coefs = build(client, y, [t for t in years if t != y])
        data = load_season(client, y)
        data["priors"] = priors
        res = totals_backtest(data, first_week=first_week, variants=variants)
        for name, rows in res.items():
            for label, lo, hi in (("weeks 4-6", 4, 6), ("weeks 7-9", 7, 9), ("weeks 10+", 10, 99), ("all", 0, 99)):
                rs = [r for r in rows if lo <= r["week"] <= hi]
                if not rs:
                    continue
                rmse = math.sqrt(statistics.mean((r["actual_total"] - r["proj_total"]) ** 2 for r in rs))
                w3, l3, _ = _record(rs, 3)
                row = {"season": y, "variant": name, "weeks": label, "games": len(rs), "rmse": round(rmse, 2),
                       "edge3_w": w3, "edge3_l": l3}
                if label == "all":
                    cw = cl = 0
                    for r, side in weekly_card(rs, 3, p4_only=True):
                        d = r["actual_total"] - r["market_total"]
                        if d:
                            cw += (d > 0) == (side == "OVER")
                            cl += (d > 0) != (side == "OVER")
                    row.update({"card_w": cw, "card_l": cl})
                out.append(row)
                print(row, flush=True)
    return out


def main(argv=None):
    from .api import CFBDClient
    from .collect import write_csv

    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--backtest", action="store_true")
    p.add_argument("--build", type=int, default=None, help="season to build priors for")
    p.add_argument("--years", default="2023,2024,2025")
    p.add_argument("--api-key", default=None)
    p.add_argument("--cache", default=None)
    args = p.parse_args(argv)
    client = CFBDClient(api_key=args.api_key, cache_dir=args.cache)
    years = tuple(int(y) for y in args.years.split(","))
    if args.backtest:
        write_csv(os.path.join("data", "priors_backtest.csv"), backtest(client, years))
    if args.build:
        priors, coefs = build(client, args.build, [y for y in years if y < args.build])
        save_priors(priors, coefs, args.build)
        print(f"saved priors for {args.build}; weights:")
        for (m, s), c in coefs.items():
            print(f"  {m} {s}: {[round(x, 3) for x in c]}")


if __name__ == "__main__":
    main()
