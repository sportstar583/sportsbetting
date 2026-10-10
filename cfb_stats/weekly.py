"""One command for the weekly routine: injuries -> totals board -> weekly card.

  python -m cfb_stats.weekly            # upcoming week
  python -m cfb_stats.weekly --week 6

Steps:
  1. Find the week (first regular-season week with a game that hasn't kicked off).
  2. Pull Big Ten, SEC, ACC and Big 12 availability reports, keeping only reports for games actually played that
     week, so an old report from last week is never applied to this week's games.
  3. Build the totals board (data/<year>/totals_week<N>.csv) with those injuries.
  4. Note if last week's advanced stats aren't loaded yet (e.g. very early Sunday).
  5. Grade earlier card picks against the closing line and final score (card_log.csv).
  6. Write the weekly card to data/<year>/card_week<N>.md and print it: the biggest over and
     under edges among Power 4 games, the strategy with the best backtest (see BACKTEST.md).
  7. Flag schedule and recency spots (cfb_stats.spots) across all games with a line, log them to
     spots_log.csv and grade earlier ones. Tracking only, not bets.
"""

import argparse
import contextlib
import csv
import datetime
import io
import os
from collections import defaultdict

from . import availability, recruiting, spots, totals, tracking
from .collect import default_year, upcoming_week


CONFERENCES = {"B10": "Big Ten", "SEC": "SEC", "ACC": "ACC", "B12": "Big 12"}


def current_injuries(client, year, week, out_dir, games):
    """Write injuries for this week's games from each conference feed; returns (path or None, note)."""
    pairs = {frozenset((g["homeTeam"], g["awayTeam"])) for g in games
             if g.get("week") == week and not g.get("completed")}
    current, notes = [], []
    for code, label in CONFERENCES.items():
        try:
            reports = availability.fetch_reports(code)
        except Exception as e:  # network/feed problems shouldn't stop the board
            notes.append(f"{label} availability feed unavailable ({e})")
            continue
        mine = [r for r in reports
                if frozenset(b.get("teamDisplayName") for b in r.get("games") or []) in pairs]
        if mine:
            current += mine
        else:
            notes.append(f"No {label} availability reports posted yet for this week's games")
    if not current:
        return None, "; ".join(notes) + "; board built without injuries."
    teams = sorted({b.get("teamDisplayName") for r in current for b in r.get("games") or []})
    rosters = availability.all_rosters(client, year, teams)
    rows = availability.entries(current, rosters)
    try:  # recruiting classes are saved to data/recruiting/ after the first download
        recruiting.add_recruiting(rows, recruiting.recruit_index(client, year), rosters)
    except Exception as e:
        notes.append(f"recruiting ratings unavailable ({e})")
    path = os.path.join(out_dir, f"injuries_week{week}.csv")
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=availability.INJURY_FIELDS, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    latest = max(f"{r.get('publishDate')} {r.get('postedTime')}" for r in current)
    note = f"Injuries: {len(current)} conference game reports ({len(rows)} players), latest posted {latest}."
    return path, note + "".join(f" {n}." for n in notes)


def stats_freshness(client, year, week, games):
    """Note if last week's advanced stats aren't loaded yet (ratings would miss those games)."""
    prev = week - 1
    if prev < 1:
        return ""
    done = {g["id"] for g in games if g.get("week") == prev and g.get("completed")
            and "fbs" in (g.get("homeClassification"), g.get("awayClassification"))}
    if not done:
        return ""
    have = {r["gameId"] for r in client.game_advanced_stats(year, prev, "regular", True)}
    missing = len(done - have)
    if missing / len(done) > 0.1:
        return (f" WARNING: advanced stats are missing for {missing} of {len(done)} completed week {prev} "
                f"games, so ratings don't include them yet. Re-run later for a full update.")
    return ""


KEY_STATUSES = {"out", "doubtful", "game time decision", "out - (1st half)"}


def key_injuries(injuries, team):
    """Notable players unlikely to play for a team: QBs, and 4-5 star recruits."""
    out = []
    for r in injuries or []:
        if r.get("team") != team or (r.get("status") or "").strip().lower() not in KEY_STATUSES:
            continue
        stars = int(float(r.get("stars") or 0))
        if r.get("position") == "QB" or stars >= 4:
            out.append(f"{r['player']} ({r.get('position')}, {recruiting.star_label(r)}, {r['status'].lower()})")
    return out


def _num(r, k):
    return float(r[k]) if r.get(k) not in ("", None) else None


def card_picks(rows, n):
    """The card: n biggest over edges and n biggest under edges among Power 4 games."""
    p4 = [r for r in rows if r["p4_game"] == "True" and r["enough_data"] == "True"]
    overs = sorted((r for r in p4 if _num(r, "edge") > 0), key=lambda r: -_num(r, "edge"))[:n]
    unders = sorted((r for r in p4 if _num(r, "edge") < 0), key=lambda r: _num(r, "edge"))[:n]
    return [(r, "OVER") for r in overs] + [(r, "UNDER") for r in unders]


def card_markdown(rows, week, year, n, injury_note, injuries=None, track_record=None, spot_lines=None,
                  spot_list=None):
    num = _num
    picks = card_picks(rows, n)
    overs = [r for r, side in picks if side == "OVER"]
    unders = [r for r, side in picks if side == "UNDER"]
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    total_spots = defaultdict(list)  # game id -> [(spot name, OVER/UNDER)]
    for s in spot_list or []:
        if s["bet_team"] in ("OVER", "UNDER"):
            total_spots[str(s["game_id"])].append((s["spot"], s["bet_team"]))

    def line(r, side):
        extras = []
        for name, spot_side in total_spots.get(str(r.get("game_id")), []):
            # Tiebreaker only: spots are tracked, not bet (see the spots section).
            extras.append(f"{name} spot agrees" if spot_side == side
                          else f"{name} spot disagrees: consider passing")
        if num(r, "injury_adj"):
            extras.append(f"injuries {num(r, 'injury_adj'):+.1f}")
        if num(r, "matchup_adj"):
            extras.append(f"run/pass matchup {num(r, 'matchup_adj'):+.1f}")
        if r.get("dome") == "True":
            extras.append("dome")
        elif num(r, "wind_mph") is not None:
            wx = f"wind {num(r, 'wind_mph'):.0f} mph"
            if num(r, "precip_in"):
                wx += f", rain {num(r, 'precip_in'):.2f} in"
            wx += f", {num(r, 'temp_f'):.0f}F"
            if num(r, "weather_adj"):
                wx += f" ({num(r, 'weather_adj'):+.1f})"
            extras.append(wx)
        moved = ""
        if num(r, "market_open") is not None and num(r, "market_open") != num(r, "market_total"):
            moved = f" (opened {r['market_open']})"
        best, book = (r.get("best_over"), r.get("best_over_book")) if side == "OVER" else \
            (r.get("best_under"), r.get("best_under_book"))
        shop = f"{best} ({book})" if best not in (None, "") and num(r, "best_over" if side == "OVER" else "best_under") \
            != num(r, "market_total") else "same"
        return (f"| {side} {r['market_total']}{moved} | {shop} | {r['away']} @ {r['home']} | {r['proj_total']} | "
                f"{num(r, 'edge'):+.1f} | {r['start'][:10]} | {', '.join(extras)} |")

    out = [f"# Week {week} card ({year})", "",
           f"Generated {stamp}. {injury_note}", "",
           f"The {n} biggest over edges and {n} biggest under edges among Power 4 games. Backtest",
           "2023-2025: 114-88 (56.4%) against closing and opening totals; small sample, track it",
           "before trusting it. See BACKTEST.md.", "",
           "Bet is the median line across books; Best line is the best number available (lowest",
           "total for an over, highest for an under) and the book offering it. Notes flag an over/under",
           "run spot in the same game: agreeing is a little extra confidence, disagreeing is a reason to",
           "consider passing (spots are tracked, not bets; see below).", "",
           "| Bet | Best line | Game | Model total | Edge | Date | Notes |",
           "| --- | --- | --- | --- | --- | --- | --- |"]
    out += [line(r, "OVER") for r in overs] + [line(r, "UNDER") for r in unders]
    keys = []
    for r in overs + unders:
        for team in (r["away"], r["home"]):
            players = key_injuries(injuries, team)
            if players:
                keys.append(f"- {team}: " + "; ".join(players))
    if keys:
        out += ["", "Key injuries in card games (QBs and 4-5 star recruits listed out, doubtful or game-time):", ""]
        out += keys
    if not overs and not unders:
        out.append("| (no Power 4 games with a market total yet) | | | | | | |")
    if track_record:
        out += [""] + track_record
    if spot_lines:
        out += [""] + spot_lines
    return "\n".join(out) + "\n"


def main(argv=None):
    from .api import CFBDClient

    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--year", type=int, default=default_year())
    p.add_argument("--week", type=int, default=None)
    p.add_argument("--card", type=int, default=3)
    p.add_argument("--out", default="data")
    p.add_argument("--api-key", default=None)
    p.add_argument("--no-injuries", action="store_true")
    args = p.parse_args(argv)

    client = CFBDClient(api_key=args.api_key)
    games = client.games(args.year, "regular")
    week = args.week or upcoming_week(games)
    out_dir = os.path.join(args.out, str(args.year))
    os.makedirs(out_dir, exist_ok=True)

    inj_path, inj_note = (None, "Injuries skipped (--no-injuries).") if args.no_injuries else \
        current_injuries(client, args.year, week, out_dir, games)
    board_args = ["--year", str(args.year), "--week", str(week), "--out", args.out, "--card", str(args.card)]
    if args.api_key:
        board_args += ["--api-key", args.api_key]
    if inj_path:
        board_args += ["--injuries", inj_path]
    with contextlib.redirect_stdout(io.StringIO()):
        totals.main(board_args)

    inj_note += stats_freshness(client, args.year, week, games)
    with open(os.path.join(out_dir, f"totals_week{week}.csv")) as f:
        reader = csv.DictReader(f)
        all_rows = list(reader)
        fields, rows = reader.fieldnames, [r for r in all_rows if r["p4_game"] == "True"]
    with open(os.path.join(out_dir, f"totals_week{week}.csv"), "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)
    injuries = []
    if inj_path:
        with open(inj_path, newline="") as f:
            injuries = list(csv.DictReader(f))
    log = tracking.log_path(out_dir)
    try:  # grade earlier picks whose games are final (about 1 API call per week graded)
        tracking.update_log(log, client, args.year, games)
    except Exception as e:
        print(f"could not update the card log ({e})")
    tracking.log_card(log, card_picks(rows, args.card), week)
    spot_lines, found = None, []
    try:  # schedule spots across all games with a spread (1 API call for the AP poll)
        found = spots.find_spots(games, spots.ap_polls(client, args.year), week, all_rows)
        prev = spots.previous_games(games, all_rows)  # recency spots: last game vs its closing line
        prev_lines = spots.closing_lines(client, args.year, {pg["week"] for pg in prev.values()}, week)
        found = spots.dedupe(found + spots.find_recency(games, all_rows, prev, prev_lines))
        spot_log = spots.log_path(out_dir)
        spots.update_log(spot_log, client, args.year, games)
        spot_lines = spots.card_section(found, all_rows, spots.log_spots(spot_log, found, week))
    except Exception as e:
        print(f"could not check schedule spots ({e})")
    md = card_markdown(rows, week, args.year, args.card, inj_note, injuries,
                       tracking.summary(tracking.read_log(log)), spot_lines, found)
    path = os.path.join(out_dir, f"card_week{week}.md")
    with open(path, "w") as f:
        f.write(md)
    print(md)
    print(f"wrote {path}")


if __name__ == "__main__":
    main()
