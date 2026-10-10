import os
import tempfile
import unittest

from cfb_stats.api import CFBDClient


class StoreTests(unittest.TestCase):
    def setUp(self):
        CFBDClient._memo.clear()
        self.tmp = tempfile.mkdtemp()
        self.calls = []

    def client(self):
        c = CFBDClient(api_key="x", store_dir=self.tmp)
        c._get = lambda path, params: self.calls.append((path, params["week"])) or [{"week": params["week"]}]
        return c

    def test_finished_weeks_are_stored(self):
        self.client().drives(2026, 3, "regular", store=True)
        self.assertTrue(os.path.exists(os.path.join(self.tmp, "2026", "cache", "drives_regular_week3.json.gz")))
        CFBDClient._memo.clear()  # a new run in a fresh process
        self.assertEqual(self.client().drives(2026, 3, "regular", store=True), [{"week": 3}])
        self.assertEqual(len(self.calls), 1)

    def test_recent_weeks_are_fetched_once_per_run(self):
        c = self.client()
        c.game_advanced_stats(2026, 6, "regular")
        c.game_advanced_stats(2026, 6, "regular")
        self.assertEqual(len(self.calls), 1)
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "2026", "cache")))


if __name__ == "__main__":
    unittest.main()
