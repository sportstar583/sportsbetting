import random
import unittest

from cfb_stats import adjust, injuries as inj
from cfb_stats.totals import TotalsModel, board, consensus_total, finishing_rates, qb_offsets, run_shares, tempo, weekly_card

TEAMS = ["A", "B", "C", "D", "E", "F"]
# True offensive quality (EPA/play); defenses all average.
QUALITY = {"A": 0.35, "B": 0.25, "C": 0.15, "D": 0.05, "E": -0.05, "F": -0.15}


def synthetic_season(weeks=6, seed=1):
    rng = random.Random(seed)
    games, rows, gid = [], [], 0
    for week in range(1, weeks + 1):
        order = TEAMS[:]
        rng.shuffle(order)
        for home, away in zip(order[::2], order[1::2]):
            gid += 1
            sides = {}
            for team in (home, away):
                epa = QUALITY[team] + rng.gauss(0, 0.03)
                plays = 60
                sides[team] = ({"ppa": epa, "plays": plays}, round(plays * (0.2 + 0.6 * epa) + rng.gauss(0, 2)))
            games.append({"id": gid, "week": week, "homeTeam": home, "awayTeam": away, "neutralSite": False,
                          "homePoints": sides[home][1], "awayPoints": sides[away][1], "completed": True})
            for team, opp in ((home, away), (away, home)):
                rows.append({"gameId": gid, "team": team, "opponent": opp,
                             "offense": sides[team][0], "defense": sides[opp][0]})
    return games, rows


class TotalsTests(unittest.TestCase):
    def test_consensus_total(self):
        lr = {"lines": [{"provider": "X", "overUnder": 50, "overUnderOpen": 48},
                        {"provider": "Y", "overUnder": 52, "overUnderOpen": None},
                        {"provider": "Z", "overUnder": None}]}
        self.assertEqual(consensus_total(lr), (51, 48, "X, Y"))
        self.assertEqual(consensus_total({"lines": []})[0], None)

    def test_better_offenses_project_higher(self):
        games, rows = synthetic_season()
        model = TotalsModel(rows, games, alpha=10)
        high = sum(model.project({"homeTeam": "A", "awayTeam": "B", "neutralSite": True}))
        low = sum(model.project({"homeTeam": "E", "awayTeam": "F", "neutralSite": True}))
        self.assertGreater(high, low + 10)

    def test_board(self):
        games, rows = synthetic_season()
        model = TotalsModel(rows, games, alpha=10)
        upcoming = [{"id": 999, "week": 7, "homeTeam": "A", "awayTeam": "B", "neutralSite": False,
                     "homePoints": None, "awayPoints": None},
                    {"id": 998, "week": 7, "homeTeam": "E", "awayTeam": "F", "neutralSite": False}]
        lines = [{"id": 999, "lines": [{"provider": "X", "overUnder": 20.5}]},
                 {"id": 998, "lines": [{"provider": "X", "overUnder": 99.5}]},
                 {"id": 997, "lines": [{"provider": "X", "overUnder": 50}]}]  # no matching game
        rows_out = {r["game_id"]: r for r in board(model, upcoming, lines)}
        self.assertEqual(set(rows_out), {998, 999})
        self.assertEqual(rows_out[999]["pick"], "OVER")
        self.assertEqual(rows_out[998]["pick"], "UNDER")
        self.assertTrue(rows_out[999]["enough_data"])
        self.assertIsNone(rows_out[999]["actual_total"])

    def test_tempo(self):
        drives = [
            # game 1: A runs 10 plays in 300s, B runs 10 plays in 200s
            {"gameId": 1, "offense": "A", "defense": "B", "plays": 10, "elapsed": {"minutes": 5, "seconds": 0}},
            {"gameId": 1, "offense": "B", "defense": "A", "plays": 10, "elapsed": {"minutes": 3, "seconds": 20}},
            {"gameId": 1, "offense": "B", "defense": "A", "plays": 0, "elapsed": {"minutes": 0, "seconds": 5}},  # ignored
        ]
        t, lg, clock = tempo(drives)
        self.assertEqual(t["A"]["s_off"], 30)
        self.assertEqual(t["B"]["s_off"], 20)
        self.assertEqual(t["A"]["s_def"], 20)
        self.assertAlmostEqual(t["A"]["pos_share"], 0.6)
        self.assertEqual((lg, clock), (25, 500))

    def test_fcs_weight_and_huber_damp_a_rout(self):
        games, rows = synthetic_season()
        # A also routs an FCS team, posting an absurd EPA.
        games.append({"id": 500, "week": 6, "homeTeam": "A", "awayTeam": "FCS U", "neutralSite": False,
                      "homeClassification": "fbs", "awayClassification": "fcs",
                      "homePoints": 80, "awayPoints": 0, "completed": True})
        rows += [{"gameId": 500, "team": "A", "opponent": "FCS U",
                  "offense": {"ppa": 1.5, "plays": 60}, "defense": {"ppa": -0.5, "plays": 60}}]
        for g in games:
            g.setdefault("homeClassification", "fbs")
            g.setdefault("awayClassification", "fbs")
        full = TotalsModel(rows, games, alpha=10).epa["off"]["A"]
        damped = TotalsModel(rows, games, alpha=10, fcs_weight=0.5, huber_k=1.5).epa["off"]["A"]
        self.assertLess(damped, full)

    def test_huber_downweights_outlier(self):
        obs = [{"offense": o, "defense": d, "home": h, "stats": {"ppa": v, "plays": 60}}
               for o, d, h, v in [("A", "B", 1, 0.1), ("A", "C", -1, 0.1), ("A", "D", 1, 0.1), ("A", "E", -1, 1.5),
                                  ("B", "A", -1, 0.1), ("C", "A", 1, 0.1), ("D", "A", -1, 0.1), ("E", "A", 1, 0.1)]]
        plain = adjust.fit(obs, ("ppa",), None, alpha=1)["off"]["A"]
        robust = adjust.fit(obs, ("ppa",), None, alpha=1, huber_k=1.5)["off"]["A"]
        self.assertLess(robust, plain)

    def test_injury_offsets_lower_projection(self):
        games, rows = synthetic_season()
        healthy = TotalsModel(rows, games, alpha=10)
        hurt = TotalsModel(rows, games, alpha=10, off_offsets={"A": -0.1})
        g = {"homeTeam": "A", "awayTeam": "B", "neutralSite": True}
        self.assertLess(sum(hurt.project(g)), sum(healthy.project(g)))
        self.assertEqual(sum(hurt.project(g, injuries=False)), sum(healthy.project(g)))


    def test_run_pass_matchup(self):
        # R runs well, passes badly and runs 70% of the time. W has a weak run defense and
        # strong pass defense, S the opposite. The overall ratings can't see that R attacks
        # W's weakness with most of its plays; the run/pass split can.
        rush_off = {"R": 0.30, "P": -0.10, "W": 0.10, "S": 0.10, "X": 0.10, "Y": 0.10}
        pass_off = {"R": -0.10, "P": 0.30, "W": 0.10, "S": 0.10, "X": 0.10, "Y": 0.10}
        rush_def = {"W": 0.20, "S": -0.20, "R": 0.0, "P": 0.0, "X": 0.0, "Y": 0.0}
        pass_def = {"W": -0.20, "S": 0.20, "R": 0.0, "P": 0.0, "X": 0.0, "Y": 0.0}
        teams = list(rush_off)
        games, rows, gid = [], [], 0
        rng = random.Random(3)
        for week in range(1, 9):
            order = teams[:]
            rng.shuffle(order)
            for home, away in zip(order[::2], order[1::2]):
                gid += 1
                sides = {}
                for o, d in ((home, away), (away, home)):
                    r, p_ = rush_off[o] + rush_def[d], pass_off[o] + pass_def[d]
                    n_run = 42 if o == "R" else 30  # R is run-heavy: 70% runs
                    sides[o] = {"ppa": (n_run * r + (60 - n_run) * p_) / 60, "plays": 60,
                                "rushingPlays": {"ppa": r or 1e-6, "totalPPA": n_run * (r or 1e-6)},
                                "passingPlays": {"ppa": p_ or 1e-6, "totalPPA": (60 - n_run) * (p_ or 1e-6)}}
                games.append({"id": gid, "week": week, "homeTeam": home, "awayTeam": away, "neutralSite": False,
                              "homePoints": 28, "awayPoints": 28, "completed": True})
                for o, d in ((home, away), (away, home)):
                    rows.append({"gameId": gid, "team": o, "opponent": d, "offense": sides[o], "defense": sides[d]})
        off_share, _, lg = run_shares(TotalsModel(rows, games, alpha=1).obs)
        self.assertGreater(off_share["R"], 0.6)
        plain = TotalsModel(rows, games, alpha=1, matchup=0.0)
        mm = TotalsModel(rows, games, alpha=1, matchup=1.0)
        gain_vs_weak = mm.features("R", "W", 0)[0] - plain.features("R", "W", 0)[0]
        gain_vs_strong = mm.features("R", "S", 0)[0] - plain.features("R", "S", 0)[0]
        self.assertGreater(gain_vs_weak, 0)
        self.assertLess(gain_vs_strong, 0)
        self.assertEqual(mm.features("R", "W", 0, use_matchup=False), plain.features("R", "W", 0))

    def test_weekly_card(self):
        rows = [{"week": w, "edge": e, "p4_game": p4, "enough_data": ok}
                for w, e, p4, ok in [(1, 5, True, True), (1, 4, True, True), (1, 9, False, True),
                                     (1, 8, True, False), (1, -2, True, True), (1, -6, True, True),
                                     (1, 1, True, True), (2, -1, True, True)]]
        card = [(r["week"], r["edge"], side) for r, side in weekly_card(rows, 2, p4_only=True)]
        self.assertEqual(card, [(1, 5, "OVER"), (1, 4, "OVER"), (1, -6, "UNDER"), (1, -2, "UNDER"),
                                (2, -1, "UNDER")])
        self.assertIn((1, 9, "OVER"), [(r["week"], r["edge"], s) for r, s in weekly_card(rows, 2)])

    def test_prior_pulls_toward_preseason_rating(self):
        obs = [{"offense": o, "defense": d, "home": h, "stats": {"ppa": 0.1, "plays": 60}}
               for o, d, h in [("A", "B", 1), ("B", "A", -1), ("A", "C", -1), ("C", "A", 1)]]
        flat = adjust.fit(obs, ("ppa",), None, alpha=10000)
        lifted = adjust.fit(obs, ("ppa",), None, alpha=10000, prior={"off": {"A": 0.2}})
        self.assertAlmostEqual(flat["off"]["A"] - flat["intercept"], 0.0, places=3)
        self.assertGreater(lifted["off"]["A"] - lifted["intercept"], 0.15)


    def test_finishing_rates(self):
        drives = [
            {"offense": "A", "defense": "B", "driveResult": "TD", "startYardsToGoal": 75, "endYardsToGoal": 0},
            {"offense": "A", "defense": "B", "driveResult": "FG", "startYardsToGoal": 60, "endYardsToGoal": 15},
            {"offense": "A", "defense": "B", "driveResult": "INT", "startYardsToGoal": 70, "endYardsToGoal": 40},
            {"offense": "B", "defense": "A", "driveResult": "PUNT", "startYardsToGoal": 80, "endYardsToGoal": 60},
            {"offense": "B", "defense": "A", "driveResult": "END OF HALF", "startYardsToGoal": 18, "endYardsToGoal": 10},
        ]
        rates, lg_rz, lg_to = finishing_rates(drives, rz_prior=0, to_prior=0)
        self.assertEqual(rates["A"]["rz_off"], 5.0)  # TD + FG on two trips; INT never reached the 20
        self.assertAlmostEqual(rates["A"]["to_off"], 1 / 3)
        self.assertEqual(rates["B"]["to_off"], 0.0)  # end-of-half drive ignored
        self.assertEqual((lg_rz, lg_to), (5.0, 0.25))


    def test_volatility(self):
        games, rows = synthetic_season()
        # Make A's offense erratic: alternate big over- and under-performances.
        for i, r in enumerate(x for x in rows if x["team"] == "A"):
            r["offense"] = dict(r["offense"], ppa=r["offense"]["ppa"] + (0.4 if i % 2 else -0.4))
        m = TotalsModel(rows, games, alpha=10)
        self.assertGreater(m.sd_off["A"], m.sd_off["B"])
        self.assertGreater(m.volatility("A", "B"), m.volatility("C", "D"))


    def test_qb_offsets_after_a_qb_change(self):
        passing = [
            {"gameId": 1, "team": "A", "playerId": "s", "player": "Starter", "attempts": 30, "ppa": 0.5},
            {"gameId": 2, "team": "A", "playerId": "s", "player": "Starter", "attempts": 30, "ppa": 0.5},
            {"gameId": 3, "team": "A", "playerId": "b", "player": "Backup", "attempts": 30, "ppa": -0.2},
            {"gameId": 1, "team": "B", "playerId": "x", "player": "Only QB", "attempts": 30, "ppa": 0.1},
            {"gameId": 3, "team": "B", "playerId": "x", "player": "Only QB", "attempts": 30, "ppa": 0.1},
        ]
        drives = [{"offense": "A", "plays": 180}, {"offense": "B", "plays": 120}]
        off, names = qb_offsets(passing, drives, prior_att=10)
        self.assertEqual(names, {"A": "Backup", "B": "Only QB"})
        self.assertLess(off["A"], 0)  # backup now starting, worse than A's season average
        self.assertAlmostEqual(off["B"], 0.0)  # one QB all season: no adjustment


    def test_team_home_field_moves_spread_only(self):
        games, rows = synthetic_season()
        model = TotalsModel(rows, games, alpha=10)
        g = [{"id": 999, "week": 7, "homeTeam": "A", "awayTeam": "B", "neutralSite": False}]
        lines = [{"id": 999, "lines": [{"provider": "X", "overUnder": 50, "spread": -3}]}]
        plain = board(model, g, lines, spread_model=model)[0]
        boosted = board(model, g, lines, spread_model=model, team_hfa={"A": 2.0})[0]
        self.assertAlmostEqual(boosted["proj_margin"] - plain["proj_margin"], 2.0, places=1)
        self.assertEqual(boosted["proj_total"], plain["proj_total"])
        neutral = board(model, [dict(g[0], neutralSite=True)], lines, spread_model=model, team_hfa={"A": 2.0})[0]
        self.assertEqual(neutral["proj_margin"], board(model, [dict(g[0], neutralSite=True)], lines, spread_model=model)[0]["proj_margin"])


    def test_transfer_net(self):
        from cfb_stats.priors import transfer_net
        rows = [{"position": "QB", "origin": "A", "destination": "B", "rating": "0.95", "stars": "4"},
                {"position": "CB", "origin": "C", "destination": "B", "rating": "", "stars": "4"},
                {"position": "WR", "origin": "B", "destination": "", "rating": "0.79", "stars": "3"}]
        net = transfer_net(rows)
        self.assertGreater(net["B"]["off"], net["A"]["off"])  # QB went A -> B
        self.assertGreater(net["B"]["def"], net["C"]["def"])  # 4-star CB went C -> B (stars fallback)


class InjuryTests(unittest.TestCase):
    def setUp(self):
        season = [
            {"id": "1", "name": "Star QB", "position": "QB", "team": "A",
             "averagePPA": {"all": 0.4}, "totalPPA": {"all": 120.0}},
            {"id": "2", "name": "Backup QB", "position": "QB", "team": "B",
             "averagePPA": {"all": -0.1}, "totalPPA": {"all": -10.0}},
            {"id": "3", "name": "Other QB", "position": "QB", "team": "C",
             "averagePPA": {"all": 0.1}, "totalPPA": {"all": 20.0}},
            {"id": "4", "name": "WR One", "position": "WR", "team": "A",
             "averagePPA": {"all": 0.6}, "totalPPA": {"all": 30.0}},
            {"id": "5", "name": "WR Two", "position": "WR", "team": "C",
             "averagePPA": {"all": 0.0}, "totalPPA": {"all": 0.0001}},
            {"id": "6", "name": "WR Three", "position": "WR", "team": "B",
             "averagePPA": {"all": 0.05}, "totalPPA": {"all": 2.0}},
        ]
        usage = [{"id": "1", "usage": {"overall": 0.5}}, {"id": "4", "usage": {"overall": 0.15}}]
        self.players, self.positions = inj.player_values(season, usage)

    def test_status_probabilities(self):
        self.assertEqual(inj._prob("Out"), 1.0)
        self.assertEqual(inj._prob("0.3"), 0.3)
        with self.assertRaises(ValueError):
            inj._prob("maybe")

    def test_offsets(self):
        injuries = [
            {"team": "A", "player": "star qb", "prob_out": 1.0, "side": "off", "epa_delta": None},
            {"team": "A", "player": "WR One", "prob_out": 0.4, "side": "off", "epa_delta": None},
            {"team": "B", "player": "Edge Rusher", "prob_out": 1.0, "side": "def", "epa_delta": 0.02},
            {"team": "B", "player": "Corner", "prob_out": 1.0, "side": "def", "epa_delta": None},
            {"team": "A", "player": "Nobody", "prob_out": 1.0, "side": "off", "epa_delta": None},
        ]
        off, dfn, detail = inj.team_offsets(injuries, self.players, self.positions)
        self.assertLess(off["A"], 0)  # losing a good QB and WR hurts the offense
        qb_only = inj.team_offsets(injuries[:1], self.players, self.positions)[0]["A"]
        self.assertLess(off["A"], qb_only)
        self.assertEqual(dfn, {"B": 0.02})
        notes = {d["player"]: d["note"] for d in detail}
        self.assertIn("skipped", notes["Corner"])
        self.assertIn("no offensive EPA", notes["Nobody"])


if __name__ == "__main__":
    unittest.main()
