"""Project game totals from opponent-adjusted EPA and compare them to market over/unders.

For every offense-vs-defense matchup we form two inputs:

    exp_epa   = off_adj[A] + def_adj[B] - intercept + hfa * home  (+ injury adjustments)
    exp_plays = plays team A should run, from tempo (see below)

and fit points = a + b * exp_plays + c * exp_plays * exp_epa on games already played
(points ~ plays x points-per-play, with points-per-play linear in EPA/play). A game's
projected total is the sum of both sides; edge = projection - market total.

Tempo comes from drive data (clock time and plays per drive):
    seconds per play for A = A's offensive sec/play + B's defensive sec/play allowed - league
    A's share of the clock = average of A's possession share and 1 - B's
    exp_plays for A        = game clock x A's share / A's seconds per play
so a fast offense facing a team that holds the ball (e.g. an option offense) is projected
for fewer plays than its season average.

Not rewarding routs of bad teams: games against FCS opponents count --fcs-weight as much
in the ratings and points fit, and --huber-k down-weights single games where a team beat
its expected EPA by a lot, so a rout moves a rating less than its raw margin would.

Injuries: --injuries <csv>, see cfb_stats/injuries.py.

Backtest before trusting edges (--backtest). Results on 2025 are in the README.

Usage:
  python -m cfb_stats.totals                      # this week's board
  python -m cfb_stats.totals --week 6 --injuries injuries.csv
  python -m cfb_stats.totals --backtest --year 2025
"""

import argparse
import math
import os
import statistics
from collections import defaultdict

import numpy as np

from . import adjust, injuries as inj
from .collect import P4_CONFERENCES, default_year, write_csv

DEFAULT_MIN_EDGE = 3.0
# Heavier ridge penalty than the rating tables use: on the 2025 backtest it cut total RMSE
# from 17.1 to 15.9 and kept early-season projections from running to extremes.
TOTALS_ALPHA = 1000.0
DEFAULT_FCS_WEIGHT = 0.5
DEFAULT_HUBER_K = 1.5
GAME_SECONDS = 3600


def _points(game, team):
    if game.get("homeTeam") == team:
        return game.get("homePoints")
    if game.get("awayTeam") == team:
        return game.get("awayPoints")
    return None


def fbs_teams(games):
    teams = set()
    for g in games:
        for side in ("home", "away"):
            if g.get(f"{side}Classification") == "fbs":
                teams.add(g[f"{side}Team"])
    return teams


def pace(obs):
    """team -> (offensive plays per game, plays faced per game), non-garbage plays."""
    off, dfn = defaultdict(list), defaultdict(list)
    for o in obs:
        plays = o["stats"].get("plays")
        if plays:
            off[o["offense"]].append(plays)
            dfn[o["defense"]].append(plays)
    teams = set(off) | set(dfn)
    return {t: (statistics.mean(off[t]) if off[t] else None,
                statistics.mean(dfn[t]) if dfn[t] else None) for t in teams}


def _secs(t):
    if not isinstance(t, dict):
        return None
    return (t.get("minutes") or 0) * 60 + (t.get("seconds") or 0)


def tempo(drives):
    """Seconds per play and possession share from drive data.

    Returns {team: {"s_off", "s_def", "pos_share"}}, league sec/play, and the average
    clock seconds covered by drives per game (a bit under 3600: kickoffs, etc.).
    """
    off_secs, off_plays = defaultdict(float), defaultdict(float)
    def_secs, def_plays = defaultdict(float), defaultdict(float)
    game_secs = defaultdict(float)
    team_game_secs = defaultdict(float)  # clock in games this team played
    games_of = defaultdict(set)
    for d in drives:
        secs, plays = _secs(d.get("elapsed")), d.get("plays") or 0
        if not secs or secs < 0 or secs > 900 or plays <= 0:
            continue
        o, df, gid = d["offense"], d["defense"], d["gameId"]
        off_secs[o] += secs
        off_plays[o] += plays
        def_secs[df] += secs
        def_plays[df] += plays
        game_secs[gid] += secs
        games_of[o].add(gid)
        games_of[df].add(gid)
    for team, gids in games_of.items():
        team_game_secs[team] = sum(game_secs[g] for g in gids)
    lg = sum(off_secs.values()) / max(sum(off_plays.values()), 1)
    out = {}
    for team in games_of:
        out[team] = {
            "s_off": off_secs[team] / off_plays[team] if off_plays[team] else lg,
            "s_def": def_secs[team] / def_plays[team] if def_plays[team] else lg,
            "pos_share": off_secs[team] / team_game_secs[team] if team_game_secs[team] else 0.5,
        }
    clock = statistics.mean(game_secs.values()) if game_secs else GAME_SECONDS
    return out, lg, clock


class TotalsModel:
    def __init__(self, game_rows, games, alpha=TOTALS_ALPHA, drives=None,
                 fcs_weight=1.0, huber_k=None, off_offsets=None, def_offsets=None):
        self.fbs = fbs_teams(games)
        self.fcs_weight = fcs_weight
        weight = self._weight if fcs_weight != 1.0 and self.fbs else None
        self.models, self.obs = adjust.fit_all(game_rows, games, alpha, weight, huber_k)
        self.epa = self.models["epa"]
        self.pace = pace(self.obs)
        all_plays = [o["stats"]["plays"] for o in self.obs if o["stats"].get("plays")]
        self.avg_plays = statistics.mean(all_plays)
        self.tempo, self.lg_secs, self.clock = tempo(drives) if drives else ({}, None, GAME_SECONDS)
        self.off_offsets = off_offsets or {}
        self.def_offsets = def_offsets or {}
        self.games_played = defaultdict(set)
        for o in self.obs:
            self.games_played[o["offense"]].add(o["game_id"])
        self._fit_points(games)

    def _weight(self, o):
        both_fbs = o["offense"] in self.fbs and o["defense"] in self.fbs
        return 1.0 if both_fbs else self.fcs_weight

    def _plays(self, off, dfn):
        t_off, t_def = self.tempo.get(off), self.tempo.get(dfn)
        if t_off and t_def:
            secs = t_off["s_off"] + t_def["s_def"] - self.lg_secs
            share = (t_off["pos_share"] + 1 - t_def["pos_share"]) / 2
            return self.clock * share / max(secs, 10.0)
        off_pace = self.pace.get(off, (None, None))[0] or self.avg_plays
        def_pace = self.pace.get(dfn, (None, None))[1] or self.avg_plays
        return (off_pace + def_pace) / 2

    def features(self, off, dfn, home, injuries=False):
        e = self.epa
        i = e["intercept"]
        exp_epa = e["off"].get(off, i) + e["def"].get(dfn, i) - i + e["hfa"] * home
        if injuries:
            exp_epa += self.off_offsets.get(off, 0.0) + self.def_offsets.get(dfn, 0.0)
        return exp_epa, self._plays(off, dfn)

    def _fit_points(self, games):
        by_id = {g["id"]: g for g in games}
        X, y, w = [], [], []
        for o in self.obs:
            pts = _points(by_id.get(o["game_id"], {}), o["offense"])
            if pts is None:
                continue
            exp_epa, exp_plays = self.features(o["offense"], o["defense"], o["home"])
            X.append((1.0, exp_plays, exp_plays * exp_epa))
            y.append(pts)
            w.append(o.get("weight", 1.0))
        sw = np.sqrt(np.array(w))
        self.coef, *_ = np.linalg.lstsq(np.array(X) * sw[:, None], np.array(y, dtype=float) * sw, rcond=None)

    def team_points(self, off, dfn, home, injuries=True):
        exp_epa, exp_plays = self.features(off, dfn, home, injuries)
        return float(self.coef @ (1.0, exp_plays, exp_plays * exp_epa))

    def project(self, game, injuries=True):
        home, away = game["homeTeam"], game["awayTeam"]
        h = 0 if game.get("neutralSite") else 1
        return self.team_points(home, away, h, injuries), self.team_points(away, home, -h, injuries)


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
        healthy = sum(model.project(g, injuries=False))
        proj = hp + ap
        h = 0 if g.get("neutralSite") else 1
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
            "injury_adj": round(proj - healthy, 1),
            "exp_plays_away": round(model._plays(away, home), 1),
            "exp_plays_home": round(model._plays(home, away), 1),
            "exp_epa_away": round(model.features(away, home, -h, True)[0], 3),
            "exp_epa_home": round(model.features(home, away, h, True)[0], 3),
            "edge": round(proj - total, 1),
            "pick": "OVER" if proj > total else "UNDER",
            "games_home": n_home,
            "games_away": n_away,
            "enough_data": min(n_home, n_away) >= min_games,
            "actual_total": (g["homePoints"] + g["awayPoints"]) if g.get("homePoints") is not None and g.get("awayPoints") is not None else None,
        })
    rows.sort(key=lambda r: -abs(r["edge"]))
    return rows


# ---------------------------------------------------------------- backtests

VARIANTS = {
    "baseline": {},
    "+tempo": {"tempo": True},
    "+fcs_weight": {"fcs_weight": DEFAULT_FCS_WEIGHT},
    "+huber": {"huber_k": DEFAULT_HUBER_K},
    "+fcs_weight+huber": {"fcs_weight": DEFAULT_FCS_WEIGHT, "huber_k": DEFAULT_HUBER_K},
    "all": {"tempo": True, "fcs_weight": DEFAULT_FCS_WEIGHT, "huber_k": DEFAULT_HUBER_K},
}


def load_season(client, year):
    games = [dict(g, seasonType="regular") for g in client.games(year, "regular")]
    weeks = sorted({g["week"] for g in games if g.get("completed")})
    data = {"games": games, "weeks": weeks, "rows": {}, "drives": {}, "lines": {}, "passing": {}}
    for w in weeks:
        data["rows"][w] = client.game_advanced_stats(year, w, "regular", True)
        data["drives"][w] = client.drives(year, w, "regular")
        data["lines"][w] = client.lines(year, w, "regular")
        data["passing"][w] = client.passing_player_games(year, w, "regular") or passing_from_box(
            client.get("/games/players", year=year, week=w, seasonType="regular"),
            client.player_game_ppa(year, w, "regular", False))
    return data


def passing_from_box(box_games, player_ppa):
    """QB-game rows like /passing/players/games, for seasons that endpoint doesn't cover:
    attempts from box scores, EPA per pass play from player-game PPA."""
    pass_ppa = {(r["id"], r["team"]): (r.get("averagePPA") or {}).get("pass") for r in player_ppa}
    rows = []
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
                            att = int(str(a["stat"]).split("/")[1])
                        except (IndexError, ValueError):
                            continue
                        rows.append({"gameId": g["id"], "team": t["team"], "playerId": a["id"],
                                     "player": a["name"], "attempts": att,
                                     "ppa": pass_ppa.get((a["id"], t["team"]))})
    return rows


def _before(data, key, week):
    return [r for w, rs in data[key].items() if w < week for r in rs]


def build_model(data, week, alpha=TOTALS_ALPHA, tempo=False, fcs_weight=1.0, huber_k=None, **offsets):
    done = [g for g in data["games"] if g.get("week", 99) < week]
    return TotalsModel(_before(data, "rows", week), done, alpha,
                       drives=_before(data, "drives", week) if tempo else None,
                       fcs_weight=fcs_weight, huber_k=huber_k, **offsets)


def backtest(data, first_week=4, min_games=3, variants=VARIANTS):
    """Walk-forward: each week is projected using only earlier weeks' games. -> {variant: rows}"""
    results = {name: [] for name in variants}
    for w in data["weeks"]:
        if w < first_week:
            continue
        wk_games = [g for g in data["games"] if g.get("week") == w]
        for name, kw in variants.items():
            model = build_model(data, w, **kw)
            results[name] += [r for r in board(model, wk_games, data["lines"][w], min_games)
                              if r["actual_total"] is not None and r["enough_data"]]
    return results


def _record(results, t):
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
    return w, l, p


def compare_variants(results):
    print(f"{'variant':<20} {'games':>5} {'RMSE':>5} {'bias':>5} {'corr':>6}  "
          f"{'|e|>=3':>13} {'|e|>=5':>13} {'|e|>=7':>13}")
    rows = []
    for name, res in results.items():
        a = np.array([r["actual_total"] for r in res])
        p = np.array([r["proj_total"] for r in res])
        m = np.array([r["market_total"] for r in res])
        rmse, bias = math.sqrt(((a - p) ** 2).mean()), (a - p).mean()
        corr = np.corrcoef(p - m, a - m)[0, 1]
        row = {"variant": name, "games": len(res), "rmse": round(rmse, 2), "bias": round(bias, 2),
               "corr_edge_result": round(corr, 3)}
        cells = []
        for t in (3, 5, 7):
            w, l, _ = _record(res, t)
            pct = w / (w + l) if w + l else float("nan")
            row[f"win_pct_edge{t}"], row[f"bets_edge{t}"] = round(pct, 3), w + l
            cells.append(f"{w + l:>4} {pct:>6.1%}")
        print(f"{name:<20} {len(res):>5} {rmse:>5.1f} {bias:>+5.1f} {corr:>+6.3f}  " + "  ".join(f"{c:>13}" for c in cells))
        rows.append(row)
    mkt = next(iter(results.values()))
    a = np.array([r["actual_total"] for r in mkt])
    m = np.array([r["market_total"] for r in mkt])
    print(f"{'market (closing)':<20} {len(mkt):>5} {math.sqrt(((a - m) ** 2).mean()):>5.1f} {(a - m).mean():>+5.1f}")
    return rows


def summarize_backtest(results, thresholds=(0, 2, 3, 4, 5, 7, 10)):
    print(f"{'|edge|>=':>9} {'bets':>5} {'W-L-P':>10} {'win%':>6} {'ROI@-110':>9}")
    out = []
    for t in thresholds:
        w, l, p = _record(results, t)
        n = w + l
        win = w / n if n else float("nan")
        roi = (w * (100 / 110) - l) / n if n else float("nan")
        print(f"{t:>9} {n + p:>5} {f'{w}-{l}-{p}':>10} {win:>6.1%} {roi:>+9.1%}")
        out.append({"min_edge": t, "bets": n + p, "wins": w, "losses": l, "pushes": p,
                    "win_pct": round(win, 3), "roi_110": round(roi, 3)})
    open_rows = []
    opened = [r for r in results if r["market_open"] is not None]
    if opened:
        e = np.array([r["proj_total"] - r["market_open"] for r in opened])
        op = np.array([r["market_open"] for r in opened])
        cl = np.array([r["market_total"] for r in opened])
        act = np.array([r["actual_total"] for r in opened])
        print(f"\n{len(opened)} games with an opening total")
        corr_move, corr_result = np.corrcoef(e, cl - op)[0, 1], np.corrcoef(e, act - op)[0, 1]
        print(f"corr(model - open, close - open) = {corr_move:+.3f}  (predicts line movement?)")
        print(f"corr(model - open, actual - open) = {corr_result:+.3f}  (predicts results?)")
        for t in (0, 3, 5, 7):
            m = (abs(e) >= t) & (act != op) & (e != 0)
            n, w = int(m.sum()), int(((act - op > 0) == (e > 0))[m].sum())
            if n:
                move = float(((cl - op) * np.sign(e))[abs(e) >= t].mean())
                roi = (w * 100 / 110 - (n - w)) / n
                print(f"  vs open |edge|>={t}: {n} bets, {w / n:.1%} win, ROI {roi:+.1%}, "
                      f"line moved {move:+.2f} toward model")
                open_rows.append({"min_edge": t, "bets": n, "wins": w, "losses": n - w, "win_pct": round(w / n, 3),
                                  "roi_110": round(roi, 3), "avg_line_move_toward_model": round(move, 2),
                                  "corr_edge_vs_line_move": round(corr_move, 3),
                                  "corr_edge_vs_result": round(corr_result, 3)})
    return out, open_rows


def _qb_season(passing):
    """(team, player id) -> {attempts, ppa, games, name} from QB-game rows."""
    agg = {}
    for r in passing:
        att = r.get("attempts") or 0
        if att <= 0 or r.get("ppa") is None:
            continue
        a = agg.setdefault((r["team"], r["playerId"]), {"name": r["player"], "attempts": 0, "ppa_sum": 0.0, "games": 0})
        a["attempts"] += att
        a["ppa_sum"] += r["ppa"] * att
        a["games"] += att >= 10
    for a in agg.values():
        a["ppa"] = a["ppa_sum"] / a["attempts"]
    return agg


def qb_out_backtest(data, first_week=4, min_games=3, model_kw=None):
    """When a team's season-to-date starting QB didn't play, did the QB adjustment help?

    Uses who actually played (known after the fact), standing in for an injury report.
    """
    model_kw = model_kw or {}
    out = []
    for w in data["weeks"]:
        if w < first_week:
            continue
        prior = _qb_season(_before(data, "passing", w))
        this_week = defaultdict(dict)
        for r in data["passing"][w]:
            this_week[(r["gameId"], r["team"])][r["playerId"]] = r.get("attempts") or 0
        starters = {}
        for (team, pid), a in prior.items():
            if a["games"] >= 2 and a["attempts"] > starters.get(team, (None, {"attempts": 0}))[1]["attempts"]:
                starters[team] = (pid, a)
        qbs = [{"position": "QB", "plays": a["attempts"], "epa": a["ppa"]} for a in prior.values()]
        if not starters or not qbs:
            continue  # no passing data before this week
        positions = {"QB": {
            "mean": float(np.average([q["epa"] for q in qbs], weights=[q["plays"] for q in qbs])),
            "replacement": float(np.percentile([q["epa"] for q in qbs if q["plays"] >= 30] or [0.0], inj.REPLACEMENT_PCTL)),
        }}
        team_plays = defaultdict(int)
        for d in _before(data, "drives", w):
            team_plays[d["offense"]] += d.get("plays") or 0
        offsets, flagged = {}, {}
        for g in data["games"]:
            if g.get("week") != w:
                continue
            for team in (g["homeTeam"], g["awayTeam"]):
                played = this_week.get((g["id"], team))
                if not played or team not in starters:
                    continue
                pid, a = starters[team]
                if played.get(pid, 0) >= 5:
                    continue
                usage = a["attempts"] / max(team_plays[team], 1)
                offsets[team] = inj.player_impact({"position": "QB", "plays": a["attempts"], "epa": a["ppa"],
                                                   "usage": usage}, positions)
                flagged[g["id"]] = flagged.get(g["id"], []) + [f"{team}: {a['name']} out"]
        if not flagged:
            continue
        model = build_model(data, w, off_offsets=offsets, **model_kw)
        wk_games = [g for g in data["games"] if g["id"] in flagged]
        for r in board(model, wk_games, data["lines"][w], min_games):
            if r["actual_total"] is None or not r["enough_data"]:
                continue
            r["qb_out"] = "; ".join(flagged[r["game_id"]])
            r["proj_no_injury"] = round(r["proj_total"] - r["injury_adj"], 1)
            out.append(r)
    return out


def summarize_qb_backtest(rows):
    if not rows:
        print("no games where a starting QB sat")
        return []
    a = np.array([r["actual_total"] for r in rows])
    with_adj = np.array([r["proj_total"] for r in rows])
    without = np.array([r["proj_no_injury"] for r in rows])
    m = np.array([r["market_total"] for r in rows])
    rm = lambda x: math.sqrt(((a - x) ** 2).mean())  # noqa: E731
    print(f"\n{len(rows)} games where a team's starting QB (season to date) didn't play")
    print(f"  avg injury adjustment {np.mean(with_adj - without):+.1f} pts")
    print(f"  RMSE without adjustment {rm(without):.1f}, with {rm(with_adj):.1f}, market {rm(m):.1f}")
    print(f"  bias without {np.mean(a - without):+.1f}, with {np.mean(a - with_adj):+.1f}, market {np.mean(a - m):+.1f}")
    out = []
    for label, proj in (("without", without), ("with", with_adj)):
        e, d = proj - m, a - m
        k = (e != 0) & (d != 0)
        win = ((e > 0) == (d > 0))[k].mean()
        print(f"  picking every game {label} adjustment: {win:.1%} of {int(k.sum())}")
        out.append({"qb_adjustment": label, "games": len(rows), "rmse": round(rm(proj), 2),
                    "bias": round(float(np.mean(a - proj)), 2), "pick_win_pct": round(float(win), 3),
                    "picks": int(k.sum()), "avg_adjustment": round(float(np.mean(proj - without)), 2)})
    out.append({"qb_adjustment": "market", "games": len(rows), "rmse": round(rm(m), 2),
                "bias": round(float(np.mean(a - m)), 2)})
    return out


# ---------------------------------------------------------------- CLI

def main(argv=None):
    from .api import CFBDClient

    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--year", type=int, default=default_year())
    p.add_argument("--week", type=int, default=None, help="regular-season week (default: next unfinished)")
    p.add_argument("--out", default="data")
    p.add_argument("--api-key", default=None)
    p.add_argument("--alpha", type=float, default=TOTALS_ALPHA)
    p.add_argument("--no-tempo", action="store_true", help="use plays/game instead of drive-based tempo")
    p.add_argument("--fcs-weight", type=float, default=DEFAULT_FCS_WEIGHT,
                   help="weight of games vs FCS opponents in the fit (1 = full)")
    p.add_argument("--huber-k", type=float, default=DEFAULT_HUBER_K,
                   help="down-weight single-game results beyond k robust SDs (0 = off)")
    p.add_argument("--injuries", default=None, help="CSV: team,player,status[,side,epa_delta]")
    p.add_argument("--min-edge", type=float, default=DEFAULT_MIN_EDGE, help="points of edge to list as a pick")
    p.add_argument("--min-games", type=int, default=3, help="games of data each team needs")
    p.add_argument("--backtest", action="store_true", help="walk-forward backtest of --year instead")
    p.add_argument("--cache", default=None, help="directory to cache API responses (backtests)")
    args = p.parse_args(argv)

    client = CFBDClient(api_key=args.api_key, cache_dir=args.cache)
    out_dir = os.path.join(args.out, str(args.year))
    os.makedirs(out_dir, exist_ok=True)
    chosen = {"alpha": args.alpha, "tempo": not args.no_tempo, "fcs_weight": args.fcs_weight,
              "huber_k": args.huber_k or None}

    if args.backtest:
        data = load_season(client, args.year)
        variants = dict(VARIANTS, chosen=chosen)
        results = backtest(data, min_games=args.min_games, variants=variants)
        comparison = compare_variants(results)
        print("\nchosen settings:", chosen)
        summary, open_summary = summarize_backtest(results["chosen"])
        qb = qb_out_backtest(data, min_games=args.min_games, model_kw=chosen)
        qb_summary = summarize_qb_backtest(qb)
        write_csv(os.path.join(out_dir, "totals_backtest_open.csv"), open_summary)
        if qb_summary:
            write_csv(os.path.join(out_dir, "totals_backtest_qb_summary.csv"), qb_summary)
        write_csv(os.path.join(out_dir, "totals_backtest_games.csv"), results["chosen"])
        write_csv(os.path.join(out_dir, "totals_backtest_summary.csv"), summary)
        write_csv(os.path.join(out_dir, "totals_backtest_variants.csv"), comparison)
        if qb:
            write_csv(os.path.join(out_dir, "totals_backtest_qb_out.csv"), qb)
        return

    games = [dict(g, seasonType="regular") for g in client.games(args.year, "regular")]
    week = args.week or min(g["week"] for g in games if not g.get("completed"))
    weeks = sorted({g["week"] for g in games if g.get("completed") and g["week"] <= week})
    game_rows = [r for w in weeks for r in client.game_advanced_stats(args.year, w, "regular", True)]
    drives = [d for w in weeks for d in client.drives(args.year, w, "regular")] if chosen["tempo"] else None

    off_offsets, def_offsets = {}, {}
    if args.injuries:
        players, positions = inj.player_values(client.player_season_ppa(args.year), client.player_usage(args.year))
        defenders = inj.defender_values(
            client.get("/stats/player/season", year=args.year, category="defensive")
            + client.get("/stats/player/season", year=args.year, category="interceptions"))
        off_offsets, def_offsets, detail = inj.team_offsets(inj.load_injuries(args.injuries), players, positions, defenders)
        write_csv(os.path.join(out_dir, f"injury_impacts_week{week}.csv"), detail)
        print("injuries (EPA/play; offense negative = worse, defense positive = allows more):")
        for team in sorted({d["team"] for d in detail}):
            rows_t = [d for d in detail if d["team"] == team]
            top = sorted((d for d in rows_t if d["impact"]), key=lambda d: -abs(d["impact"]))[:3]
            no_est = sum(d["impact"] is None for d in rows_t)
            print(f"  {team:<16} off {off_offsets.get(team, 0):+.3f}  def {def_offsets.get(team, 0):+.3f}  "
                  f"({len(rows_t)} listed{f', {no_est} OL/unestimated' if no_est else ''})  biggest: "
                  + ", ".join(f"{d['player']} {d['impact']:+.3f}" for d in top))

    done = [g for g in games if g.get("completed")]
    model = TotalsModel(game_rows, done, args.alpha, drives=drives, fcs_weight=args.fcs_weight,
                        huber_k=args.huber_k or None, off_offsets=off_offsets, def_offsets=def_offsets)
    lines = client.lines(args.year, week, "regular")
    upcoming = [g for g in games if g.get("week") == week and not g.get("completed")]
    rows = board(model, upcoming, lines, args.min_games)

    path = os.path.join(out_dir, f"totals_week{week}.csv")
    write_csv(path, rows)
    picks = [r for r in rows if abs(r["edge"]) >= args.min_edge and r["enough_data"]]
    print(f"week {week}: {len(rows)} games with a market total, wrote {path}")
    print(f"{len(picks)} with |edge| >= {args.min_edge} (lean only: see --backtest):")
    for r in picks:
        inj_note = f"  (injuries {r['injury_adj']:+.1f})" if r["injury_adj"] else ""
        print(f"  {r['away']:>20} @ {r['home']:<20} mkt {r['market_total']:>5}  proj {r['proj_total']:>5}  "
              f"{r['pick']:<5} {r['edge']:+5.1f}{inj_note}")


if __name__ == "__main__":
    main()
