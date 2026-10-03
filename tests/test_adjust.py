import random
import unittest

import numpy as np

from cfb_stats.adjust import fit_all, player_table, team_table


def simulate_season(seed=0, n_teams=40, n_weeks=12):
    """Teams with known true offense/defense EPA. The first half of the league only
    plays each other, as does the second half, and the second half is much weaker,
    so raw numbers are distorted by schedule and adjusted numbers should not be."""
    rng = random.Random(seed)
    teams = [f"T{i}" for i in range(n_teams)]
    half = n_teams // 2
    true_off = {t: rng.gauss(0.10 if i < half else -0.05, 0.08) for i, t in enumerate(teams)}
    true_def = {t: rng.gauss(0.00 if i < half else 0.15, 0.08) for i, t in enumerate(teams)}
    hfa = 0.03

    games, rows, gid = [], [], 0
    for week in range(1, n_weeks + 1):
        # Weeks 1-3 are "non-conference": strong half vs weak half. After that, in-group only.
        if week <= 3:
            strong, weak = teams[:half], teams[half:]
            rng.shuffle(weak)
            pairs = list(zip(strong, weak))
        else:
            pairs = []
            for block in (teams[:half], teams[half:]):
                order = block[:]
                rng.shuffle(order)
                pairs += list(zip(order[::2], order[1::2]))
        for home, away in pairs:
            gid += 1
            games.append({"id": gid, "week": week, "homeTeam": home, "awayTeam": away, "neutralSite": False})
            sides = {}
            for off, dfn, h in ((home, away, 1), (away, home, -1)):
                val = true_off[off] + true_def[dfn] - 0.05 + hfa * h + rng.gauss(0, 0.04)
                rush = val - 0.05 + rng.gauss(0, 0.03)
                sides[off] = {
                    "plays": 70, "ppa": val, "successRate": 0.42 + val / 3, "explosiveness": 1.2,
                    "rushingPlays": {"ppa": rush, "totalPPA": rush * 35, "successRate": 0.4},
                    "passingPlays": {"ppa": val + 0.05, "totalPPA": (val + 0.05) * 35, "successRate": 0.44},
                }
            rows.append({"gameId": gid, "team": home, "opponent": away, "offense": sides[home], "defense": sides[away]})
            rows.append({"gameId": gid, "team": away, "opponent": home, "offense": sides[away], "defense": sides[home]})
    return teams, true_off, true_def, games, rows


class AdjustTests(unittest.TestCase):
    def setUp(self):
        self.teams, self.true_off, self.true_def, self.games, self.rows = simulate_season()
        self.models, self.obs = fit_all(self.rows, self.games, alpha=20)

    def corr(self, est, truth):
        return np.corrcoef([est[t] for t in self.teams], [truth[t] for t in self.teams])[0, 1]

    def test_adjusted_beats_raw(self):
        epa = self.models["epa"]
        self.assertGreater(self.corr(epa["off"], self.true_off), 0.9)
        self.assertGreater(self.corr(epa["def"], self.true_def), 0.9)
        self.assertGreater(self.corr(epa["off"], self.true_off), self.corr(epa["raw_off"], self.true_off))
        self.assertGreater(self.corr(epa["def"], self.true_def), self.corr(epa["raw_def"], self.true_def))
        self.assertAlmostEqual(epa["hfa"], 0.03, delta=0.01)

    def test_rush_pass_models_fit(self):
        for name in ("rush_epa", "pass_epa", "success_rate", "rush_success_rate"):
            self.assertIn(name, self.models)

    def test_fcs_opponent_without_own_row(self):
        # Only the FBS team's row exists; both matchups must still be used.
        rows = [r for r in self.rows if r["team"] != "T1"]
        _, obs = fit_all(rows, self.games, alpha=20)
        self.assertEqual(len(obs), len(self.obs))

    def test_team_table(self):
        conf = {t: "SEC" for t in self.teams[:4]}
        table = team_table(self.models, self.obs, conf, set(self.teams))
        self.assertEqual(len(table), 4)
        row = table[0]
        for col in ("off_epa_raw", "off_epa_adj", "off_epa_adj_rank", "def_rush_epa_adj", "net_epa_adj", "sos_opp_def_epa"):
            self.assertIn(col, row)
        nets = [r["net_epa_adj"] for r in table]
        self.assertEqual(nets, sorted(nets, reverse=True))

    def test_player_adjustment_direction(self):
        rush = self.models["rush_epa"]
        tough = min(rush["def"], key=rush["def"].get)
        soft = max(rush["def"], key=rush["def"].get)
        players = [
            {"id": 1, "name": "Back A", "position": "RB", "team": "T0", "opponent": tough, "averagePPA": {"all": 0.2, "rush": 0.2, "pass": None}},
            {"id": 2, "name": "Back B", "position": "RB", "team": "T0", "opponent": soft, "averagePPA": {"all": 0.2, "rush": 0.2, "pass": None}},
            {"id": 3, "name": "Other", "position": "RB", "team": "NotP4", "opponent": soft, "averagePPA": {"rush": 0.5}},
        ]
        table = {r["player"]: r for r in player_table(players, self.models, {"T0": "SEC"})}
        self.assertNotIn("Other", table)
        # Same raw EPA/rush, but Back A did it against the best run defense.
        self.assertEqual(table["Back A"]["rush_epa_raw"], table["Back B"]["rush_epa_raw"])
        self.assertGreater(table["Back A"]["rush_epa_adj"], table["Back A"]["rush_epa_raw"])
        self.assertLess(table["Back B"]["rush_epa_adj"], table["Back B"]["rush_epa_raw"])
        self.assertIsNone(table["Back A"]["pass_epa_raw"])


if __name__ == "__main__":
    unittest.main()
