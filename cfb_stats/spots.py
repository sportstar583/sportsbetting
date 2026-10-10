"""Schedule and recency spots, flagged on the weekly card for tracking (not bets).

Schedule spots (a "big game" is one against an AP top-25 team, the poll at the time; the current
opponent is unranked):

  bounce-back: the team lost to a ranked team last game        -> back the team
  sandwich:    ranked opponent last game and next game         -> fade the team

Recency spots (each team's previous game against its closing line):

  ats-revert fade: covered by 21+ last game (opponent didn't)  -> fade the team
  ats-revert back: missed by 21+ last game (opponent didn't)   -> back the team
  over run:        both teams' last games went over by 10+     -> over
  under run:       both teams' last games went under by 10+    -> under

Season record spot (over/under records against closing totals, 4+ graded games each):

  over teams:      both teams have gone over in 65%+ of games  -> under

All beat the closing line in 2023-2025 on small samples found after trying several versions
(scripts/spots_backtest.py, scripts/recency_backtest.py, BACKTEST.md). Every card run logs this
week's spots to data/<year>/spots_log.csv; later runs grade them against the closing line and the
final score.
"""

import csv
import datetime
import os
import statistics
from collections import defaultdict

# name -> 2023-2025 record against the closing line, as flagged here
SPOTS = {"bounce-back": "122-103", "sandwich": "70-53", "ats-revert fade": "218-187",
         "ats-revert back": "202-172", "over run": "95-77", "under run": "103-82", "over teams": "54-40"}
ATS_REVERT = 21  # points beyond the spread last game
TOTAL_RUN = 10  # points beyond the total last game, both teams
OVER_PCT, MIN_GRADED = 0.65, 4  # over teams: both teams' season over % and games graded
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
    return dedupe(found)


def dedupe(found):
    """Drop games where spread spots point at both sides. Totals spots that disagree (an over run
    and over teams in the same game) are both kept, so each spot's record matches its backtest;
    the card marks the conflict."""
    sides = defaultdict(set)
    for s in found:
        if s["bet_team"] not in ("OVER", "UNDER"):
            sides[s["game_id"]].add(s["bet_team"])
    return [s for s in found if s["bet_team"] in ("OVER", "UNDER") or len(sides[s["game_id"]]) == 1]


def find_season_records(games, rows, lines, week):
    """'over teams' spots: both teams went over their closing total in OVER_PCT+ of graded games
    this season (before this week). lines: {game id: (spread, total)} for those weeks."""
    ou = defaultdict(list)
    for g in games:
        if (g.get("week") or 0) >= week or g.get("homePoints") is None or g.get("awayPoints") is None:
            continue
        tot = lines.get(g["id"], (None, None))[1]
        d = None if tot is None else g["homePoints"] + g["awayPoints"] - tot
        if d:
            for t in (g["homeTeam"], g["awayTeam"]):
                ou[t].append(d > 0)
    found = []
    for r in rows:
        if r.get("market_total") in (None, ""):
            continue
        h, a = ou.get(r["home"], []), ou.get(r["away"], [])
        if len(h) < MIN_GRADED or len(a) < MIN_GRADED:
            continue
        ph, pa = sum(h) / len(h), sum(a) / len(a)
        if min(ph, pa) >= OVER_PCT:
            found.append({"game_id": int(r["game_id"]), "spot": "over teams", "team_in_spot": f"{r['away']} @ {r['home']}",
                          "bet_team": "UNDER", "opponent": "", "line": float(r["market_total"]),
                          "why": f"{r['home']} over in {sum(h)}/{len(h)}, {r['away']} in {sum(a)}/{len(a)} games"})
    return found


def previous_games(games, rows):
    """{(game id, team): previous game} for each team in the board rows."""
    sched = _schedules(games)
    out = {}
    for r in rows:
        gid = int(r["game_id"])
        for team in (r["home"], r["away"]):
            gs = sched.get(team, [])
            i = next((k for k, s in enumerate(gs) if s["id"] == gid), None)
            if i:
                out[(gid, team)] = gs[i - 1]
    return out


def closing_lines(client, year, weeks, current_week):
    """{game id: (median home spread, median total)} for these weeks. Weeks finished 2+ weeks ago
    are kept in data/<year>/cache/ (their closing lines don't change)."""
    from .totals import consensus_spread, consensus_total

    out = {}
    for w in sorted(weeks):
        kw = dict(year=year, week=w, seasonType="regular")
        resp = (client.get_stored(f"lines_regular_week{w}", "/lines", **kw) if w <= current_week - 2
                else client.get("/lines", **kw))
        for lr in resp or []:
            out[lr["id"]] = (consensus_spread(lr)[0], consensus_total(lr)[0])
    return out


def find_recency(games, rows, prev, lines):
    """Recency spots among board rows. prev: previous_games(); lines: closing_lines() for the
    weeks of those previous games."""
    by_id = {g["id"]: g for g in games}

    def last(gid, team):
        """(cover margin, total minus closing total) in the team's previous game, or None."""
        pg = prev.get((gid, team))
        g = by_id.get(pg["id"]) if pg else None
        sp, tot = lines.get(pg["id"], (None, None)) if pg else (None, None)
        if g is None or g.get("homePoints") is None or g.get("awayPoints") is None:
            return None
        sign = 1 if g["homeTeam"] == team else -1
        margin = sign * (g["homePoints"] - g["awayPoints"])
        return (None if sp is None else margin + sign * sp,
                None if tot is None else g["homePoints"] + g["awayPoints"] - tot)

    found = []
    for r in rows:
        gid = int(r["game_id"])
        lh, la = last(gid, r["home"]), last(gid, r["away"])
        if lh is None or la is None:
            continue
        if r.get("market_spread") not in (None, "") and lh[0] is not None and la[0] is not None:
            spread = float(r["market_spread"])
            for (team, opp, sign), mine, theirs in (((r["home"], r["away"], 1), lh[0], la[0]),
                                                    ((r["away"], r["home"], -1), la[0], lh[0])):
                if mine >= ATS_REVERT and theirs < ATS_REVERT:
                    found.append({"game_id": gid, "spot": "ats-revert fade", "team_in_spot": team,
                                  "bet_team": opp, "opponent": team, "line": -sign * spread,
                                  "why": f"{team} covered by {mine:g} last game"})
                if mine <= -ATS_REVERT and theirs > -ATS_REVERT:
                    found.append({"game_id": gid, "spot": "ats-revert back", "team_in_spot": team,
                                  "bet_team": team, "opponent": opp, "line": sign * spread,
                                  "why": f"{team} missed the spread by {-mine:g} last game"})
        if r.get("market_total") not in (None, "") and lh[1] is not None and la[1] is not None:
            total = float(r["market_total"])
            for name, side, ok in (("over run", "OVER", min(lh[1], la[1]) >= TOTAL_RUN),
                                   ("under run", "UNDER", max(lh[1], la[1]) <= -TOTAL_RUN)):
                if ok:
                    found.append({"game_id": gid, "spot": name, "team_in_spot": f"{r['away']} @ {r['home']}",
                                  "bet_team": side, "opponent": "", "line": total,
                                  "why": f"last games {lh[1]:+g} ({r['home']}) and {la[1]:+g} ({r['away']}) vs their totals"})
    return found


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
    """Grade logged spots whose game is final: closing line, margin, CLV and result."""
    rows = read_log(path)
    by_id = {str(g["id"]): g for g in games}
    pending = [r for r in rows if not r.get("result") and by_id.get(str(r["game_id"]), {}).get("completed")]
    close, close_total = {}, {}
    for week in sorted({int(r["week"]) for r in pending}):
        for lr in client.get("/lines", year=year, week=week, seasonType="regular") or []:
            sp = [ln["spread"] for ln in lr.get("lines") or [] if ln.get("spread") is not None]
            ou = [ln["overUnder"] for ln in lr.get("lines") or [] if ln.get("overUnder") is not None]
            if sp:
                close[str(lr["id"])] = statistics.median(sp)
            if ou:
                close_total[str(lr["id"])] = statistics.median(ou)
    for r in pending:
        g = by_id[str(r["game_id"])]
        if g.get("homePoints") is None or g.get("awayPoints") is None:
            continue
        if r["bet_team"] in ("OVER", "UNDER"):
            over = r["bet_team"] == "OVER"
            total, line = g["homePoints"] + g["awayPoints"], float(r["line"])
            r["margin"] = total
            r["result"] = "P" if total == line else ("W" if (total > line) == over else "L")
            if str(r["game_id"]) in close_total:
                c = close_total[str(r["game_id"])]
                r["close_line"] = c
                r["clv"] = round((c - line) if over else (line - c), 2)
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
           "Spots that beat the closing line in 2023-2025 on small samples (" +
           ", ".join(f"{k} {v}" for k, v in SPOTS.items()) + "): bounce-back = back a team that just lost to"
           " a ranked team; sandwich = fade a team between two ranked opponents; ats-revert = fade a team"
           " that covered by 21+ last game, back one that missed by 21+; over/under run = both teams' last"
           " games beat their totals by 10+ the same way; over teams = under when both teams have gone over in"
           " 65%+ of games. Logged to spots_log.csv to see if they hold up."
           " Use them as a tiebreaker, not a bet: a spot that agrees with a pick is a little extra"
           " confidence; one that disagrees is a reason to consider passing.", ""]
    if spots:
        out += ["| Spot | Take | Game | Why |", "| --- | --- | --- | --- |"]
        total_sides = defaultdict(set)
        for s in spots:
            if s["bet_team"] in ("OVER", "UNDER"):
                total_sides[s["game_id"]].add(s["bet_team"])
        for s in spots:
            g = by_id.get(str(s["game_id"]), {})
            why = s.get("why") or (f"{s['team_in_spot']} lost to a ranked team last game" if s["spot"] == "bounce-back"
                                   else f"{s['team_in_spot']} between two ranked opponents")
            take = (f"{s['bet_team']} {s['line']:g}" if s["bet_team"] in ("OVER", "UNDER")
                    else f"{s['bet_team']} {s['line']:+g}")
            if len(total_sides.get(s["game_id"], ())) > 1 and s["bet_team"] in ("OVER", "UNDER"):
                why += " (spots disagree in this game: no lean)"
            out.append(f"| {s['spot']} | {take} | {g.get('away')} @ {g.get('home')} | {why} |")
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
