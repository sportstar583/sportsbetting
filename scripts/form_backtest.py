"""Walk-forward test of the recent-form term in the totals model (2023-2025).

  CFBD_CACHE=.cfbd_cache python scripts/form_backtest.py
"""
import os
import sys

from cfb_stats.api import CFBDClient
from cfb_stats.totals import (DEFAULT_FCS_WEIGHT, DEFAULT_HUBER_K, DEFAULT_MATCHUP, TOTALS_ALPHA, backtest,
                              compare_variants, load_season)

BASE = {"alpha": TOTALS_ALPHA, "tempo": True, "fcs_weight": DEFAULT_FCS_WEIGHT, "huber_k": DEFAULT_HUBER_K,
        "matchup": DEFAULT_MATCHUP}
GRID = [(0.0, 3, 2.0)] + [(f, n, pr) for n in (3, 5) for pr in (2.0, 5.0) for f in (0.1, 0.2, 0.3, 0.5)]


def main():
    client = CFBDClient(cache_dir=os.environ.get("CFBD_CACHE"))  # run from the repo root
    years = [int(y) for y in sys.argv[1:]] or [2023, 2024, 2025]
    variants = {f"form {f:g} last {n} p{pr:g}": dict(BASE, form=f, form_games=n, form_prior=pr) for f, n, pr in GRID}
    pooled = {k: [] for k in variants}
    for y in years:
        res = backtest(load_season(client, y), variants=variants)
        print(f"\n== {y} ==")
        compare_variants(res)
        for k, v in res.items():
            pooled[k] += v
    print("\n== all seasons ==")
    compare_variants(pooled)


if __name__ == "__main__":
    main()
