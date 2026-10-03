"""Project game totals from opponent-adjusted EPA and compare them to market over/unders.

For every offense-vs-defense matchup we form two inputs:

    exp_epa   = intercept + off_adj[A] + def_adj[B] - 2 * intercept + hfa * home
    exp_plays = average of A's offensive plays per game and B's plays faced per game

and fit points = a + b * exp_plays + c * exp_plays * exp_epa on games already played
(points ~ plays x points-per-play, with points-per-play linear in EPA/play). A game's
projected total is the sum of both sides; edge = projection - market total.

Backtest before trusting edges (--backtest). On 2025, walk-forward from week 4, edges
did NOT beat closing totals (~50% at every threshold and every ridge penalty tried). They did predict line movement:
lines moved toward the model ~0.5-1 pt between open and close, so the board is most
useful as a screen for early-week numbers, not as a list of bets.

Usage:
  python -m cfb_stats.totals                      # this week's board
  python -m cfb_stats.totals --week 6
  python -m cfb_stats.totals --backtest --year 2025
"""

import argparse
import math
import os
import statistics
from collections import defaultdict

import numpy as np

from . import adjust
from .collect import P4_CONFERENCES, default_year, write_csv

DEFAULT_MIN_EDGE = 3.0
# Heavier ridge penalty than the rating tables use: on the 2025 backtest it cut total RMSE
# from 17.1 to 15.9 and kept early-season projections from running to extremes.
TOTALS_ALPHA = 1000.0


def _points(game, team):
    if game.get("homeTeam") == team:
        return game.get("homePoints")
    if game.get("awayTeam") == team:
        return game.get("awayPoints")
    return None


def pace(obs):
    """team -> (offensive plays per game, plays faced per game)."""
    off, dfn = defaultdict(list), defaultdict(list)
    for o in obs:
        plays = o["stats"].get("plays")
        if plays:
            off[o["offense"]].append(plays)
            dfn[o["defense"]].append(plays)
    teams = set(off) | set(dfn)
    return {t: (statistics.mean(off[t]) if off[t] else None,
                statistics.mean(dfn[t]) if dfn[t] else None) for t in teams}


class TotalsModel:
    def __init__(self, game_rows, games, alpha=TOTALS_ALPHA):
        self.models, self.obs = adjust.fit_all(game_rows, games, alpha)
        self.epa = self.models["epa"]
        self.pace = pace(self.obs)
        all_plays = [o["stats"]["plays"] for o in self.obs if o["stats"].get("plays")]
        self.avg_plays = statistics.mean(all_plays)
        self.games_played = defaultdict(set)
        for o in self.obs:
            self.games_played[o["offense"]].add(o["game_id"])
        self._fit_points(games)

    def features(self, off, dfn, home):
        e = self.epa
        i = e["intercept"]
        exp_epa = e["off"].get(off, i) + e["def"].get(dfn, i) - i + e["hfa"] * home
        off_pace = self.pace.get(off, (None, None))[0] or self.avg_plays
        def_pace = self.pace.get(dfn, (None, None))[1] or self.avg_plays
        exp_plays = (off_pace + def_pace) / 2
        return exp_epa, exp_plays

    def _fit_points(self, games):
        by_id = {g["id"]: g for g in games}
        X, y = [], []
        for o in self.obs:
            pts = _points(by_id.get(o["game_id"], {}), o["offense"])
            if pts is None:
                continue
            exp_epa, exp_plays = self.features(o["offense"], o["defense"], o["home"])
            X.append((1.0, exp_plays, exp_plays * exp_epa))
            y.append(pts)
        self.coef, *_ = np.linalg.lstsq(np.array(X), np.array(y, dtype=float), rcond=None)

    def team_points(self, off, dfn, home):
        exp_epa, exp_plays = self.features(off, dfn, home)
        return float(self.coef @ (1.0, exp_plays, exp_plays * exp_epa))

    def project(self, game):
        home, away = game["homeTeam"], game["awayTeam"]
        h = 0 if game.get("neutralSite") else 1
        hp, ap = self.team_points(home, away, h), self.team_points(away, home, -h)
        return hp, ap


def consensus_total(line_row):
    """Median closing over/under across books, and the opening median."""
    lines = line_row.get("lines") or []
    close = [l["overUnder"] for l in lines if l.get("overUnder") is not None]
    opens = [l["overUnderOpen"] for l in lines if l.get("overUnderOpen") is not None]
    return (statistics.median(close) if close else None,
            statistics.median(opens) if opens else None,
            ", ".join(sorted({l["provider"] for l in lines if l.get("overUnder") is not None})))


def board(model, games, lines, min_games=3):
    """One row per game with a market total."""
    by_id = {g["id"]: g for g in games}
    rows = []
    for lr in lines:
        g = by_id.get(lr["id"])
        total, total_open, books = consensus_total(lr)
        if g is None or total is None:
            continue
        home, away = g["homeTeam"], g["awayTeam"]
        n_home, n_away = len(model.games_played[home]), len(model.games_played[away])
        hp, ap = model.project(g)
        proj = hp + ap
        side = "OVER" if proj > total else "UNDER"
        rows.append({
            "game_id": g["id"],
            "week": g.get("week"),
            "start": g.get("startDate"),
            "away": away,
            "home": home,
            "neutral": bool(g.get("neutralSite")),
            "p4_game": g.get("homeConference") in P4_CONFERENCES or g.get("awayConference") in P4_CONFERENCES,
            "market_total": total,
            "market_open": total_open,
            "books": books,
            "proj_away": round(ap, 1),
            "proj_home": round(hp, 1),
            "proj_total": round(proj, 1),
            "edge": round(proj - total, 1),
            "pick": side,
            "games_home": n_home,
            "games_away": n_away,
            "enough_data": min(n_home, n_away) >= min_games,
            "actual_total": (g["homePoints"] + g["awayPoints"]) if g.get("homePoints") is not None and g.get("awayPoints") is not None else None,
        })
    rows.sort(key=lambda r: -abs(r["edge"]))
    return rows


def fetch_season(client, year, weeks, exclude_gt=True):
    games = [dict(g, seasonType="regular") for g in client.games(year, "regular")]
    game_rows, lines = {}, {}
    for w in weeks:
        game_rows[w] = client.game_advanced_stats(year, w, "regular", exclude_gt)
        lines[w] = client.get("/lines", year=year, week=w, seasonType="regular")
    return games, game_rows, lines


def _played_before(games, game_rows, week):
    rows = [r for w, rs in game_rows.items() if w < week for r in rs]
    done = [g for g in games if g.get("week", 99) < week]
    return rows, done


def backtest(client, year, first_week=4, last_week=None, alpha=TOTALS_ALPHA, min_games=3):
    """Walk-forward: each week is projected using only earlier weeks' games."""
    games = [dict(g, seasonType="regular") for g in client.games(year, "regular")]
    completed_weeks = sorted({g["week"] for g in games if g.get("completed")})
    last_week = last_week or max(completed_weeks)
    weeks = [w for w in completed_weeks if w <= last_week]
    game_rows = {w: client.game_advanced_stats(year, w, "regular", True) for w in weeks}
    results = []
    for w in weeks:
        if w < first_week:
            continue
        rows, done = _played_before(games, game_rows, w)
        model = TotalsModel(rows, done, alpha)
        lines = client.get("/lines", year=year, week=w, seasonType="regular")
        wk_games = [g for g in games if g.get("week") == w]
        for r in board(model, wk_games, lines, min_games):
            if r["actual_total"] is not None and r["enough_data"]:
                results.append(r)
    return results


def summarize_backtest(results, thresholds=(0, 2, 3, 4, 5, 7, 10)):
    miss = [r["actual_total"] - r["proj_total"] for r in results]
    mkt_miss = [r["actual_total"] - r["market_total"] for r in results]
    print(f"{len(results)} games with a market total and >=3 games of data per team")
    print(f"model  total RMSE {math.sqrt(statistics.mean(m * m for m in miss)):.1f}  "
          f"bias {statistics.mean(miss):+.1f}")
    print(f"market total RMSE {math.sqrt(statistics.mean(m * m for m in mkt_miss)):.1f}  "
          f"bias {statistics.mean(mkt_miss):+.1f}")
    edge = np.array([r["edge"] for r in results])
    beat = np.array(mkt_miss)
    print(f"corr(model edge, actual - market) = {np.corrcoef(edge, beat)[0, 1]:+.3f}")
    print(f"{'|edge|>=':>9} {'bets':>5} {'W-L-P':>10} {'win%':>6} {'ROI@-110':>9}")
    out = []
    for t in thresholds:
        w = l = p = 0
        for r in results:
            if abs(r["edge"]) < t or r["edge"] == 0:
                continue
            diff = r["actual_total"] - r["market_total"]
            if diff == 0:
                p += 1
            elif (diff > 0) == (r["pick"] == "OVER"):
                w += 1
            else:
                l += 1
        n = w + l
        win = w / n if n else float("nan")
        roi = (w * (100 / 110) - l) / n if n else float("nan")
        print(f"{t:>9} {n + p:>5} {f'{w}-{l}-{p}':>10} {win:>6.1%} {roi:>+9.1%}")
        out.append({"min_edge": t, "bets": n + p, "wins": w, "losses": l, "pushes": p,
                    "win_pct": round(win, 3), "roi_110": round(roi, 3)})
    opened = [r for r in results if r["market_open"] is not None]
    if opened:
        e = np.array([r["proj_total"] - r["market_open"] for r in opened])
        op = np.array([r["market_open"] for r in opened])
        cl = np.array([r["market_total"] for r in opened])
        act = np.array([r["actual_total"] for r in opened])
        print(f"\n{len(opened)} games with an opening total")
        print(f"corr(model - open, close - open) = {np.corrcoef(e, cl - op)[0, 1]:+.3f}  (predicts line movement?)")
        print(f"corr(model - open, actual - open) = {np.corrcoef(e, act - op)[0, 1]:+.3f}  (predicts results?)")
        for t in (0, 3, 5, 7):
            m = (abs(e) >= t) & (act != op) & (e != 0)
            n, w = int(m.sum()), int(((act - op > 0) == (e > 0))[m].sum())
            if n:
                move = float(((cl - op) * np.sign(e))[abs(e) >= t].mean())
                print(f"  vs open |edge|>={t}: {n} bets, {w / n:.1%} win, ROI {(w * 100 / 110 - (n - w)) / n:+.1%}, "
                      f"line moved {move:+.2f} toward model")
    return out


def main(argv=None):
    from .api import CFBDClient

    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--year", type=int, default=default_year())
    p.add_argument("--week", type=int, default=None, help="regular-season week (default: next unfinished)")
    p.add_argument("--out", default="data")
    p.add_argument("--api-key", default=None)
    p.add_argument("--alpha", type=float, default=TOTALS_ALPHA)
    p.add_argument("--min-edge", type=float, default=DEFAULT_MIN_EDGE, help="points of edge to list as a pick")
    p.add_argument("--min-games", type=int, default=3, help="games of data each team needs")
    p.add_argument("--backtest", action="store_true", help="walk-forward backtest of --year instead")
    args = p.parse_args(argv)

    client = CFBDClient(api_key=args.api_key)
    out_dir = os.path.join(args.out, str(args.year))
    os.makedirs(out_dir, exist_ok=True)

    if args.backtest:
        results = backtest(client, args.year, alpha=args.alpha, min_games=args.min_games)
        summary = summarize_backtest(results)
        write_csv(os.path.join(out_dir, "totals_backtest_games.csv"), results)
        write_csv(os.path.join(out_dir, "totals_backtest_summary.csv"), summary)
        return

    games = [dict(g, seasonType="regular") for g in client.games(args.year, "regular")]
    week = args.week or min(g["week"] for g in games if not g.get("completed"))
    weeks = sorted({g["week"] for g in games if g.get("completed") and g["week"] <= week})
    game_rows = [r for w in weeks for r in client.game_advanced_stats(args.year, w, "regular", True)]
    done = [g for g in games if g.get("completed")]
    model = TotalsModel(game_rows, done, args.alpha)
    lines = client.get("/lines", year=args.year, week=week, seasonType="regular")
    upcoming = [g for g in games if g.get("week") == week and not g.get("completed")]
    rows = board(model, upcoming, lines, args.min_games)

    path = os.path.join(out_dir, f"totals_week{week}.csv")
    write_csv(path, rows)
    picks = [r for r in rows if abs(r["edge"]) >= args.min_edge and r["enough_data"]]
    print(f"week {week}: {len(rows)} games with a market total, wrote {path}")
    print(f"{len(picks)} with |edge| >= {args.min_edge} (lean only: see --backtest):")
    for r in picks:
        print(f"  {r['away']:>20} @ {r['home']:<20} mkt {r['market_total']:>5}  proj {r['proj_total']:>5}  "
              f"{r['pick']:<5} {r['edge']:+5.1f}")


if __name__ == "__main__":
    main()
