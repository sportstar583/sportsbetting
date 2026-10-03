"""Collect season team stats for every Power 4 college football team.

Writes CSVs (one row per team) to data/<year>/:
  offense.csv        scoring, yardage, efficiency and advanced (EPA/success rate) offense
  defense.csv        points/yards allowed, sacks, takeaways and advanced defense
  special_teams.csv  kick and punt returns
  all_stats.csv      everything above in one wide table
  adjusted_team_epa.csv    opponent-adjusted EPA/success rate, offense and defense
  adjusted_player_epa.csv  opponent-adjusted EPA/play for every P4 player

Usage:
  export CFBD_API_KEY=...
  python -m cfb_stats.collect --year 2026
"""

import argparse
import csv
import datetime
import os
from collections import defaultdict

from . import adjust

P4_CONFERENCES = ("ACC", "Big 12", "Big Ten", "SEC")

# Raw stats from /stats/season that describe what a team's defense did.
DEFENSIVE_STATS = {
    "sacks",
    "tacklesForLoss",
    "passesIntercepted",
    "interceptionYards",
    "interceptionTDs",
    "fumblesRecovered",
    "passesDeflected",
    "qbHurries",
}
SPECIAL_TEAMS_STATS = {
    "kickReturns",
    "kickReturnYards",
    "kickReturnTDs",
    "puntReturns",
    "puntReturnYards",
    "puntReturnTDs",
}
OPPONENT_SUFFIX = "Opponent"


def default_year(today=None):
    today = today or datetime.date.today()
    # The season starts in late August; before that, the latest full season is last year's.
    return today.year if today.month >= 8 else today.year - 1


def upcoming_week(games, now=None):
    """First regular-season week with a game that hasn't kicked off yet.

    Uses kickoff time rather than the "completed" flag: postponed or canceled games stay
    "not completed" forever, and on Sunday morning a late Saturday game may not be marked
    final yet; neither should hold the card on a week that's already been played.
    """
    now = now or datetime.datetime.now(datetime.timezone.utc)
    cutoff = now
    weeks = []
    for g in games:
        if g.get("completed") or g.get("week") is None:
            continue
        start = g.get("startDate")
        try:
            kick = datetime.datetime.fromisoformat(start.replace("Z", "+00:00")) if start else None
        except ValueError:
            kick = None
        if kick is None or kick >= cutoff:
            weeks.append(g["week"])
    if not weeks:
        raise ValueError("no upcoming regular-season games found; pass --week")
    return min(weeks)


def p4_teams(fbs_teams, include_notre_dame=False):
    teams = {}
    for t in fbs_teams:
        if t.get("conference") in P4_CONFERENCES or (
            include_notre_dame and t.get("school") == "Notre Dame"
        ):
            teams[t["school"]] = t.get("conference") or "FBS Independents"
    return teams


def stat_side(name):
    """Return 'offense', 'defense' or 'special_teams' for a raw /stats/season stat name."""
    is_opp = name.endswith(OPPONENT_SUFFIX)
    base = name[: -len(OPPONENT_SUFFIX)] if is_opp else name
    if base in SPECIAL_TEAMS_STATS:
        return "special_teams"
    if base in DEFENSIVE_STATS:
        # e.g. sacksOpponent = sacks the opponents made = sacks our offense allowed
        return "offense" if is_opp else "defense"
    if base == "games":
        return "offense"
    # e.g. totalYardsOpponent = yards our defense allowed
    return "defense" if is_opp else "offense"


def pivot_season_stats(rows, teams):
    """Long (team, statName, statValue) rows -> {team: {statName: value}}."""
    out = defaultdict(dict)
    for r in rows:
        if r["team"] in teams:
            out[r["team"]][r["statName"]] = r["statValue"]
    return out


def flatten(obj, prefix):
    flat = {}
    for key, val in obj.items():
        name = f"{prefix}_{key}"
        if isinstance(val, dict):
            flat.update(flatten(val, name))
        else:
            flat[name] = val
    return flat


def flatten_advanced(rows, teams):
    """Nested advanced stats -> {team: {'off_successRate': .., 'def_ppa': .., ...}}."""
    out = {}
    for r in rows:
        if r["team"] in teams:
            flat = {}
            flat.update(flatten(r.get("offense") or {}, "adv_off"))
            flat.update(flatten(r.get("defense") or {}, "adv_def"))
            out[r["team"]] = flat
    return out


def _g(game, camel, snake):
    return game.get(camel, game.get(snake))


def scoring_from_games(games, teams):
    """Points for/against from completed games -> {team: {...}}."""
    totals = defaultdict(lambda: {"games_played": 0, "points_for": 0, "points_against": 0, "wins": 0, "losses": 0})
    for g in games:
        home, away = _g(g, "homeTeam", "home_team"), _g(g, "awayTeam", "away_team")
        hp, ap = _g(g, "homePoints", "home_points"), _g(g, "awayPoints", "away_points")
        if hp is None or ap is None or g.get("completed") is False:
            continue
        for team, pf, pa in ((home, hp, ap), (away, ap, hp)):
            if team in teams:
                t = totals[team]
                t["games_played"] += 1
                t["points_for"] += pf
                t["points_against"] += pa
                t["wins"] += pf > pa
                t["losses"] += pf < pa
    return totals


def _div(a, b, digits=3):
    try:
        return round(float(a) / float(b), digits) if b else None
    except (TypeError, ValueError):
        return None


def derived_offense(s, sc, games):
    plays = (s.get("rushingAttempts") or 0) + (s.get("passAttempts") or 0)
    return {
        "points_per_game": _div(sc.get("points_for"), sc.get("games_played"), 1),
        "yards_per_game": _div(s.get("totalYards"), games, 1),
        "yards_per_play": _div(s.get("totalYards"), plays, 2),
        "rush_yards_per_game": _div(s.get("rushingYards"), games, 1),
        "yards_per_rush": _div(s.get("rushingYards"), s.get("rushingAttempts"), 2),
        "pass_yards_per_game": _div(s.get("netPassingYards"), games, 1),
        "yards_per_pass_att": _div(s.get("netPassingYards"), s.get("passAttempts"), 2),
        "completion_pct": _div(s.get("passCompletions"), s.get("passAttempts")),
        "third_down_pct": _div(s.get("thirdDownConversions"), s.get("thirdDowns")),
        "fourth_down_pct": _div(s.get("fourthDownConversions"), s.get("fourthDowns")),
        "turnovers_per_game": _div(s.get("turnovers"), games, 2),
        "sacks_allowed_per_game": _div(s.get("sacksOpponent"), games, 2),
    }


def derived_defense(s, sc, games):
    o = lambda k: s.get(k + OPPONENT_SUFFIX)  # noqa: E731
    opp_plays = (o("rushingAttempts") or 0) + (o("passAttempts") or 0)
    # turnoversOpponent is the API's own takeaway count; passesIntercepted + fumblesRecovered
    # can undercount it, so only fall back to that sum when it is missing.
    takeaways = o("turnovers")
    if takeaways is None and ("passesIntercepted" in s or "fumblesRecovered" in s):
        takeaways = (s.get("passesIntercepted") or 0) + (s.get("fumblesRecovered") or 0)
    return {
        "points_allowed_per_game": _div(sc.get("points_against"), sc.get("games_played"), 1),
        "yards_allowed_per_game": _div(o("totalYards"), games, 1),
        "yards_per_play_allowed": _div(o("totalYards"), opp_plays, 2),
        "rush_yards_allowed_per_game": _div(o("rushingYards"), games, 1),
        "yards_per_rush_allowed": _div(o("rushingYards"), o("rushingAttempts"), 2),
        "pass_yards_allowed_per_game": _div(o("netPassingYards"), games, 1),
        "yards_per_pass_att_allowed": _div(o("netPassingYards"), o("passAttempts"), 2),
        "third_down_pct_allowed": _div(o("thirdDownConversions"), o("thirdDowns")),
        "sacks_per_game": _div(s.get("sacks"), games, 2),
        "tfl_per_game": _div(s.get("tacklesForLoss"), games, 2),
        "takeaways": takeaways,
        "turnover_margin": None if takeaways is None or "turnovers" not in s else takeaways - s["turnovers"],
    }


def build_tables(teams, season_rows, advanced_rows, games):
    stats = pivot_season_stats(season_rows, teams)
    adv = flatten_advanced(advanced_rows, teams)
    scoring = scoring_from_games(games, teams)

    tables = {"offense": [], "defense": [], "special_teams": [], "all_stats": []}
    for team in sorted(teams):
        s, sc = stats.get(team, {}), scoring.get(team, {})
        n_games = s.get("games") or sc.get("games_played")
        base = {
            "team": team,
            "conference": teams[team],
            "games": n_games,
            "wins": sc.get("wins"),
            "losses": sc.get("losses"),
        }
        off = dict(base, points_for=sc.get("points_for"), **derived_offense(s, sc, n_games))
        dfn = dict(base, points_against=sc.get("points_against"), **derived_defense(s, sc, n_games))
        st = dict(base)
        sides = {"offense": off, "defense": dfn, "special_teams": st}
        for name in sorted(s):
            if name != "games":
                sides[stat_side(name)][name] = s[name]
        for key, val in sorted(adv.get(team, {}).items()):
            (off if key.startswith("adv_off_") else dfn)[key] = val

        tables["offense"].append(off)
        tables["defense"].append(dfn)
        tables["special_teams"].append(st)
        tables["all_stats"].append({**off, **dfn, **st})
    return tables


def played_weeks(games):
    """Sorted (seasonType, week) pairs that have at least one completed game."""
    weeks = {
        (g["seasonType"], g["week"])
        for g in games
        if g.get("week") is not None and _g(g, "homePoints", "home_points") is not None
    }
    return sorted(weeks, key=lambda sw: (sw[0] != "regular", sw[1]))


def write_csv(path, rows):
    columns = []
    for r in rows:
        columns.extend(k for k in r if k not in columns)
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=columns)
        w.writeheader()
        w.writerows(rows)


def main(argv=None):
    from .api import CFBDClient

    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--year", type=int, default=default_year())
    p.add_argument("--out", default="data")
    p.add_argument("--api-key", default=None)
    p.add_argument("--include-notre-dame", action="store_true")
    p.add_argument("--include-garbage-time", action="store_true", help="include garbage time in advanced stats")
    p.add_argument("--alpha", type=float, default=adjust.DEFAULT_ALPHA,
                   help="ridge penalty for opponent adjustment; higher shrinks more toward average")
    args = p.parse_args(argv)

    client = CFBDClient(api_key=args.api_key)
    fbs = client.fbs_teams(args.year)
    teams = p4_teams(fbs, args.include_notre_dame)
    print(f"{len(teams)} P4 teams for {args.year}")

    season_rows = client.season_stats(args.year)
    advanced_rows = client.advanced_season_stats(args.year, not args.include_garbage_time)
    games = []
    for season_type in ("regular", "postseason"):
        games += [dict(g, seasonType=season_type) for g in client.games(args.year, season_type)]

    tables = build_tables(teams, season_rows, advanced_rows, games)

    # Opponent adjustment uses every FBS game, not just P4 ones, so opponents are rated fairly.
    exclude_gt = not args.include_garbage_time
    game_rows, player_games = [], []
    for season_type, week in played_weeks(games):
        game_rows += client.game_advanced_stats(args.year, week, season_type, exclude_gt)
        player_games += client.player_game_ppa(args.year, week, season_type, exclude_gt)
    models, obs = adjust.fit_all(game_rows, games, args.alpha)
    if "epa" in models:
        print(f"home field advantage: {models['epa']['hfa']:+.3f} EPA/play")
    fbs_schools = {t["school"] for t in fbs}
    tables["adjusted_team_epa"] = adjust.team_table(models, obs, teams, fbs_schools)
    tables["adjusted_player_epa"] = adjust.player_table(player_games, models, teams)

    out_dir = os.path.join(args.out, str(args.year))
    os.makedirs(out_dir, exist_ok=True)
    for name, rows in tables.items():
        path = os.path.join(out_dir, f"{name}.csv")
        write_csv(path, rows)
        print(f"wrote {path} ({len(rows)} rows)")


if __name__ == "__main__":
    main()
