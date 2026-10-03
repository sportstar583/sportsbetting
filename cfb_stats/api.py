"""Thin client for the CollegeFootballData.com API (https://api.collegefootballdata.com).

A free API key is required: https://collegefootballdata.com/key
"""

import os
import time

import requests

BASE_URL = "https://api.collegefootballdata.com"


class CFBDClient:
    def __init__(self, api_key=None, base_url=BASE_URL, retries=3):
        self.api_key = api_key or os.environ.get("CFBD_API_KEY")
        if not self.api_key:
            raise RuntimeError(
                "No API key. Set CFBD_API_KEY or pass --api-key "
                "(get a free key at https://collegefootballdata.com/key)."
            )
        self.base_url = base_url.rstrip("/")
        self.retries = retries
        self.session = requests.Session()
        self.session.headers.update(
            {"Authorization": f"Bearer {self.api_key}", "Accept": "application/json"}
        )

    def get(self, path, **params):
        params = {k: v for k, v in params.items() if v is not None}
        url = f"{self.base_url}{path}"
        for attempt in range(self.retries):
            resp = self.session.get(url, params=params, timeout=30)
            if resp.status_code == 429 or resp.status_code >= 500:
                time.sleep(2 ** attempt)
                continue
            resp.raise_for_status()
            return resp.json()
        resp.raise_for_status()
        return resp.json()

    def fbs_teams(self, year):
        return self.get("/teams/fbs", year=year)

    def season_stats(self, year):
        """Counting stats, long format: one row per (team, statName)."""
        return self.get("/stats/season", year=year)

    def advanced_season_stats(self, year, exclude_garbage_time=True):
        """EPA/PPA, success rate, explosiveness, havoc, etc. split offense/defense."""
        return self.get(
            "/stats/season/advanced",
            year=year,
            excludeGarbageTime=str(exclude_garbage_time).lower(),
        )

    def games(self, year, season_type):
        return self.get("/games", year=year, seasonType=season_type)
