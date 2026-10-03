"""Game-time weather from Open-Meteo (free, no key) for each game's stadium.

The CFBD weather endpoint needs a paid tier, so this uses stadium coordinates from CFBD
/venues and hourly weather from Open-Meteo: the historical archive for played games and
the forecast (up to 16 days out) for upcoming ones. Each game gets the average over the
hours it's played (kickoff to about 3.5 hours later). Dome stadiums are treated as calm,
dry and 70F.

Fields per game: wind_mph, gust_mph, precip_in (total over the game), temp_f, dome.

Effect on totals (python -m cfb_stats.weather --backtest): over 2023-2025 the model's
projected totals ran about 0.75 points high per mph of wind above 10 mph (3-4 points in
15-20 mph wind), while the market's totals showed no wind bias, so books already price it.
The model applies that wind correction (WIND_PER_MPH); rain and cold effects were too
noisy to estimate, so they are shown but not applied. Out of sample the correction made
projections slightly more accurate but did not change betting results.

Usage:
  python -m cfb_stats.weather --backtest        # leave-one-season-out test on 2023-2025
"""

import datetime
import hashlib
import json
import os
import time
from collections import defaultdict

import requests

ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
HOURLY = "wind_speed_10m,wind_gusts_10m,precipitation,temperature_2m"
GAME_HOURS = 4  # kickoff hour plus the next three
BATCH = 10  # locations per request (big multi-location responses can get cut off)
WIND_THRESHOLD = 10.0  # mph
WIND_PER_MPH = -0.75  # points on the game total per mph above the threshold
DOME = {"wind_mph": 0.0, "gust_mph": 0.0, "precip_in": 0.0, "temp_f": 70.0, "dome": True}


def _parse_time(s):
    if not s:
        return None
    try:
        return datetime.datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(datetime.timezone.utc)
    except ValueError:
        return None


def kickoff(game):
    """Kickoff in UTC. For TBD start times assume a mid-afternoon Eastern kickoff."""
    t = _parse_time(game.get("startDate"))
    if t is None:
        return None
    if game.get("startTimeTBD"):
        t = t.replace(hour=19, minute=0)  # 3pm ET
    return t


class OpenMeteo:
    def __init__(self, cache_dir=None, session=None):
        self.cache_dir = cache_dir
        self.session = session or requests.Session()

    def _get(self, url, params, retries=6):
        key = None
        if self.cache_dir:
            key = os.path.join(self.cache_dir, "om_" + hashlib.sha1(
                json.dumps([url, sorted(params.items())]).encode()).hexdigest() + ".json")
            if os.path.exists(key):
                with open(key) as f:
                    return json.load(f)
        data = None
        for attempt in range(retries):
            try:
                r = self.session.get(url, params=params, timeout=60)
            except (requests.ConnectionError, requests.Timeout):
                time.sleep(5 * 2 ** attempt)
                continue
            if r.status_code == 429 or r.status_code >= 500:
                # Free tier limits calls per minute, weighted by data volume.
                time.sleep(5 * 2 ** attempt)
                continue
            r.raise_for_status()
            data = r.json()
            break
        if data is None:
            raise RuntimeError(f"Open-Meteo request failed after retries: {url}")
        data = data if isinstance(data, list) else [data]
        if key:
            os.makedirs(self.cache_dir, exist_ok=True)
            with open(key, "w") as f:
                json.dump(data, f)
        return data

    def _fetch(self, chunk, start, end, forecast, retries=6):
        """Raw per-location responses; a batch that keeps failing is split in half."""
        params = {
            "latitude": ",".join(f"{lat:.4f}" for lat, _ in chunk),
            "longitude": ",".join(f"{lon:.4f}" for _, lon in chunk),
            "hourly": HOURLY, "wind_speed_unit": "mph", "temperature_unit": "fahrenheit",
            "precipitation_unit": "inch", "timezone": "GMT",
            "start_date": start.isoformat(), "end_date": end.isoformat(),
        }
        try:
            return self._get(FORECAST_URL if forecast else ARCHIVE_URL, params,
                             retries if len(chunk) == 1 else 2)
        except RuntimeError:
            if len(chunk) == 1:
                raise
            mid = len(chunk) // 2
            return (self._fetch(chunk[:mid], start, end, forecast, retries)
                    + self._fetch(chunk[mid:], start, end, forecast, retries))

    def hourly(self, locations, start, end, forecast=False):
        """locations: [(lat, lon)] -> [{utc hour datetime: {field: value}}], one dict per location."""
        out = []
        for i in range(0, len(locations), BATCH):
            for loc in self._fetch(locations[i:i + BATCH], start, end, forecast):
                h = loc.get("hourly") or {}
                series = {}
                for j, ts in enumerate(h.get("time") or []):
                    t = datetime.datetime.fromisoformat(ts).replace(tzinfo=datetime.timezone.utc)
                    series[t] = {k: (h.get(k) or [None] * (j + 1))[j] for k in HOURLY.split(",")}
                out.append(series)
        return out


def game_weather(games, venues, client=None, now=None):
    """{game id: weather dict} for games with a known venue and kickoff.

    Played games use the archive; games within the next 16 days use the forecast.
    """
    client = client or OpenMeteo()
    now = now or datetime.datetime.now(datetime.timezone.utc)
    by_venue = {v["id"]: v for v in venues}
    out, todo = {}, defaultdict(list)  # (venue id) -> [(game, kickoff)]
    for g in games:
        v, k = by_venue.get(g.get("venueId")), kickoff(g)
        if v is None or k is None:
            continue
        if v.get("dome"):
            out[g["id"]] = dict(DOME)
            continue
        if v.get("latitude") is None or v.get("longitude") is None:
            continue
        todo[v["id"]].append((g, k))
    if not todo:
        return out

    archive_cutoff = (now - datetime.timedelta(days=5)).date()  # archive lags a few days
    for forecast in (False, True):
        groups = {vid: [(g, k) for g, k in gs if (k.date() > archive_cutoff) == forecast]
                  for vid, gs in todo.items()}
        groups = {vid: gs for vid, gs in groups.items() if gs}
        if not groups:
            continue
        kicks = [k for gs in groups.values() for _, k in gs]
        start = min(kicks).date()
        end = (max(kicks) + datetime.timedelta(hours=GAME_HOURS)).date()
        if forecast:
            start = max(start, now.date() - datetime.timedelta(days=1))
            end = min(end, now.date() + datetime.timedelta(days=15))
            if start > end:
                continue
        vids = sorted(groups)
        series = client.hourly([(by_venue[v]["latitude"], by_venue[v]["longitude"]) for v in vids],
                               start, end, forecast)
        for vid, s in zip(vids, series):
            for g, k in groups[vid]:
                hours = [s.get(k.replace(minute=0, second=0, microsecond=0) + datetime.timedelta(hours=i))
                         for i in range(GAME_HOURS)]
                hours = [h for h in hours if h and h.get("wind_speed_10m") is not None]
                if not hours:
                    continue
                avg = lambda f: sum(h[f] or 0.0 for h in hours) / len(hours)  # noqa: E731
                out[g["id"]] = {
                    "wind_mph": round(avg("wind_speed_10m"), 1),
                    "gust_mph": round(avg("wind_gusts_10m"), 1),
                    "precip_in": round(sum(h["precipitation"] or 0.0 for h in hours), 2),
                    "temp_f": round(avg("temperature_2m"), 1),
                    "dome": False,
                }
    return out


def total_adjustment(wx):
    """Points to add to a projected game total for game-time weather (wind only)."""
    if not wx or wx.get("dome"):
        return 0.0
    return WIND_PER_MPH * max(wx["wind_mph"] - WIND_THRESHOLD, 0.0)


def backtest(years=(2023, 2024, 2025), data_dir="data", api_key=None, cache_dir=None):
    """Leave-one-season-out test of the wind correction on saved totals backtests."""
    import csv
    import numpy as np
    from .api import CFBDClient
    from .totals import weekly_card

    client = CFBDClient(api_key=api_key, cache_dir=cache_dir)
    om = OpenMeteo(cache_dir=cache_dir)
    venues = client.get("/venues")
    rows = []
    for y in years:
        with open(os.path.join(data_dir, str(y), "totals_backtest_games.csv")) as f:
            season = list(csv.DictReader(f))
        ids = {int(r["game_id"]) for r in season}
        wx = game_weather([g for g in client.games(y, "regular") if g["id"] in ids], venues, om)
        for r in season:
            x = wx.get(int(r["game_id"]))
            if x:
                rows.append({**r, "season": y, **x})
    for r in rows:
        for k in ("actual_total", "proj_total", "market_total", "edge"):
            r[k] = float(r[k])
        r["p4_game"] = r["p4_game"] == "True"
        r["enough_data"] = True
        r["week"] = (r["season"], int(r["week"]))  # card groups by week
    act = np.array([r["actual_total"] for r in rows])
    proj = np.array([r["proj_total"] for r in rows])
    excess = np.array([0.0 if r["dome"] else max(r["wind_mph"] - WIND_THRESHOLD, 0.0) for r in rows])
    season = np.array([r["season"] for r in rows])

    def record(rs):
        w = l = 0
        for r, side in rs:
            d = r["actual_total"] - r["market_total"]
            if d:
                w += (d > 0) == (side == "OVER")
                l += (d > 0) != (side == "OVER")
        return w, l

    def every_edge(rs, t=3):
        return record([(r, "OVER" if r["edge"] > 0 else "UNDER") for r in rs if abs(r["edge"]) >= t])

    out = []
    print("season  wind coef   RMSE base -> wind   edge>=3 base -> wind   P4 card base -> wind")
    for y in years:
        tr, te = season != y, season == y
        A = np.column_stack([np.ones(tr.sum()), excess[tr]])
        coef = float(np.linalg.lstsq(A, (act - proj)[tr], rcond=None)[0][1])
        test = [r for r in rows if r["season"] == y]
        adj = [{**r, "proj_total": r["proj_total"] + coef * e, "edge": r["edge"] + coef * e}
               for r, e in zip(test, excess[te])]
        rm = lambda rs: float(np.sqrt(np.mean([(r["actual_total"] - r["proj_total"]) ** 2 for r in rs])))  # noqa: E731
        res = {"season": y, "wind_coef": round(coef, 2), "rmse_base": round(rm(test), 2), "rmse_wind": round(rm(adj), 2)}
        for name, rs in (("base", test), ("wind", adj)):
            e3, card = every_edge(rs), record(weekly_card(rs, 3, p4_only=True))
            res.update({f"edge3_{name}_w": e3[0], f"edge3_{name}_l": e3[1], f"card_{name}_w": card[0], f"card_{name}_l": card[1]})
        print(f"{y}  {coef:+9.2f}   {res['rmse_base']:.2f} -> {res['rmse_wind']:.2f}       "
              f"{res['edge3_base_w']}-{res['edge3_base_l']} -> {res['edge3_wind_w']}-{res['edge3_wind_l']}"
              f"        {res['card_base_w']}-{res['card_base_l']} -> {res['card_wind_w']}-{res['card_wind_l']}")
        out.append(res)
    mkt = np.array([r["market_total"] for r in rows])
    wind = np.array([0.0 if r["dome"] else r["wind_mph"] for r in rows])
    for lo, hi in ((0, 10), (10, 15), (15, 99)):
        m = (wind >= lo) & (wind < hi)
        print(f"wind {lo}-{hi} mph: {int(m.sum())} games, actual - model {np.mean((act - proj)[m]):+.1f}, "
              f"actual - market {np.mean((act - mkt)[m]):+.1f}")
    return out


def main(argv=None):
    import argparse
    from .collect import write_csv
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--backtest", action="store_true")
    p.add_argument("--years", default="2023,2024,2025")
    p.add_argument("--api-key", default=None)
    p.add_argument("--cache", default=None)
    args = p.parse_args(argv)
    if not args.backtest:
        p.print_help()
        return
    out = backtest(tuple(int(y) for y in args.years.split(",")), api_key=args.api_key, cache_dir=args.cache)
    write_csv(os.path.join("data", "weather_backtest.csv"), out)


if __name__ == "__main__":
    main()
