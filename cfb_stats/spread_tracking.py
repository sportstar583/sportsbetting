"""Spread angles tracked on paper: logged each week and graded against the closing line and the
final score, never bet. None beat the closing spread in the 2023-2025 backtest (see BACKTEST.md);
this is to see whether any does in 2026.

Angles:
- model: the spread model's 3 biggest disagreements with the line among Power 4 games (with
  whether SP+ is on the same side).
- coach vs top 10: a team facing an AP top-10 opponent whose head coach, over his previous 3
  seasons at any school, covered 55%+ of such games (shrunk toward .500 by 10 games) or won half
  or more of them. 25-17 and 28-17 in 2023-2025, small samples picked from many tests.

Coach records are built once per season from CFBD (games, lines and AP polls for the 3 previous
seasons: 9 API calls) and saved to data/priors/coach_vs_top10_<year>.csv.
"""

import csv
import os

from .priors import PRIOR_DIR

MODEL_PICKS = 3
TIER = 10
MIN_GAMES = 4


def _ap_by_week(client, year):
    out = {}
    for w in client.get("/rankings", year=year):
        if w.get("seasonType") == "regular":
            for p in w.get("polls") or []:
                if p.get("poll") == "AP Top 25":
                    out[w["week"]] = {r["school"]: r["rank"] for r in p["ranks"]}
    return out


def _entering(polls, week):
    return next((polls[w] for w in range(week, 0, -1) if w in polls), {})


def ap_ranks(client, year, week):
    """{team: AP rank} entering `week` (1 API call)."""
    return _entering(_ap_by_week(client, year), week)


def coach_vs_top10(client, year, coach_map, out_dir=PRIOR_DIR):
    """{coach: (straight-up wins, ATS covers, games)} against AP top-10 teams, previous 3 seasons."""
    path = os.path.join(out_dir, f"coach_vs_top{TIER}_{year}.csv")
    if not os.path.exists(path):
        from .totals import consensus_spread
        rec = {}
        for y in range(year - 3, year):
            spreads = {lr["id"]: consensus_spread(lr)[0] for lr in client.get("/lines", year=y, seasonType="regular")}
            polls = _ap_by_week(client, y)
            for g in client.games(y, "regular"):
                spread = spreads.get(g["id"])
                if spread is None or g.get("homePoints") is None or g.get("awayPoints") is None:
                    continue
                margin = g["homePoints"] - g["awayPoints"]
                if margin + spread == 0:
                    continue
                ranks = _entering(polls, g["week"])
                for team, opp, sign in ((g["homeTeam"], g["awayTeam"], 1), (g["awayTeam"], g["homeTeam"], -1)):
                    coach = coach_map.get((team, y))
                    if coach and ranks.get(opp, 99) <= TIER:
                        su, ats, n = rec.get(coach, (0, 0, 0))
                        rec[coach] = (su + int(sign * margin > 0), ats + int(sign * (margin + spread) > 0), n + 1)
        os.makedirs(out_dir, exist_ok=True)
        with open(path, "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["coach", "su_wins", "ats_covers", "games"])
            w.writerows([c, *v] for c, v in sorted(rec.items()))
    with open(path, newline="") as f:
        return {r["coach"]: (int(r["su_wins"]), int(r["ats_covers"]), int(r["games"])) for r in csv.DictReader(f)}


def _num(r, k):
    v = r.get(k)
    return None if v in (None, "") else float(v)


def model_picks(rows, n=MODEL_PICKS):
    out = []
    ok = [r for r in rows if str(r.get("enough_data")) == "True" and _num(r, "spread_edge") is not None]
    for r in sorted(ok, key=lambda r: -abs(_num(r, "spread_edge")))[:n]:
        edge, spread = _num(r, "spread_edge"), _num(r, "market_spread")
        side = r["home"] if edge > 0 else r["away"]
        sp = _num(r, "sp_margin")
        sp_note = "" if sp is None else ("; SP+ agrees" if (sp + spread > 0) == (edge > 0) else "; SP+ disagrees")
        out.append(_pick(r, "model", side, f"model margin {_num(r, 'proj_margin'):+.1f}, edge {edge:+.1f}{sp_note}"))
    return out


def coach_angle(rows, ranks, coach_map, records, year):
    out = []
    for r in rows:
        if _num(r, "market_spread") is None:
            continue
        game = []
        for team, opp in ((r["home"], r["away"]), (r["away"], r["home"])):
            if ranks.get(opp, 99) > TIER:
                continue
            coach = coach_map.get((team, year))
            su, ats, n = records.get(coach, (0, 0, 0))
            if n >= MIN_GAMES and ((ats + 5) / (n + 10) >= 0.55 or su / n >= 0.5):
                game.append(_pick(r, f"coach vs top {TIER}", team,
                                  f"{coach} vs top {TIER}, last 3 seasons: {ats}-{n - ats} ATS, {su}-{n - su} SU; "
                                  f"{opp} is #{ranks[opp]}"))
        if len(game) == 1:  # both coaches qualifying (two top-10 teams) would be offsetting picks
            out += game
    return out


def _pick(r, angle, side, detail):
    spread = _num(r, "market_spread")
    line = spread if side == r["home"] else -spread
    return {"game_id": r["game_id"], "away": r["away"], "home": r["home"], "angle": angle, "side": side,
            "home_line": spread, "pick": f"{side} {line:+g}", "detail": detail}


def picks(rows, ranks, coach_map, records, year):
    return model_picks(rows) + coach_angle(rows, ranks, coach_map, records, year)
