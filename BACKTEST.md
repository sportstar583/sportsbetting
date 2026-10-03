# Totals model backtest

Results for `cfb_stats.totals` (game-total projections from opponent-adjusted EPA and
tempo). Reproduce with:

```bash
python -m cfb_stats.totals --year 2025 --backtest --cache .cfbd_cache
python -m cfb_stats.totals --year 2024 --backtest --cache .cfbd_cache
```

**Method.** Walk-forward: every week from week 4 on is projected using only games played
before it. Only games with a market total where both teams have 3+ games of data are
included. "Edge" is projected total minus the market total. Closing and opening totals are
the median across books from the CFBD `/lines` endpoint. Breakeven at -110 is 52.4%.

**Bottom line.** The model is less accurate than the closing market in both seasons. Its
edges did not reliably beat closing totals: break-even to losing in 2025, winning in 2024,
and about 53.6% across both (roughly 1,150 bets at edge >= 3), which is within normal luck
of breakeven. It does predict which way lines move between open and close. Use the board as
a screen for early-week numbers, not as a list of bets. Against the spread it has no edge at
all (49-51% at every threshold in both seasons).

## Accuracy (RMSE of the game total, points)

| Variant | 2025 | 2024 |
| --- | --- | --- |
| Baseline (EPA + plays/game) | 15.89 | 17.17 |
| + tempo (time per play) | 15.59 | 17.04 |
| + FCS games at half weight | 15.90 | 17.21 |
| + rout damping (Huber) | 15.86 | 17.16 |
| + FCS weight + Huber | 15.81 | 17.19 |
| **All (current default)** | **15.49** | **17.00** |
| Closing market | 14.9 | 16.8 |

Tempo is the only change that improved accuracy on its own in both seasons. Down-weighting
FCS games and routs doesn't help alone, but the full combination is the most accurate.
Source: `data/<year>/totals_backtest_variants.csv`.

## Betting the edge against the closing total (current default model)

| Edge >= | 2025 bets | 2025 win % | 2025 ROI | 2024 bets | 2024 win % | 2024 ROI |
| --- | --- | --- | --- | --- | --- | --- |
| 0 | 1,104 | 50.9% | -2.8% | 1,112 | 53.0% | +1.3% |
| 3 | 534 | 50.0% | -4.5% | 621 | 56.8% | +8.4% |
| 5 | 243 | 49.2% | -6.1% | 338 | 56.5% | +7.8% |
| 7 | 106 | 49.5% | -5.5% | 158 | 56.8% | +8.4% |
| 10 | 31 | 45.2% | -13.8% | 42 | 51.2% | -2.2% |

- The seasons disagree. In 2024 the plain baseline also hit about 55% at edge >= 3, so
  2024 probably suited this style of model rather than showing an edge.
- Bigger edges don't win more often; the largest (10+) are the worst in both seasons. A real
  edge would strengthen as the edge grows.

Source: `data/<year>/totals_backtest_summary.csv`, per game in `totals_backtest_games.csv`.

## Betting the edge against the opening total

| Edge >= | 2025 bets | 2025 win % | 2025 ROI | 2024 bets | 2024 win % | 2024 ROI |
| --- | --- | --- | --- | --- | --- | --- |
| 0 | 609 | 52.4% | 0.0% | 607 | 53.4% | +1.9% |
| 3 | 303 | 52.8% | +0.8% | 355 | 53.5% | +2.2% |
| 5 | 155 | 52.9% | +1.0% | 202 | 48.5% | -7.4% |
| 7 | 73 | 58.9% | +12.5% | 95 | 51.6% | -1.5% |

- Lines move toward the model between open and close: correlation 0.32 (2025) and 0.14
  (2024); at edge >= 3 the line moved 0.87 and 0.33 points toward the model on average.
- The model's edge vs the opening line barely predicts results (correlation 0.06 and 0.02).
- The 58.9% at edge >= 7 in 2025 is 73 bets and isn't repeated in 2024.

Source: `data/<year>/totals_backtest_open.csv`.

## Injury adjustment (starting QB absent)

Games where a team's season-to-date starting QB didn't play, using who actually played as a
stand-in for an injury report. The raw estimate overshot by about 3x, so offensive impacts
are scaled by 0.35, which was fit on 2025.

| | 2025 (181 games, in sample) | 2024 (351 games, out of sample) |
| --- | --- | --- |
| RMSE without / with adjustment | 15.81 / 15.54 | 17.77 / 17.85 |
| Market RMSE | 15.06 | 17.39 |
| Pick win % without / with | 51.1% / 54.9% | 54.2% / 54.9% |
| Average adjustment | -2.1 pts | -1.8 pts |

Out of sample the adjustment slightly worsened accuracy and slightly improved pick rate,
which is no clear effect either way. The market already moves on QB news.

Source: `data/<year>/totals_backtest_qb_summary.csv`, per game in `totals_backtest_qb_out.csv`.

## Against the spread

Same walk-forward setup, using each team's projected points to get a projected margin
(home minus away), compared with the median home spread. A pick is the side the model says
covers; edge is how many points it disagrees with the line.

**No edge against the spread at any setting.** The model's edge has essentially zero
correlation with whether the favorite covers (-0.011 in 2025, 0.000 in 2024).

Shrinkage matters a lot more for spreads than totals. The totals model (ridge penalty
alpha=1000) projects margins that vary about half as much as market spreads, so it "picks"
nearly every underdog. Less shrinkage is more accurate, but never close to the market:

| Margin RMSE (points) | 2025 | 2024 |
| --- | --- | --- |
| Totals settings (alpha 1000) | 20.6 | 19.9 |
| alpha 150 | 18.4 | 18.1 |
| alpha 75 | 18.0 | 17.7 |
| **alpha 25 (spread model)** | **17.6** | **17.4** |
| Closing market | 15.1 | 15.3 |

Spread model (alpha 25) against the line:

| Edge >= | 2025 close | 2024 close | 2025 open | 2024 open |
| --- | --- | --- | --- | --- |
| 0 | 50.5% (1,098) | 49.5% (1,086) | 53.4% (612) | 49.9% (591) |
| 3 | 50.3% (785) | 49.6% (762) | 53.3% (443) | 51.3% (411) |
| 5 | 49.2% (608) | 50.2% (576) | 54.1% (338) | 52.5% (295) |
| 7 | 49.5% (438) | 49.1% (377) | 52.4% (254) | 51.8% (197) |
| 10 | 49.6% (250) | 48.5% (227) | 52.1% (146) | 53.9% (115) |

(Win % with bets in parentheses, pushes excluded.) Against the opening spread, 2025 hit
53-54% and lines moved toward the model (correlation 0.30), but 2024 was 50-54% with
almost no line-movement signal (0.09). That is the same inconsistent pattern as totals.

Even alpha 25 is overconfident early in the season. In week 5 of 2026 it projected margins
like UMass by 38 over Eastern Michigan. So the board keeps projected margins in the CSV for
reference but does not list spread picks.

Source: `data/<year>/spreads_backtest_summary.csv`, `spreads_backtest_variants.csv`,
per game in `spreads_backtest_games.csv`.

## Not backtested

- Defensive-player injury estimates (share of team defensive production) and the Big Ten
  availability report import. There is no historical availability data to test against, so
  the defensive scale is uncalibrated and kept small.
- Weather, rest and travel, and prior-season ratings carried into early weeks.
