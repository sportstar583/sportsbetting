#!/usr/bin/env python3
"""
Arbitrage scanner using The Odds API (https://the-odds-api.com).

Finds markets where the best prices across different sportsbooks have a
combined implied probability below 100%, then sizes stakes so every outcome
returns roughly the same amount.

Setup:
    pip install requests
    export ODDS_API_KEY=your_key_here

Usage:
    python scripts/arb_scanner.py                          # one scan, default sports
    python scripts/arb_scanner.py --loop 60                # rescan every 60 seconds
    python scripts/arb_scanner.py --live-only --min-profit 0.5
    python scripts/arb_scanner.py --sports basketball_nba americanfootball_nfl --stake 500

Quota: each odds call costs (number of markets x number of regions) credits,
once per sport. Watch the "remaining" number printed after each scan.
"""

import argparse
import os
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone

import requests

BASE_URL = "https://api.the-odds-api.com/v4"
DEFAULT_SPORTS = ["basketball_nba", "americanfootball_nfl", "icehockey_nhl"]


# ---------------------------------------------------------------- data types

@dataclass
class Price:
    book: str
    odds: float          # decimal odds
    last_update: datetime


@dataclass
class Arb:
    event: str
    sport: str
    commence: datetime
    market: str          # e.g. "h2h", "spreads home -3.5", "totals 221.5"
    legs: dict           # outcome label -> Price
    profit_pct: float


# ---------------------------------------------------------------- fetching

def fetch_odds(api_key, sport, regions, markets):
    """Return (events, remaining_credits) for one sport."""
    resp = requests.get(
        f"{BASE_URL}/sports/{sport}/odds",
        params={
            "apiKey": api_key,
            "regions": ",".join(regions),
            "markets": ",".join(markets),
            "oddsFormat": "decimal",
            "dateFormat": "iso",
        },
        timeout=15,
    )
    if resp.status_code == 401:
        sys.exit("Invalid API key (401).")
    if resp.status_code == 422:
        print(f"  [{sport}] not available right now (422), skipping")
        return [], resp.headers.get("x-requests-remaining")
    resp.raise_for_status()
    return resp.json(), resp.headers.get("x-requests-remaining")


def parse_time(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


# ---------------------------------------------------------------- normalizing

def group_markets(event):
    """
    Turn one event's bookmaker data into comparable groups.

    Returns {group_key: {outcome_label: [Price, ...]}} where every outcome in a
    group is one side of the SAME bet. The tricky part is spreads and totals:
    a book offering Over 221.5 can only be paired with Under 221.5, and
    Lakers -3.5 only with Celtics +3.5.

    Each group also gets a "_size" entry: the most outcomes any single book
    listed for it (2 for moneylines, 3 when a book offers the draw).
    """
    groups, sizes = {}, {}
    home = event["home_team"]

    for book in event.get("bookmakers", []):
        for market in book.get("markets", []):
            mkey = market["key"]
            updated = parse_time(market.get("last_update") or book["last_update"])
            per_book = {}

            for oc in market["outcomes"]:
                name, price, point = oc["name"], oc["price"], oc.get("point")
                if mkey != "h2h" and point is None:
                    continue

                if mkey == "h2h":
                    gkey, label = "h2h", name
                elif mkey == "totals":
                    gkey, label = f"totals {point:g}", name           # Over / Under
                elif mkey == "spreads":
                    # Key the group by the HOME team's line so both sides line up.
                    # "+ 0.0" turns -0.0 into 0.0 so pick'em lines group together.
                    home_line = (point if name == home else -point) + 0.0
                    gkey, label = f"spreads home {home_line:+g}", f"{name} {point:+g}"
                else:
                    continue

                groups.setdefault(gkey, {}).setdefault(label, []).append(
                    Price(book["title"], price, updated)
                )
                per_book[gkey] = per_book.get(gkey, 0) + 1

            for gkey, n in per_book.items():
                sizes[gkey] = max(sizes.get(gkey, 0), n)

    for gkey, outcomes in groups.items():
        outcomes["_size"] = sizes[gkey]
    return groups


# ---------------------------------------------------------------- the math

def find_arb(outcomes, max_age_s, now):
    """
    Given {label: [Price, ...]}, pick the best fresh price per outcome and
    check whether sum(1/odds) < 1. Returns (legs, profit_pct) or None.
    """
    outcomes = dict(outcomes)
    size = outcomes.pop("_size", len(outcomes))
    # Every outcome must be covered, e.g. a soccer moneyline needs the draw too.
    if len(outcomes) < max(2, size):
        return None

    legs = {}
    for label, prices in outcomes.items():
        fresh = [p for p in prices if (now - p.last_update).total_seconds() <= max_age_s]
        if not fresh:
            return None          # can't cover this outcome with a fresh price
        legs[label] = max(fresh, key=lambda p: p.odds)

    # All legs at one book is never a real arb (and usually means bad data).
    if len({p.book for p in legs.values()}) < 2:
        return None

    implied = sum(1 / p.odds for p in legs.values())
    if implied >= 1:
        return None
    return legs, (1 / implied - 1) * 100


def size_stakes(legs, total_stake, round_to=1.0):
    """
    Split total_stake so each outcome returns the same amount, then round
    stakes (exact cents look like a bot to sportsbooks). Returns
    (stakes, worst_case_profit) computed AFTER rounding.
    """
    implied = sum(1 / p.odds for p in legs.values())
    stakes = {}
    for label, p in legs.items():
        raw = total_stake * (1 / p.odds) / implied
        stakes[label] = round(raw / round_to) * round_to
    spent = sum(stakes.values())
    worst_return = min(stakes[l] * legs[l].odds for l in legs)
    return stakes, worst_return - spent


# ---------------------------------------------------------------- scanning

def scan(args, api_key):
    now = datetime.now(timezone.utc)
    arbs, remaining = [], None

    for sport in args.sports:
        try:
            events, rem = fetch_odds(api_key, sport, args.regions, args.markets)
        except requests.RequestException as e:
            print(f"  [{sport}] request failed: {e}")
            continue
        remaining = rem or remaining
        for ev in events:
            commence = parse_time(ev["commence_time"])
            is_live = commence <= now
            if args.live_only and not is_live:
                continue

            for gkey, outcomes in group_markets(ev).items():
                result = find_arb(outcomes, args.max_age, now)
                if not result:
                    continue
                legs, profit = result
                if args.min_profit <= profit <= args.max_profit:
                    arbs.append(Arb(f"{ev['away_team']} @ {ev['home_team']}",
                                    sport, commence, gkey, legs, profit))

    arbs.sort(key=lambda a: a.profit_pct, reverse=True)
    return arbs, remaining, now


def print_arbs(arbs, total_stake, now):
    if not arbs:
        print("  No arbs found.")
        return
    for a in arbs:
        status = "LIVE" if a.commence <= now else a.commence.strftime("%a %H:%M UTC")
        print(f"\n  {a.profit_pct:.2f}%  |  {a.event}  |  {a.market}  |  {status}")
        stakes, profit = size_stakes(a.legs, total_stake)
        for label, p in a.legs.items():
            age = max(0, int((now - p.last_update).total_seconds()))
            print(f"     {label:<28} {p.odds:>6.2f} @ {p.book:<18} "
                  f"stake ${stakes[label]:>7.2f}   (updated {age}s ago)")
        print(f"     Guaranteed profit after rounding: ${profit:.2f}")


def main():
    ap = argparse.ArgumentParser(description="Sports betting arbitrage scanner")
    ap.add_argument("--sports", nargs="+", default=DEFAULT_SPORTS)
    ap.add_argument("--regions", nargs="+", default=["us"],
                    help="us, us2, uk, eu, au (each adds to quota cost)")
    ap.add_argument("--markets", nargs="+", default=["h2h", "spreads", "totals"])
    ap.add_argument("--stake", type=float, default=100, help="total stake per arb")
    ap.add_argument("--min-profit", type=float, default=0.0, help="percent")
    ap.add_argument("--max-profit", type=float, default=10.0,
                    help="percent; bigger 'arbs' are usually stale or wrong prices")
    ap.add_argument("--max-age", type=int, default=120,
                    help="ignore prices older than this many seconds")
    ap.add_argument("--live-only", action="store_true")
    ap.add_argument("--loop", type=int, default=0,
                    help="rescan every N seconds (0 = scan once)")
    args = ap.parse_args()

    api_key = os.environ.get("ODDS_API_KEY")
    if not api_key:
        sys.exit("Set the ODDS_API_KEY environment variable first.")

    while True:
        print(f"\n=== Scan at {datetime.now().strftime('%H:%M:%S')} ===")
        arbs, remaining, now = scan(args, api_key)
        print_arbs(arbs, args.stake, now)
        print(f"\n  API credits remaining: {remaining}")
        if not args.loop:
            break
        time.sleep(args.loop)


if __name__ == "__main__":
    main()
