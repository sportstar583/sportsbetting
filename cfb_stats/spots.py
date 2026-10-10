"""Schedule spots against the spread, flagged on the weekly card for tracking (not bets).

A "big game" is one against an AP top-25 team (the poll at the time). In both spots the current
opponent is unranked:

  bounce-back: the team lost to a ranked team last game        -> back the team
  sandwich:    ranked opponent last game and next game         -> fade the team

Over 2023-2025 (as flagged here, one bet per game) bounce-back went 122-103 (54.2%) and sandwich
70-53 (56.9%) against the closing spread, above breakeven in all three seasons, but small samples
found after trying several spots (scripts/spots_backtest.py, BACKTEST.md). Every card run logs this week's spots to
data/<year>/spots_log.csv; later runs grade them against the closing spread and the final score.
"""

import csv
import datetime
import os
import statistics
from collections import defaultdict

SPOTS = {"bounce-back": "lost to a ranked team last game, unranked opponent now",
         "sandwich": "ranked opponent last game and next game, unranked opponent now"}
LOG_FIELDS = ["logged_at", "week", "game_id", "spot", "bet_team", "opponent", "team_in_spot", "line",
              "close_line", "margin", "clv", "result"]


def ap_polls(client, year):
    """{week: set of AP top-25 schools}, the poll entering that week (1 API call)."""
    out = {}
    for wk in client.get("/rankings", year=year, seasonType="regular") or []:
        for poll in wk.get("polls") or []:
            if poll.get("poll") == "AP Top 25":
                out[wk["week"]] = {r["school"] for r in poll.get("ranks") or []}
    return out


def ranked_at(polls, week):
    ws = [w for w in polls if w <= week]
    return polls[max(ws)] if ws else set()


def _schedules(games):
    sched = defaultdict(list)
    for g in sorted(games, key=lambda g: (g.get("week") or 0, g.get("startDate") or "")):
        for t, o in ((g["homeTeam"], g["awayTeam"]), (g["awayTeam"], g["homeTeam"])):
            won = None
            if g.get("homePoints") is not None and g.get("awayPoints") is not None:
                won = (g["homePoints"] > g["awayPoints"]) == (t == g["homeTeam"])
            sched[t].append({"id": g["id"], "week": g.get("week"), "opp": o, "won": won})
    return sched


def find_spots(games, polls, week, rows):
    """This week's spots among board rows with a spread.

    rows: board rows (game_id, home, away, market_spread as the home line). -> list of dicts with
    game_id, spot, team_in_spot, bet_team, opponent, line (the bet team's spread)."""
    now = ranked_at(polls, week)
    sched = _schedules(games)
    found = []
    for r in rows:
        if r.get("market_spread") in (None, ""):
            continue
        gid, spread = int(r["game_id"]), float(r["market_spread"])
        for team, opp, sign in ((r["home"], r["away"], 1), (r["away"], r["home"], -1)):
            if opp in now:
                continue
            gs = sched.get(team, [])
            i = next((k for k, s in enumerate(gs) if s["id"] == gid), None)
            if i is None:
                continue
            prev = gs[i - 1] if i else None
            nxt = gs[i + 1] if i + 1 < len(gs) else None
            prev_big = bool(prev and prev["opp"] in ranked_at(polls, prev["week"]))
            next_big = bool(nxt and nxt["opp"] in now)
            team_line = sign * spread  # the team's own spread (negative = favored)
            # A team between two ranked opponents is a sandwich even if it lost the first one
            # (that's how the backtest counted it).
            if prev_big and prev["won"] is False and not next_big:
                found.append({"game_id": gid, "spot": "bounce-back", "team_in_spot": team,
                              "bet_team": team, "opponent": opp, "line": team_line})
            if prev_big and next_big:
                found.append({"game_id": gid, "spot": "sandwich", "team_in_spot": team,
                              "bet_team": opp, "opponent": team, "line": -team_line})
    # Drop games where the spots point at both sides.
    sides = defaultdict(set)
    for s in found:
        sides[s["game_id"]].add(s["bet_team"])
    return [s for s in found if len(sides[s["game_id"]]) == 1]


def log_path(out_dir):
    return os.path.join(out_dir, "spots_log.csv")


def read_log(path):
    if not os.path.exists(path):
        return []
    with open(path, newline="") as f:
        return list(csv.DictReader(f))


def write_log(path, rows):
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=LOG_FIELDS, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


def log_spots(path, spots, week, now=None):
    """Append this week's spots unless already logged (the first run of the week sets the line)."""
    now = now or datetime.datetime.now(datetime.timezone.utc)
    rows = read_log(path)
    have = {(r["week"], r["game_id"], r["spot"], r["bet_team"]) for r in rows}
    for s in spots:
        key = (str(week), str(s["game_id"]), s["spot"], s["bet_team"])
        if key in have:
            continue
        rows.append({"logged_at": now.strftime("%Y-%m-%d %H:%M"), "week": week, "game_id": s["game_id"],
                     "spot": s["spot"], "bet_team": s["bet_team"], "opponent": s["opponent"],
                     "team_in_spot": s["team_in_spot"], "line": s["line"]})
    write_log(path, rows)
    return rows


def update_log(path, client, year, games):
    """Grade logged spots whose game is final: closing spread, margin, CLV and result."""
    rows = read_log(path)
    by_id = {str(g["id"]): g for g in games}
    pending = [r for r in rows if not r.get("result") and by_id.get(str(r["game_id"]), {}).get("completed")]
    close = {}
    for week in sorted({int(r["week"]) for r in pending}):
        for lr in client.get("/lines", year=year, week=week, seasonType="regular") or []:
            sp = [ln["spread"] for ln in lr.get("lines") or [] if ln.get("spread") is not None]
            if sp:
                close[str(lr["id"])] = statistics.median(sp)
    for r in pending:
        g = by_id[str(r["game_id"])]
        if g.get("homePoints") is None or g.get("awayPoints") is None:
            continue
        home = r["bet_team"] == g["homeTeam"]
        margin = (g["homePoints"] - g["awayPoints"]) * (1 if home else -1)
        line = float(r["line"])
        cover = margin + line
        r["margin"] = margin
        r["result"] = "P" if cover == 0 else ("W" if cover > 0 else "L")
        if str(r["game_id"]) in close:
            c = close[str(r["game_id"])] * (1 if home else -1)
            r["close_line"] = c
            r["clv"] = round(line - c, 2)  # getting more points than the close = positive
    if pending:
        write_log(path, rows)
    return rows


def card_section(spots, board_rows, log_rows):
    """Markdown lines for the weekly card."""
    by_id = {str(r["game_id"]): r for r in board_rows}
    out = ["## Spots to track (not bets)", "",
           "Schedule spots that beat the closing spread in 2023-2025 on small samples: bounce-back"
           " (back a team that just lost to a ranked team) 122-103, sandwich (fade a team between two"
           " ranked opponents) 70-53. Logged to spots_log.csv to see if they hold up.", ""]
    if spots:
        out += ["| Spot | Take | Game | Why |", "| --- | --- | --- | --- |"]
        for s in spots:
            g = by_id.get(str(s["game_id"]), {})
            why = (f"{s['team_in_spot']} lost to a ranked team last game" if s["spot"] == "bounce-back"
                   else f"{s['team_in_spot']} between two ranked opponents")
            out.append(f"| {s['spot']} | {s['bet_team']} {s['line']:+g} | {g.get('away')} @ {g.get('home')} | {why} |")
    else:
        out.append("No spots this week.")
    graded = [r for r in log_rows if r.get("result")]
    if graded:
        parts = []
        for name in SPOTS:
            g = [r for r in graded if r["spot"] == name]
            w, l = sum(r["result"] == "W" for r in g), sum(r["result"] == "L" for r in g)
            if g:
                parts.append(f"{name} {w}-{l}" + (f" ({w / (w + l):.0%})" if w + l else ""))
        clv = [float(r["clv"]) for r in graded if r.get("clv") not in (None, "")]
        out += ["", "Spots so far in 2026: " + ", ".join(parts)
                + (f"; average closing line value {sum(clv) / len(clv):+.2f}" if clv else "") + "."]
    return out
