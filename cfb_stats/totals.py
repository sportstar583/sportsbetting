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

Run/pass matchup (--matchup, default 1): exp_epa is built from rush EPA (A's rush offense vs
B's rush defense) and pass EPA (A's pass offense vs B's pass defense), weighted by A's expected
run rate, so a strong run team facing a weak run defense gets credit the overall ratings
average away. matchup_adj on the board shows how many points this moved each total.

Not rewarding routs of bad teams: games against FCS opponents count --fcs-weight as much
in the ratings and points fit, and --huber-k down-weights single games where a team beat
its expected EPA by a lot, so a rout moves a rating less than its raw margin would.

Injuries: --injuries <csv>, see cfb_stats/injuries.py.

Spreads: the board also has projected margins (proj_margin, spread_edge) from a lightly
shrunk model (--spread-alpha). The backtest found no edge against the spread; see BACKTEST.md.

Backtest before trusting edges (--backtest). Results are in BACKTEST.md.

Usage:
  python -m cfb_stats.totals                      # this week's board
  python -m cfb_stats.totals --week 6 --injuries injuries.csv
  python -m cfb_stats.totals --backtest --year 2025
"""

import argparse
import csv
import math
import os
import statistics
from collections import defaultdict

import numpy as np

from . import adjust, injuries as inj, priors as priors_mod, sp_plus, weather as wx_mod
from .collect import P4_CONFERENCES, default_year, upcoming_week, write_csv

DEFAULT_MIN_EDGE = 3.0
# Heavier ridge penalty than the rating tables use: on the 2025 backtest it cut total RMSE
# from 17.1 to 15.9 and kept early-season projections from running to extremes.
TOTALS_ALPHA = 1000.0
# Spreads need far less shrinkage than totals: at alpha 1000 projected margins vary about
# half as much as market spreads. 25 gave the lowest margin RMSE on 2024 and 2025.
SPREAD_ALPHA = 25.0
# Run/pass matchup weight for totals. Full split improved total RMSE and edge-vs-result
# correlation in both 2024 and 2025; it made spreads slightly worse, so spreads use 0.
DEFAULT_MATCHUP = 1.0
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


def run_shares(obs, prior_plays=100):
    """Run share of plays for each offense and allowed by each defense, shrunk toward the league."""
    off, dfn = defaultdict(lambda: [0.0, 0.0]), defaultdict(lambda: [0.0, 0.0])
    for o in obs:
        rush = adjust._plays(o["stats"], "rushingPlays") if o["stats"].get("rushingPlays") else 0.0
        pas = adjust._plays(o["stats"], "passingPlays") if o["stats"].get("passingPlays") else 0.0
        if rush + pas <= 0:
            continue
        for d, team in ((off, o["offense"]), (dfn, o["defense"])):
            d[team][0] += rush
            d[team][1] += rush + pas
    tot_r = sum(v[0] for v in off.values())
    tot = sum(v[1] for v in off.values())
    lg = tot_r / tot if tot else 0.5
    shrink = lambda d: {t: (r + lg * prior_plays) / (n + prior_plays) for t, (r, n) in d.items()}  # noqa: E731
    return shrink(off), shrink(dfn), lg


TURNOVER_RESULTS = {"INT", "INT TD", "FUMBLE", "FUMBLE TD", "FUMBLE RETURN TD"}
CLOCK_RESULTS = {"END OF HALF", "END OF GAME", "END OF 4TH QUARTER", "END OF HALF TD", "END OF GAME TD"}


def finishing_rates(drives, rz_prior=20, to_prior=60):
    """Red zone points per trip and turnovers per drive, for each offense and defense allowed.

    A red zone trip is a drive that starts or ends inside the opponent's 20 (TD = 7, FG = 3).
    Rates are shrunk toward the league rate by rz_prior trips / to_prior drives, since both are
    noisy over a few games. Returns ({team: {rz_off, rz_def, to_off, to_def}}, league rz, league to).
    """
    rz = {"off": defaultdict(lambda: [0.0, 0]), "def": defaultdict(lambda: [0.0, 0])}
    to = {"off": defaultdict(lambda: [0, 0]), "def": defaultdict(lambda: [0, 0])}
    for d in drives:
        res = d.get("driveResult") or ""
        if res in CLOCK_RESULTS or res == "Uncategorized":
            continue
        o, df = d["offense"], d["defense"]
        lost = int(res in TURNOVER_RESULTS)
        for side, team in (("off", o), ("def", df)):
            to[side][team][0] += lost
            to[side][team][1] += 1
        start, end = d.get("startYardsToGoal"), d.get("endYardsToGoal")
        if res == "TD" or (start is not None and start <= 20) or (end is not None and end <= 20):
            pts = 7.0 if res == "TD" else 3.0 if res == "FG" else 0.0
            for side, team in (("off", o), ("def", df)):
                rz[side][team][0] += pts
                rz[side][team][1] += 1
    lg_rz = sum(v[0] for v in rz["off"].values()) / max(sum(v[1] for v in rz["off"].values()), 1)
    lg_to = sum(v[0] for v in to["off"].values()) / max(sum(v[1] for v in to["off"].values()), 1)
    out = defaultdict(dict)
    for side in ("off", "def"):
        for t, (p, n) in rz[side].items():
            out[t][f"rz_{side}"] = (p + lg_rz * rz_prior) / (n + rz_prior)
        for t, (k, n) in to[side].items():
            out[t][f"to_{side}"] = (k + lg_to * to_prior) / (n + to_prior)
    return dict(out), lg_rz, lg_to


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
                 fcs_weight=1.0, huber_k=None, off_offsets=None, def_offsets=None, matchup=0.0, priors=None,
                 finishing=False, fixed_hfa=False):
        self.fbs = fbs_teams(games)
        self.fcs_weight = fcs_weight
        weight = self._weight if fcs_weight != 1.0 and self.fbs else None
        # Rush/pass ratings see about half the plays; halve their penalty so they are as spread
        # out as the overall rating (otherwise the matchup term is mostly extra shrinkage).
        # fixed_hfa holds home field at its full-season value (adjust.HOME_FIELD). Fitted week by
        # week it's inflated 2-4x early in the season, which biased spreads toward home teams; the
        # spread model fixes it. Totals keep it free: the inflated term soaks up early home routs of
        # weak teams that would otherwise inflate offensive ratings (fixing it hurt totals badly).
        self.models, self.obs = adjust.fit_all(game_rows, games, alpha, weight, huber_k, subset_alpha_scale=0.5,
                                               priors=priors, hfa_fixed=adjust.HOME_FIELD if fixed_hfa else None)
        self.epa = self.models["epa"]
        self.matchup = matchup if "rush_epa" in self.models and "pass_epa" in self.models else 0.0
        self.run_off, self.run_def, self.run_lg = run_shares(self.obs)
        self.pace = pace(self.obs)
        all_plays = [o["stats"]["plays"] for o in self.obs if o["stats"].get("plays")]
        self.avg_plays = statistics.mean(all_plays)
        self.tempo, self.lg_secs, self.clock = tempo(drives) if drives else ({}, None, GAME_SECONDS)
        self.finishing = bool(finishing and drives)
        self.finish, self.lg_rz, self.lg_to = finishing_rates(drives) if self.finishing else ({}, 0.0, 0.0)
        self.off_offsets = off_offsets or {}
        self.def_offsets = def_offsets or {}
        self.games_played = defaultdict(set)
        for o in self.obs:
            self.games_played[o["offense"]].add(o["game_id"])
        self._fit_points(games)
        self._consistency()

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

    def _expected(self, name, off, dfn, home):
        m = self.models[name]
        i = m["intercept"]
        return m["off"].get(off, i) + m["def"].get(dfn, i) - i + m["hfa"] * home

    def run_rate(self, off, dfn):
        """Expected share of A's plays that are runs: A's tendency, nudged by what B's opponents do."""
        r = self.run_off.get(off, self.run_lg) + self.run_def.get(dfn, self.run_lg) - self.run_lg
        return min(max(r, 0.2), 0.8)

    def features(self, off, dfn, home, injuries=False, use_matchup=True):
        exp_epa = self._expected("epa", off, dfn, home)
        if self.matchup and use_matchup:
            # Run/pass matchup: a strong run offense against a weak run defense gets credit
            # that the overall ratings average away.
            r = self.run_rate(off, dfn)
            split = r * self._expected("rush_epa", off, dfn, home) + (1 - r) * self._expected("pass_epa", off, dfn, home)
            # Rush and pass EPA sit on a different level from overall EPA; keep the overall
            # level and add the matchup's deviation from what the split model expects on average.
            lvl = r * self.models["rush_epa"]["intercept"] + (1 - r) * self.models["pass_epa"]["intercept"]
            exp_epa += self.matchup * ((split - lvl) - (exp_epa - self.epa["intercept"]))
        if injuries:
            exp_epa += self.off_offsets.get(off, 0.0) + self.def_offsets.get(dfn, 0.0)
        return exp_epa, self._plays(off, dfn)

    def _consistency(self, prior_games=4):
        """Game-to-game spread of each offense's and defense's EPA around what the ratings expected.

        A residual is a game's EPA/play minus the matchup expectation; its spread per team is
        shrunk toward the league's (prior_games pseudo-games), since a few games say little
        about variance. Scaled by sqrt(plays) so short games don't look erratic.
        """
        e, i = self.epa, self.epa["intercept"]
        res_off, res_def, allr = defaultdict(list), defaultdict(list), []
        for o in self.obs:
            ppa, plays = o["stats"].get("ppa"), o["stats"].get("plays")
            if ppa is None or not plays:
                continue
            exp = e["off"].get(o["offense"], i) + e["def"].get(o["defense"], i) - i + e["hfa"] * o["home"]
            r = (ppa - exp) * math.sqrt(plays / 60)
            res_off[o["offense"]].append(r)
            res_def[o["defense"]].append(r)
            allr.append(r)
        lg_var = statistics.pvariance(allr) if len(allr) > 1 else 0.0
        shrink = lambda rs: (sum(x * x for x in rs) + lg_var * prior_games) / (len(rs) + prior_games)  # noqa: E731
        self.lg_sd = math.sqrt(lg_var)
        self.sd_off = {t: math.sqrt(shrink(rs)) for t, rs in res_off.items()}
        self.sd_def = {t: math.sqrt(shrink(rs)) for t, rs in res_def.items()}

    def volatility(self, home, away):
        """Relative volatility of a game: 1 = league-typical, higher = less consistent teams."""
        if not self.lg_sd:
            return 1.0
        sds = [self.sd_off.get(home, self.lg_sd), self.sd_def.get(away, self.lg_sd),
               self.sd_off.get(away, self.lg_sd), self.sd_def.get(home, self.lg_sd)]
        return math.sqrt(sum(x * x for x in sds) / 4) / self.lg_sd

    def _finish_terms(self, off, dfn, exp_plays):
        """Extra regression columns: red zone and turnover matchup, scaled by volume."""
        if not self.finishing:
            return ()
        a, b = self.finish.get(off, {}), self.finish.get(dfn, {})
        rz = a.get("rz_off", self.lg_rz) + b.get("rz_def", self.lg_rz) - 2 * self.lg_rz
        to = a.get("to_off", self.lg_to) + b.get("to_def", self.lg_to) - 2 * self.lg_to
        return (exp_plays * rz, exp_plays * to)

    def _fit_points(self, games):
        by_id = {g["id"]: g for g in games}
        X, y, w = [], [], []
        for o in self.obs:
            pts = _points(by_id.get(o["game_id"], {}), o["offense"])
            if pts is None:
                continue
            exp_epa, exp_plays = self.features(o["offense"], o["defense"], o["home"])
            X.append((1.0, exp_plays, exp_plays * exp_epa) + self._finish_terms(o["offense"], o["defense"], exp_plays))
            y.append(pts)
            w.append(o.get("weight", 1.0))
        sw = np.sqrt(np.array(w))
        self.coef, *_ = np.linalg.lstsq(np.array(X) * sw[:, None], np.array(y, dtype=float) * sw, rcond=None)

    def team_points(self, off, dfn, home, injuries=True, use_matchup=True):
        exp_epa, exp_plays = self.features(off, dfn, home, injuries, use_matchup)
        x = (1.0, exp_plays, exp_plays * exp_epa) + self._finish_terms(off, dfn, exp_plays)
        return float(self.coef @ np.array(x))

    def project(self, game, injuries=True, use_matchup=True):
        home, away = game["homeTeam"], game["awayTeam"]
        h = 0 if game.get("neutralSite") else 1
        return (self.team_points(home, away, h, injuries, use_matchup),
                self.team_points(away, home, -h, injuries, use_matchup))

    def matchup_detail(self, off, dfn, home):
        """Expected run rate and rush/pass EPA for this offense vs this defense."""
        if "rush_epa" not in self.models or "pass_epa" not in self.models:
            return None, None, None
        return (self.run_rate(off, dfn), self._expected("rush_epa", off, dfn, home),
                self._expected("pass_epa", off, dfn, home))


def consensus_total(line_row):
    """Median closing over/under across books, and the opening median."""
    lines = line_row.get("lines") or []
    close = [l["overUnder"] for l in lines if l.get("overUnder") is not None]
    opens = [l["overUnderOpen"] for l in lines if l.get("overUnderOpen") is not None]
    return (statistics.median(close) if close else None,
            statistics.median(opens) if opens else None,
            ", ".join(sorted({l["provider"] for l in lines if l.get("overUnder") is not None})))


def best_totals(line_row):
    """Best total for each side across books: lowest for an over, highest for an under."""
    lines = [l for l in line_row.get("lines") or [] if l.get("overUnder") is not None]
    if not lines:
        return None, "", None, ""
    lo = min(l["overUnder"] for l in lines)
    hi = max(l["overUnder"] for l in lines)
    books = lambda v: ", ".join(sorted(l["provider"] for l in lines if l["overUnder"] == v))  # noqa: E731
    return lo, books(lo), hi, books(hi)


def consensus_spread(line_row):
    """Median closing and opening home spread (negative = home favored)."""
    lines = line_row.get("lines") or []
    close = [l["spread"] for l in lines if l.get("spread") is not None]
    opens = [l["spreadOpen"] for l in lines if l.get("spreadOpen") is not None]
    return (statistics.median(close) if close else None, statistics.median(opens) if opens else None)


def _ats(margin, spread):
    """Home result against the spread: >0 home covers, <0 away covers, 0 push."""
    return margin + spread


TEAM_HFA_PATH = os.path.join("data", "priors", "team_home_field.csv")


def load_team_hfa(path=TEAM_HFA_PATH):
    """{team: points of home field beyond the league-wide edge} (see scripts/home_field_backtest.py)."""
    if not os.path.exists(path):
        return {}
    with open(path, newline="") as f:
        return {r["team"]: float(r["home_edge_pts"]) for r in csv.DictReader(f)}


def board(model, games, lines, min_games=3, spread_model=None, weather=None, team_hfa=None, sp=None):
    """One row per game with a market total; spread columns too when there's a spread.

    Spread columns come from spread_model (a lightly shrunk model) when given. weather
    ({game id: conditions}, see cfb_stats.weather) adds a wind correction to the total.
    sp (cfb_stats.sp_plus.load) adds SP+ total and margin columns, for reference only."""
    weather = weather or {}
    by_id = {g["id"]: g for g in games}
    rows = []
    for lr in lines:
        g = by_id.get(lr["id"])
        total, total_open, books = consensus_total(lr)
        if g is None or total is None:
            continue
        home, away = g["homeTeam"], g["awayTeam"]
        n_home, n_away = len(model.games_played[home]), len(model.games_played[away])
        wx = weather.get(g["id"])
        w_adj = wx_mod.total_adjustment(wx)
        hp, ap = model.project(g)
        hp, ap = hp + w_adj / 2, ap + w_adj / 2
        healthy = sum(model.project(g, injuries=False)) + w_adj
        proj = hp + ap
        no_matchup = sum(model.project(g, use_matchup=False)) + w_adj
        h = 0 if g.get("neutralSite") else 1
        spread, spread_open = consensus_spread(lr)
        best_over, best_over_book, best_under, best_under_book = best_totals(lr)
        shp, sap = spread_model.project(g) if spread_model else (hp, ap)
        if team_hfa and not g.get("neutralSite"):
            shp += team_hfa.get(home, 0.0)  # team-specific home field, spreads only
        margin = shp - sap
        done = g.get("homePoints") is not None and g.get("awayPoints") is not None
        spread_edge = None if spread is None else _ats(margin, spread)
        sp_total, sp_margin = sp_plus.project(sp, home, away, bool(g.get("neutralSite")))
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
            "best_over": best_over,
            "best_over_book": best_over_book,
            "best_under": best_under,
            "best_under_book": best_under_book,
            "proj_away": round(ap, 1),
            "proj_home": round(hp, 1),
            "proj_total": round(proj, 1),
            "injury_adj": round(proj - healthy, 1),
            "exp_plays_away": round(model._plays(away, home), 1),
            "exp_plays_home": round(model._plays(home, away), 1),
            "exp_epa_away": round(model.features(away, home, -h, True)[0], 3),
            "exp_epa_home": round(model.features(home, away, h, True)[0], 3),
            "matchup_adj": round(proj - no_matchup, 1),
            "weather_adj": round(w_adj, 1),
            "wind_mph": wx.get("wind_mph") if wx else None,
            "gust_mph": wx.get("gust_mph") if wx else None,
            "precip_in": wx.get("precip_in") if wx else None,
            "temp_f": wx.get("temp_f") if wx else None,
            "dome": wx.get("dome") if wx else None,
            **{f"{k}_{side}": (None if v is None else round(v, 3))
               for side, (o, d, hh) in (("away", (away, home, -h)), ("home", (home, away, h)))
               for k, v in zip(("run_rate", "exp_rush_epa", "exp_pass_epa"), model.matchup_detail(o, d, hh))},
            "edge": round(proj - total, 1),
            "pick": "OVER" if proj > total else "UNDER",
            "volatility": round(model.volatility(home, away), 3),
            "games_home": n_home,
            "games_away": n_away,
            "enough_data": min(n_home, n_away) >= min_games,
            "actual_total": (g["homePoints"] + g["awayPoints"]) if done else None,
            # Spread: market_spread is the home line (-7 = home favored by 7).
            "market_spread": spread,
            "market_spread_open": spread_open,
            "proj_margin": round(margin, 1),
            "spread_edge": None if spread_edge is None else round(spread_edge, 1),
            "spread_pick": None if spread_edge is None else (
                f"{home} {spread:+g}" if spread_edge > 0 else f"{away} {-spread:+g}"),
            "actual_margin": (g["homePoints"] - g["awayPoints"]) if done else None,
            # SP+ (reference only, not used for picks): implied total and home margin.
            "sp_total": sp_total,
            "sp_margin": sp_margin,
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
    "all+matchup0.5": {"tempo": True, "fcs_weight": DEFAULT_FCS_WEIGHT, "huber_k": DEFAULT_HUBER_K, "matchup": 0.5},
    "all+matchup": {"tempo": True, "fcs_weight": DEFAULT_FCS_WEIGHT, "huber_k": DEFAULT_HUBER_K, "matchup": 1.0},
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


def load_season_lite(client, year):
    """load_season without passing/PPA, plus the raw box scores (for cfb_stats.defense)."""
    games = [dict(g, seasonType="regular") for g in client.games(year, "regular")]
    weeks = sorted({g["week"] for g in games if g.get("completed")})
    data = {"games": games, "weeks": weeks, "rows": {}, "drives": {}, "lines": {}, "box": {}}
    for w in weeks:
        data["rows"][w] = client.game_advanced_stats(year, w, "regular", True)
        data["drives"][w] = client.drives(year, w, "regular")
        data["lines"][w] = client.lines(year, w, "regular")
        data["box"][w] = client.get("/games/players", year=year, week=w, seasonType="regular")
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


def build_model(data, week, alpha=TOTALS_ALPHA, tempo=False, fcs_weight=1.0, huber_k=None, matchup=0.0,
                use_priors=False, finishing=False, qb=False, fixed_hfa=False, **offsets):
    done = [g for g in data["games"] if g.get("week", 99) < week]
    if qb:
        qb_off = qb_offsets(_before(data, "passing", week), _before(data, "drives", week))[0]
        merged = dict(offsets.get("off_offsets") or {})
        for t, v in qb_off.items():
            merged[t] = merged.get(t, 0.0) + v
        offsets["off_offsets"] = merged
    return TotalsModel(_before(data, "rows", week), done, alpha,
                       drives=_before(data, "drives", week) if tempo else None,
                       fcs_weight=fcs_weight, huber_k=huber_k, matchup=matchup,
                       priors=data.get("priors") if use_priors else None, finishing=finishing,
                       fixed_hfa=fixed_hfa, **offsets)


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


def weekly_card(rows, n=3, p4_only=False):
    """Each week's n biggest over edges and n biggest under edges -> [(row, side)]."""
    by_week = defaultdict(list)
    for r in rows:
        if r.get("enough_data") and (r.get("p4_game") or not p4_only):
            by_week[r["week"]].append(r)
    card = []
    for _, rs in sorted(by_week.items()):
        card += [(r, "OVER") for r in sorted(rs, key=lambda r: -r["edge"])[:n] if r["edge"] > 0]
        card += [(r, "UNDER") for r in sorted(rs, key=lambda r: r["edge"])[:n] if r["edge"] < 0]
    return card


def summarize_cards(results, sizes=(3,)):
    """Backtest of the weekly card, all games and P4 only, at closing and opening totals."""
    out = []
    print("\nweekly card (n biggest over edges + n biggest under edges each week):")
    for n in sizes:
        for p4 in (False, True):
            card = weekly_card(results, n, p4)
            for key, label in (("market_total", "close"), ("market_open", "open")):
                w = l = p = 0
                for r, side in card:
                    if r.get(key) is None:
                        continue
                    d = r["actual_total"] - r[key]
                    if d == 0:
                        p += 1
                    elif (d > 0) == (side == "OVER"):
                        w += 1
                    else:
                        l += 1
                if w + l:
                    pool = "P4" if p4 else "all"
                    roi = (w * 100 / 110 - l) / (w + l)
                    print(f"  top {n} each way, {pool:<3} vs {label}: {w}-{l}-{p}  {w / (w + l):.1%}  ROI {roi:+.1%}")
                    out.append({"n": n, "pool": pool, "line": label, "wins": w, "losses": l, "pushes": p,
                                "win_pct": round(w / (w + l), 3), "roi_110": round(roi, 3)})
    return out


def _ats_record(results, t, line_key="market_spread"):
    w = l = p = 0
    for r in results:
        line = r.get(line_key)
        if line is None or r.get("actual_margin") is None:
            continue
        edge = _ats(r["proj_margin"], line)
        if abs(edge) < t or edge == 0:
            continue
        res = _ats(r["actual_margin"], line)
        if res == 0:
            p += 1
        elif (res > 0) == (edge > 0):
            w += 1
        else:
            l += 1
    return w, l, p


def summarize_spreads(results, thresholds=(0, 2, 3, 5, 7, 10)):
    """Against-the-spread results at the closing and opening line."""
    rows = [r for r in results if r.get("market_spread") is not None and r.get("actual_margin") is not None]
    a = np.array([r["actual_margin"] for r in rows])
    p = np.array([r["proj_margin"] for r in rows])
    m = -np.array([r["market_spread"] for r in rows])  # market's expected home margin
    print(f"\nSPREADS: {len(rows)} games with a spread")
    print(f"margin RMSE model {math.sqrt(((a - p) ** 2).mean()):.1f}, market {math.sqrt(((a - m) ** 2).mean()):.1f}")
    print(f"corr(model edge, actual vs spread) = {np.corrcoef(p - m, a - m)[0, 1]:+.3f}")
    out = []
    for key, label in (("market_spread", "close"), ("market_spread_open", "open")):
        if label == "open":
            op = [r for r in rows if r.get("market_spread_open") is not None]
            if op:
                e = np.array([r["proj_margin"] + r["market_spread_open"] for r in op])
                mv = np.array([r["market_spread_open"] - r["market_spread"] for r in op])  # >0: line moved toward home
                print(f"corr(model edge vs open, line move toward home) = {np.corrcoef(e, mv)[0, 1]:+.3f}")
        print(f"  vs {label}: " + "  ".join(
            f"|e|>={t}: {w + l} {w / (w + l):.1%}" for t in thresholds
            for w, l, _ in [_ats_record(rows, t, key)] if w + l))
        for t in thresholds:
            w, l, pu = _ats_record(rows, t, key)
            n = w + l
            if n:
                out.append({"line": label, "min_edge": t, "bets": n + pu, "wins": w, "losses": l, "pushes": pu,
                            "win_pct": round(w / n, 3), "roi_110": round((w * 100 / 110 - l) / n, 3)})
    return out


def compare_spread_variants(results):
    rows = []
    print(f"\n{'spreads: variant':<20} {'games':>5} {'RMSE':>5}  {'|e|>=3':>13} {'|e|>=5':>13} {'|e|>=7':>13}")
    for name, res in results.items():
        res = [r for r in res if r.get("market_spread") is not None and r.get("actual_margin") is not None]
        a = np.array([r["actual_margin"] for r in res])
        p = np.array([r["proj_margin"] for r in res])
        rmse = math.sqrt(((a - p) ** 2).mean())
        row, cells = {"variant": name, "games": len(res), "margin_rmse": round(rmse, 2)}, []
        for t in (3, 5, 7):
            w, l, _ = _ats_record(res, t)
            pct = w / (w + l) if w + l else float("nan")
            row[f"win_pct_edge{t}"], row[f"bets_edge{t}"] = round(pct, 3), w + l
            cells.append(f"{w + l:>4} {pct:>6.1%}")
        print(f"{name:<20} {len(res):>5} {rmse:>5.1f}  " + "  ".join(f"{c:>13}" for c in cells))
        rows.append(row)
    mkt = [r for r in next(iter(results.values())) if r.get("market_spread") is not None and r.get("actual_margin") is not None]
    a = np.array([r["actual_margin"] for r in mkt])
    m = -np.array([r["market_spread"] for r in mkt])
    print(f"{'market (closing)':<20} {len(mkt):>5} {math.sqrt(((a - m) ** 2).mean()):>5.1f}")
    return rows


def qb_offsets(passing, drives, prior_att=150):
    """Offense EPA/play offsets for each team's expected QB vs its season-average QB play.

    passing: QB-game rows (gameId, team, playerId, attempts, ppa per pass) from games already
    played. Each QB's EPA per pass is shrunk toward the league by prior_att attempts. The
    expected starter is the QB with the most attempts in the team's latest game. The offset
    is (expected QB - team's attempt-weighted QB average) x the team's pass share, so it's
    zero for a team that has used one QB all season and matters after a QB change.
    Returns ({team: offset}, {team: expected QB name}).
    """
    qbs, latest = defaultdict(lambda: [0.0, 0, ""]), {}
    tot_ppa = tot_att = 0.0
    order = sorted({r["gameId"] for r in passing})
    rank = {g: i for i, g in enumerate(order)}
    for r in passing:
        att, ppa = r.get("attempts") or 0, r.get("ppa")
        if att <= 0 or ppa is None:
            continue
        q = qbs[(r["team"], r["playerId"])]
        q[0] += ppa * att
        q[1] += att
        q[2] = r.get("player") or q[2]
        tot_ppa += ppa * att
        tot_att += att
        g = rank[r["gameId"]]
        cur = latest.get(r["team"])
        if cur is None or g > cur[0] or (g == cur[0] and att > cur[2]):
            latest[r["team"]] = (g, r["playerId"], att)
    if not tot_att:
        return {}, {}
    lg = tot_ppa / tot_att
    rating = {k: (v[0] + lg * prior_att) / (v[1] + prior_att) for k, v in qbs.items()}
    team_att, team_mean = defaultdict(float), defaultdict(float)
    for (team, pid), v in qbs.items():
        team_att[team] += v[1]
        team_mean[team] += rating[(team, pid)] * v[1]
    plays = defaultdict(float)
    for d in drives:
        plays[d["offense"]] += d.get("plays") or 0
    offsets, names = {}, {}
    for team, (_, pid, _) in latest.items():
        mean = team_mean[team] / team_att[team]
        share = min(team_att[team] / plays[team], 0.75) if plays.get(team) else 0.5
        offsets[team] = (rating[(team, pid)] - mean) * share
        names[team] = qbs[(team, pid)][2]
    return offsets, names


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
    p.add_argument("--no-weather", action="store_true", help="skip the Open-Meteo forecast and wind correction")
    p.add_argument("--spread-alpha", type=float, default=SPREAD_ALPHA, help="ridge penalty for the spread model")
    p.add_argument("--matchup", type=float, default=DEFAULT_MATCHUP,
                   help="weight of the run/pass matchup split in totals (0 = overall EPA only)")
    p.add_argument("--fcs-weight", type=float, default=DEFAULT_FCS_WEIGHT,
                   help="weight of games vs FCS opponents in the fit (1 = full)")
    p.add_argument("--huber-k", type=float, default=DEFAULT_HUBER_K,
                   help="down-weight single-game results beyond k robust SDs (0 = off)")
    p.add_argument("--injuries", default=None, help="CSV: team,player,status[,side,epa_delta]")
    p.add_argument("--sp-plus", default=None,
                   help="CSV of current SP+ (team,sp,off,def); default data/<year>/sp_plus_week<N>.csv if it exists")
    p.add_argument("--min-edge", type=float, default=DEFAULT_MIN_EDGE, help="points of edge to list as a pick")
    p.add_argument("--card", type=int, default=3, help="size of the weekly card (biggest over and under edges)")
    p.add_argument("--min-games", type=int, default=3, help="games of data each team needs")
    p.add_argument("--backtest", action="store_true", help="walk-forward backtest of --year instead")
    p.add_argument("--cache", default=None, help="directory to cache API responses (backtests)")
    args = p.parse_args(argv)

    client = CFBDClient(api_key=args.api_key, cache_dir=args.cache)
    out_dir = os.path.join(args.out, str(args.year))
    os.makedirs(out_dir, exist_ok=True)
    chosen = {"alpha": args.alpha, "tempo": not args.no_tempo, "fcs_weight": args.fcs_weight,
              "huber_k": args.huber_k or None, "matchup": args.matchup}

    if args.backtest:
        data = load_season(client, args.year)
        try:  # the spread model uses preseason priors, fit on the other seasons (leave-one-out)
            data["priors"] = priors_mod.build(client, args.year,
                                              [y for y in (2023, 2024, 2025) if y != args.year])[0]
        except Exception as e:
            print(f"priors unavailable ({e}); spread backtest runs without them")
        spread_variants_cfg = {f"spread a{a:g}": dict(chosen, alpha=a, matchup=0.0, fixed_hfa=True) for a in (25, 75, 150)}
        spread_variants_cfg["spread a25 free home field"] = dict(chosen, alpha=25, matchup=0.0)
        variants = dict(VARIANTS, chosen=chosen,
                        spread_model=dict(chosen, alpha=args.spread_alpha, matchup=0.0, fixed_hfa=True,
                                          use_priors="priors" in data),
                        **spread_variants_cfg)
        results = backtest(data, min_games=args.min_games, variants=variants)
        comparison = compare_variants({k: v for k, v in results.items() if k in VARIANTS or k == "chosen"})
        print("\nchosen settings:", chosen)
        summary, open_summary = summarize_backtest(results["chosen"])
        qb = qb_out_backtest(data, min_games=args.min_games, model_kw=chosen)
        qb_summary = summarize_qb_backtest(qb)
        card_summary = summarize_cards(results["chosen"], sizes=(3, 5))
        write_csv(os.path.join(out_dir, "totals_backtest_card.csv"), card_summary)
        spread_variants = compare_spread_variants(results)
        spread_summary = summarize_spreads(results["spread_model"])
        write_csv(os.path.join(out_dir, "spreads_backtest_variants.csv"), spread_variants)
        write_csv(os.path.join(out_dir, "spreads_backtest_summary.csv"), spread_summary)
        write_csv(os.path.join(out_dir, "totals_backtest_open.csv"), open_summary)
        if qb_summary:
            write_csv(os.path.join(out_dir, "totals_backtest_qb_summary.csv"), qb_summary)
        spread_cols = ("market_spread", "market_spread_open", "proj_margin", "spread_edge", "spread_pick", "actual_margin")
        write_csv(os.path.join(out_dir, "totals_backtest_games.csv"),
                  [{k: v for k, v in r.items() if k not in spread_cols} for r in results["chosen"]])
        write_csv(os.path.join(out_dir, "spreads_backtest_games.csv"),
                  [{k: r[k] for k in ("game_id", "week", "away", "home", "neutral", "p4_game") + spread_cols}
                   for r in results["spread_model"]])
        write_csv(os.path.join(out_dir, "totals_backtest_summary.csv"), summary)
        write_csv(os.path.join(out_dir, "totals_backtest_variants.csv"), comparison)
        if qb:
            write_csv(os.path.join(out_dir, "totals_backtest_qb_out.csv"),
                      [{k: v for k, v in r.items() if k not in spread_cols} for r in qb])
        return

    games = [dict(g, seasonType="regular") for g in client.games(args.year, "regular")]
    week = args.week or upcoming_week(games)
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
                        huber_k=args.huber_k or None, off_offsets=off_offsets, def_offsets=def_offsets,
                        matchup=args.matchup)
    lines = client.lines(args.year, week, "regular")
    upcoming = [g for g in games if g.get("week") == week and not g.get("completed")]
    # Preseason priors (cfb_stats.priors) made spreads more accurate but totals less, so only the
    # spread model uses them. Build with: python -m cfb_stats.priors --build <year>
    spread_priors = priors_mod.load_priors(args.year)
    spread_model = TotalsModel(game_rows, done, args.spread_alpha, drives=drives, fcs_weight=args.fcs_weight,
                               huber_k=args.huber_k or None, off_offsets=off_offsets, def_offsets=def_offsets,
                               priors=spread_priors, fixed_hfa=True)
    weather = {}
    if not args.no_weather:
        try:
            weather = wx_mod.game_weather(upcoming, client.get("/venues"))
            print(f"weather: forecasts for {len(weather)} of {len(upcoming)} games")
        except Exception as e:  # weather is optional; never block the board on it
            print(f"weather: forecast unavailable ({e}); no wind correction applied")
    sp = sp_plus.load(args.sp_plus or sp_plus.path_for(out_dir, week))
    if sp:
        missing = sorted({t for g in upcoming for t in (g["homeTeam"], g["awayTeam"])
                          if g.get("homeConference") in P4_CONFERENCES or g.get("awayConference") in P4_CONFERENCES}
                         - set(sp["teams"]))
        print(f"SP+: {len(sp['teams'])} teams (reference only)"
              + (f"; no SP+ for {len(missing)} teams in P4 games: {', '.join(missing)}" if missing else ""))
    rows = board(model, upcoming, lines, args.min_games, spread_model, weather, load_team_hfa(), sp)

    path = os.path.join(out_dir, f"totals_week{week}.csv")
    write_csv(path, rows)
    picks = [r for r in rows if abs(r["edge"]) >= args.min_edge and r["enough_data"]]
    print(f"week {week}: {len(rows)} games with a market total, wrote {path}")
    print(f"{len(picks)} with |edge| >= {args.min_edge} (lean only: see --backtest):")
    for r in picks:
        inj_note = f"  (injuries {r['injury_adj']:+.1f})" if r["injury_adj"] else ""
        print(f"  {r['away']:>20} @ {r['home']:<20} mkt {r['market_total']:>5}  proj {r['proj_total']:>5}  "
              f"{r['pick']:<5} {r['edge']:+5.1f}{inj_note}")
    card = weekly_card(rows, args.card, p4_only=True)
    if card:
        print(f"\nweekly card: {args.card} biggest over and under edges, P4 games (see BACKTEST.md):")
        for r, side in card:
            print(f"  {side:<5} {r['market_total']:>5}  {r['away']} @ {r['home']}  (proj {r['proj_total']}, edge {r['edge']:+.1f})")
    print("\nspreads: projected margins are in the CSV (proj_margin, spread_edge) for reference only.")
    print("  The backtest found no edge against the spread (49-51% at every threshold, 2024 and 2025),")
    print("  and early-season margins are very noisy, so no spread picks are listed. See BACKTEST.md.")


if __name__ == "__main__":
    main()
