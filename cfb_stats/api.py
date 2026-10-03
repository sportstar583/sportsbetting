"""Thin client for the CollegeFootballData.com API (https://api.collegefootballdata.com).

A free API key is required: https://collegefootballdata.com/key
"""

import hashlib
import json
import os
import time

import requests

BASE_URL = "https://api.collegefootballdata.com"


class CFBDClient:
    def __init__(self, api_key=None, base_url=BASE_URL, retries=5, cache_dir=None):
        self.api_key = api_key or os.environ.get("CFBD_API_KEY")
        if not self.api_key:
            raise RuntimeError(
                "No API key. Set CFBD_API_KEY or pass --api-key "
                "(get a free key at https://collegefootballdata.com/key)."
            )
        self.base_url = base_url.rstrip("/")
        self.retries = retries
        # Optional on-disk cache so repeated backtests don't re-download (and hit rate limits).
        self.cache_dir = cache_dir
        self.session = requests.Session()
        self.session.headers.update(
            {"Authorization": f"Bearer {self.api_key}", "Accept": "application/json"}
        )

    def get(self, path, **params):
        params = {k: v for k, v in params.items() if v is not None}
        cache_path = None
        if self.cache_dir:
            key = hashlib.sha1(json.dumps([path, sorted(params.items())]).encode()).hexdigest()
            cache_path = os.path.join(self.cache_dir, f"{key}.json")
            if os.path.exists(cache_path):
                with open(cache_path) as f:
                    return json.load(f)
        data = self._get(path, params)
        if cache_path:
            os.makedirs(self.cache_dir, exist_ok=True)
            with open(cache_path, "w") as f:
                json.dump(data, f)
        return data

    def _get(self, path, params):
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

    def game_advanced_stats(self, year, week, season_type, exclude_garbage_time=True):
        """Per team-game advanced stats (offense and defense PPA, success rate, ...)."""
        return self.get(
            "/stats/game/advanced",
            year=year,
            week=week,
            seasonType=season_type,
            excludeGarbageTime=str(exclude_garbage_time).lower(),
        )

    def drives(self, year, week, season_type):
        """Every drive: offense, defense, plays, elapsed clock time, result."""
        return self.get("/drives", year=year, week=week, seasonType=season_type)

    def passing_player_games(self, year, week, season_type):
        """Per QB-game attempts and EPA (ppa)."""
        return self.get("/passing/players/games", year=year, week=week, seasonType=season_type)

    def player_season_ppa(self, year):
        return self.get("/ppa/players/season", year=year)

    def player_usage(self, year):
        return self.get("/player/usage", year=year)

    def lines(self, year, week, season_type):
        return self.get("/lines", year=year, week=week, seasonType=season_type)

    def player_game_ppa(self, year, week, season_type, exclude_garbage_time=True):
        """Per player-game average PPA (all / pass / rush)."""
        return self.get(
            "/ppa/players/games",
            year=year,
            week=week,
            seasonType=season_type,
            excludeGarbageTime=str(exclude_garbage_time).lower(),
        )
