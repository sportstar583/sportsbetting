import unittest

from cfb_stats import injuries as inj
from cfb_stats.availability import entries, match_player, parse_entry

ROSTERS = {"Ohio State": [
    {"id": "11", "firstName": "Anthony", "lastName": "Rogers", "jersey": 21},
    {"id": "12", "firstName": "Jay", "lastName": "Timmons", "jersey": 25},
    {"id": "13", "firstName": "Siale", "lastName": "Taupaki", "jersey": 92},
]}


class AvailabilityTests(unittest.TestCase):
    def test_parse_entry(self):
        self.assertEqual(parse_entry("WR #0 Chase Sowell"), ("WR", 0, "Chase Sowell"))
        self.assertEqual(parse_entry("S CJ Christian"), ("S", None, "CJ Christian"))

    def test_match_player(self):
        self.assertEqual(match_player("Ohio State", 21, "Anthony “Turbo” Rogers", ROSTERS), "11")
        self.assertEqual(match_player("Ohio State", None, "Jay Timmons", ROSTERS), "12")
        # Name differs from roster but the jersey is unique on the team.
        self.assertEqual(match_player("Ohio State", 92, "Siale Liku", ROSTERS), "13")
        self.assertIsNone(match_player("Ohio State", 99, "Nobody Here", ROSTERS))

    def test_entries_skip_available_exempt_and_specialists(self):
        reports = [{"ReportType": "Update 2", "publishDate": "2026-10-02", "postedTime": "19:00:00", "games": [
            {"teamDisplayName": "Ohio State", "rows": [
                {"name": "RB #21 Anthony “Turbo” Rogers", "status": "Out", "exemptStatus": "NonExempt"},
                {"name": "CB #25 Jay Timmons", "status": "Questionable", "exemptStatus": "NonExempt"},
                {"name": "WR #1 Healthy Guy", "status": "Available", "exemptStatus": "NonExempt"},
                {"name": "QB #9 Walk On", "status": "Exempt", "exemptStatus": "Exempt"},
                {"name": "K #95 Kicker Guy", "status": "Out", "exemptStatus": "NonExempt"},
            ]}]}]
        rows = entries(reports, ROSTERS)
        self.assertEqual([(r["player"], r["side"], r["player_id"]) for r in rows],
                         [("Anthony “Turbo” Rogers", "off", "11"), ("Jay Timmons", "def", "12")])
        self.assertEqual(inj._prob("Game Time Decision"), 0.5)
        self.assertEqual(inj._prob("Out - (1st Half)"), 0.5)

    def test_defender_values(self):
        stats = [
            {"team": "A", "playerId": "1", "player": "Star", "category": "defensive", "statType": "TOT", "stat": "30"},
            {"team": "A", "playerId": "1", "player": "Star", "category": "defensive", "statType": "SACKS", "stat": "5"},
            {"team": "A", "playerId": "2", "player": "Depth", "category": "defensive", "statType": "TOT", "stat": "5"},
            {"team": "A", "playerId": "2", "player": "Depth", "category": "interceptions", "statType": "INT", "stat": "0"},
            {"team": "A", "playerId": "2", "player": "Depth", "category": "interceptions", "statType": "YDS", "stat": "40"},
        ]
        d = inj.defender_values(stats)
        self.assertAlmostEqual(d[("A", "id:1")]["share"], 45 / 50)
        self.assertAlmostEqual(d[("A", "depth")]["share"], 5 / 50)
        injuries = [{"team": "A", "player": "Star", "player_id": "1", "prob_out": 1.0, "side": "def", "epa_delta": None}]
        _, dfn, _ = inj.team_offsets(injuries, {}, {}, d)
        self.assertAlmostEqual(dfn["A"], inj.DEF_SCALE * 0.9)


if __name__ == "__main__":
    unittest.main()
