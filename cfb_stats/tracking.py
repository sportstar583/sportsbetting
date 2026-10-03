"""Track the weekly card against the closing line and the final score.

Every card run appends its picks to data/<year>/card_log.csv. Later runs fill in, for weeks
whose games are final, the closing total (median across books from CFBD /lines, which holds
closing numbers once games are played) and the final score, then compute:

  closing line value (CLV): points the line moved in your favor after the pick
      OVER:  close - line at pick      UNDER: line at pick - close
  result: win/loss/push at the best line available when the card was made

The bet for a week is the first time a game/side appeared on a card (normally the Sunday
run); later runs that week are logged too, so you can see what Friday changed.
"""

import csv
import datetime
import os

LOG_FIELDS = ["logged_at", "run", "week", "game_id", "away", "home", "side", "median_line", "best_line",
              "best_book", "proj_total", "edge", "close_line", "actual_total", "clv", "result"]


def log_path(out_dir):
    return os.path.join(out_dir, "card_log.csv")


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


def log_card(path, picks, week, now=None):
    """Append this run's picks. picks: [(board row, side)]."""
    now = now or datetime.datetime.now(datetime.timezone.utc)
    rows = read_log(path)
    for r, side in picks:
        best = r.get("best_over") if side == "OVER" else r.get("best_under")
        book = r.get("best_over_book") if side == "OVER" else r.get("best_under_book")
        rows.append({"logged_at": now.strftime("%Y-%m-%d %H:%M"), "run": now.strftime("%A"), "week": week,
                     "game_id": r["game_id"], "away": r["away"], "home": r["home"], "side": side,
                     "median_line": r["market_total"], "best_line": best or r["market_total"],
                     "best_book": book or "", "proj_total": r["proj_total"], "edge": r["edge"]})
    write_log(path, rows)


def grade(row, close, actual):
    line, best = float(row["median_line"]), float(row["best_line"] or row["median_line"])
    over = row["side"] == "OVER"
    clv = (close - line) if over else (line - close)
    diff = actual - best
    result = "P" if diff == 0 else ("W" if (diff > 0) == over else "L")
    return round(clv, 2), result


def update_log(path, client, year, games):
    """Fill closing line, final total, CLV and result for picks whose game is final."""
    from .totals import consensus_total

    rows = read_log(path)
    by_id = {str(g["id"]): g for g in games}
    pending = [r for r in rows if not r.get("result") and by_id.get(str(r["game_id"]), {}).get("completed")]
    closes = {}
    for week in sorted({int(r["week"]) for r in pending}):
        for lr in client.get("/lines", year=year, week=week, seasonType="regular"):
            closes[str(lr["id"])] = consensus_total(lr)[0]
    for r in pending:
        g, close = by_id[str(r["game_id"])], closes.get(str(r["game_id"]))
        if close is None or g.get("homePoints") is None or g.get("awayPoints") is None:
            continue
        actual = g["homePoints"] + g["awayPoints"]
        r["close_line"], r["actual_total"] = close, actual
        r["clv"], r["result"] = grade(r, close, actual)
    if pending:
        write_log(path, rows)
    return rows


def summary(rows):
    """Markdown lines summarizing graded bets (first appearance of each game/side)."""
    seen, bets = set(), []
    for r in rows:
        key = (r["week"], r["game_id"], r["side"])
        if key not in seen:
            seen.add(key)
            bets.append(r)
    graded = [r for r in bets if r.get("result")]
    if not graded:
        return ["Track record: no graded picks yet (fills in once card games are final)."]
    w = sum(r["result"] == "W" for r in graded)
    l = sum(r["result"] == "L" for r in graded)
    p = sum(r["result"] == "P" for r in graded)
    clvs = [float(r["clv"]) for r in graded]
    beat = sum(c > 0 for c in clvs)
    lines = [f"Track record ({len(graded)} picks): {w}-{l}-{p} at the best line"
             f"{f' ({w / (w + l):.1%})' if w + l else ''}; average closing line value {sum(clvs) / len(clvs):+.2f} pts,"
             f" beat the close on {beat} of {len(clvs)}. Positive CLV over a full season is the best early sign the"
             " edge is real."]
    return lines
