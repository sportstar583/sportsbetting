"""Thin client for the CollegeFootballData.com API (https://api.collegefootballdata.com).

A free API key is required: https://collegefootballdata.com/key
"""

import gzip
import hashlib
import json
import os
import time

import requests

BASE_URL = "https://api.collegefootballdata.com"


class CFBDClient:
    # Responses already fetched in this process (the weekly run asks for some twice).
    _memo = {}

    def __init__(self, api_key=None, base_url=BASE_URL, retries=5, cache_dir=None, store_dir="data"):
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
        # Finished weeks' data, kept with the repo (data/<year>/cache/) so weekly runs in fresh
        # checkouts don't re-download every past week. Only used for calls made with store=True.
        self.store_dir = store_dir
        self.session = requests.Session()
        self.session.headers.update(
            {"Authorization": f"Bearer {self.api_key}", "Accept": "application/json"}
        )

    def get(self, path, **params):
        params = {k: v for k, v in params.items() if v is not None}
        memo_key = (self.base_url, path, json.dumps(sorted(params.items())))
        if memo_key not in CFBDClient._memo:
            CFBDClient._memo[memo_key] = self._cached_get(path, params)
        return CFBDClient._memo[memo_key]

    def get_stored(self, name, path, **params):
        """get(), saved under data/<year>/cache/<name>.json.gz and read from there afterwards.
        Only for data that no longer changes (weeks finished well in the past)."""
        if not self.store_dir:
            return self.get(path, **params)
        fn = os.path.join(self.store_dir, str(params["year"]), "cache", f"{name}.json.gz")
        if os.path.exists(fn):
            with gzip.open(fn, "rt") as f:
                return json.load(f)
        data = self.get(path, **params)
        if data:  # don't freeze an empty answer
            os.makedirs(os.path.dirname(fn), exist_ok=True)
            with gzip.open(fn, "wt") as f:
                json.dump(data, f, separators=(",", ":"))
        return data

    def _cached_get(self, path, params):
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

    def game_advanced_stats(self, year, week, season_type, exclude_garbage_time=True, store=False):
        """Per team-game advanced stats (offense and defense PPA, success rate, ...).
        store=True keeps the response in data/<year>/cache/ (for finished weeks)."""
        params = dict(year=year, week=week, seasonType=season_type,
                      excludeGarbageTime=str(exclude_garbage_time).lower())
        if store:
            gt = "" if exclude_garbage_time else "_with_garbage"
            return self.get_stored(f"advanced_{season_type}_week{week}{gt}", "/stats/game/advanced", **params)
        return self.get("/stats/game/advanced", **params)

    def drives(self, year, week, season_type, store=False):
        """Every drive: offense, defense, plays, elapsed clock time, result.
        store=True keeps the response in data/<year>/cache/ (for finished weeks)."""
        params = dict(year=year, week=week, seasonType=season_type)
        if store:
            return self.get_stored(f"drives_{season_type}_week{week}", "/drives", **params)
        return self.get("/drives", **params)

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
