"""One command for the weekly routine: injuries -> totals board -> weekly card.

  python -m cfb_stats.weekly            # upcoming week
  python -m cfb_stats.weekly --week 6

Steps:
  1. Find the week (first regular-season week with unplayed games).
  2. Pull Big Ten availability reports, keeping only reports for games actually played that
     week, so an old report from last week is never applied to this week's games.
  3. Build the totals board (data/<year>/totals_week<N>.csv) with those injuries.
  4. Write the weekly card to data/<year>/card_week<N>.md and print it: the biggest over and
     under edges among Power 4 games, the strategy with the best backtest (see BACKTEST.md).
"""

import argparse
import contextlib
import csv
import datetime
import io
import os

from . import availability, totals
from .collect import default_year, upcoming_week


def week_games(client, year, week):
    return [g for g in client.games(year, "regular") if g.get("week") == week]


def current_injuries(client, year, week, out_dir):
    """Write injuries for this week's Big Ten games; returns (path or None, note)."""
    try:
        reports = availability.fetch_reports("B10")
    except Exception as e:  # network/feed problems shouldn't stop the board
        return None, f"Big Ten availability feed unavailable ({e}); board built without injuries."
    pairs = {frozenset((g["homeTeam"], g["awayTeam"])) for g in week_games(client, year, week)
             if not g.get("completed")}
    current = [r for r in reports
               if frozenset(b.get("teamDisplayName") for b in r.get("games") or []) in pairs]
    if not current:
        return None, "No Big Ten availability reports posted yet for this week's games."
    teams = sorted({b.get("teamDisplayName") for r in current for b in r.get("games") or []})
    rosters = {t: client.get("/roster", year=year, team=t) for t in teams}
    rows = availability.entries(current, rosters)
    path = os.path.join(out_dir, f"injuries_week{week}.csv")
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["team", "player", "status", "side", "epa_delta",
                                          "player_id", "position", "jersey", "report"])
        w.writeheader()
        w.writerows(rows)
    latest = max(f"{r.get('publishDate')} {r.get('postedTime')}" for r in current)
    return path, f"Injuries: {len(current)} Big Ten game reports ({len(rows)} players), latest posted {latest}."


def card_markdown(rows, week, year, n, injury_note):
    num = lambda r, k: float(r[k]) if r.get(k) not in ("", None) else None  # noqa: E731
    p4 = [r for r in rows if r["p4_game"] == "True" and r["enough_data"] == "True"]
    overs = sorted((r for r in p4 if num(r, "edge") > 0), key=lambda r: -num(r, "edge"))[:n]
    unders = sorted((r for r in p4 if num(r, "edge") < 0), key=lambda r: num(r, "edge"))[:n]
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    def line(r, side):
        extras = []
        if num(r, "injury_adj"):
            extras.append(f"injuries {num(r, 'injury_adj'):+.1f}")
        if num(r, "matchup_adj"):
            extras.append(f"run/pass matchup {num(r, 'matchup_adj'):+.1f}")
        moved = ""
        if num(r, "market_open") is not None and num(r, "market_open") != num(r, "market_total"):
            moved = f" (opened {r['market_open']})"
        return (f"| {side} {r['market_total']}{moved} | {r['away']} @ {r['home']} | {r['proj_total']} | "
                f"{num(r, 'edge'):+.1f} | {r['start'][:10]} | {', '.join(extras)} |")

    out = [f"# Week {week} card ({year})", "",
           f"Generated {stamp}. {injury_note}", "",
           f"The {n} biggest over edges and {n} biggest under edges among Power 4 games. Backtest",
           "2023-2025: 114-88 (56.4%) against closing and opening totals; small sample, track it",
           "before trusting it. See BACKTEST.md.", "",
           "| Bet | Game | Model total | Edge | Date | Notes |",
           "| --- | --- | --- | --- | --- | --- |"]
    out += [line(r, "OVER") for r in overs] + [line(r, "UNDER") for r in unders]
    if not overs and not unders:
        out.append("| (no Power 4 games with a market total yet) | | | | | |")
    other = sorted((r for r in rows if r not in p4 and r["enough_data"] == "True" and abs(num(r, "edge")) >= 7),
                   key=lambda r: -abs(num(r, "edge")))[:5]
    if other:
        out += ["", "Biggest edges outside Power 4 (weaker backtest, about 50-53%):", ""]
        out += [f"- {r['pick']} {r['market_total']}: {r['away']} @ {r['home']} (model {r['proj_total']}, "
                f"edge {num(r, 'edge'):+.1f})" for r in other]
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
    week = args.week or upcoming_week(client.games(args.year, "regular"))
    out_dir = os.path.join(args.out, str(args.year))
    os.makedirs(out_dir, exist_ok=True)

    inj_path, inj_note = (None, "Injuries skipped (--no-injuries).") if args.no_injuries else \
        current_injuries(client, args.year, week, out_dir)
    board_args = ["--year", str(args.year), "--week", str(week), "--out", args.out, "--card", str(args.card)]
    if args.api_key:
        board_args += ["--api-key", args.api_key]
    if inj_path:
        board_args += ["--injuries", inj_path]
    with contextlib.redirect_stdout(io.StringIO()):
        totals.main(board_args)

    with open(os.path.join(out_dir, f"totals_week{week}.csv")) as f:
        rows = list(csv.DictReader(f))
    md = card_markdown(rows, week, args.year, args.card, inj_note)
    path = os.path.join(out_dir, f"card_week{week}.md")
    with open(path, "w") as f:
        f.write(md)
    print(md)
    print(f"wrote {path}")


if __name__ == "__main__":
    main()
