# Backtest

Results for `cfb_stats.totals`: game totals (and spreads) projected from opponent-adjusted
EPA, tempo and run/pass matchups. Reproduce with:

```bash
python -m cfb_stats.totals --year 2025 --backtest --cache .cfbd_cache   # also 2024, 2023
```

**Method.** Walk-forward: every week from week 4 on is projected using only games played
before it. Only games with a market total where both teams have 3+ games of data are
included. "Edge" is projection minus market. Closing and opening lines are the median across
books from the CFBD `/lines` endpoint. Breakeven at -110 is 52.4%. Win % excludes pushes.

**Which seasons are clean tests.** The ridge penalty and injury scale were chosen by looking
at 2024 and 2025, and the run/pass matchup and weekly card were judged on all three seasons.
2023 was the holdout for the base model, but it has since been looked at, so none of the
results below is fully out of sample. The next real test is the 2026 season as it's played.

## Bottom line

- **Totals: a small, unproven lean.** The model is less accurate than the closing market in
  every season. Betting every edge >= 3 went 53.0% against the closing total and 54.0% against
  the opening total over 2023-2025, which is barely above breakeven.
- **The weekly card is the strongest result:** the 3 biggest over edges and 3 biggest under
  edges each week among Power 4 games went **114-88 (56.4%)** against both closing and opening
  totals, above breakeven in every season. That is about 1.7 standard errors above breakeven
  on 202 bets, and it was found after trying several ways to slice the results. Promising,
  not proven.
- **Spreads: no edge.** 49-52% against the closing spread at every threshold in every season.
- Treat the board as a screen, with modest stakes, and track the card on 2026 before trusting it.

## Weekly card

Each week, take the N biggest over edges and N biggest under edges (games where both teams
have 3+ games of data). `python -m cfb_stats.totals` prints this week's Power 4 card.

| Card | 2023 | 2024 | 2025 | All three |
| --- | --- | --- | --- | --- |
| **Top 3 each way, P4, vs closing** | 35-29 (54.7%) | 40-29 (58.0%) | 39-30 (56.5%) | **114-88 (56.4%)** |
| **Top 3 each way, P4, vs opening** | 35-28 (55.6%) | 41-29 (58.6%) | 38-31 (55.1%) | **114-88 (56.4%)** |
| Top 3 each way, all games, vs closing | 34-32 (51.5%) | 43-32 (57.3%) | 37-38 (49.3%) | 114-102 (52.8%) |
| Top 5 each way, P4, vs closing | 55-48 (53.4%) | 58-53 (52.3%) | 59-54 (52.2%) | 172-155 (52.6%) |
| Top 5 each way, all games, vs closing | 62-47 (56.9%) | 73-50 (59.3%) | 62-61 (50.4%) | 197-158 (55.5%) |

- The P4 top-3 card is the only version above breakeven in every season against both lines.
- Widening to top 5 dilutes it, so most of the value is in the very top of the list.
- P4 games have more data and more consistent schedules, which may be why the ratings
  separate them better than Group of 5 and FCS games.

Source: `data/<year>/totals_backtest_card.csv`.

## Totals: accuracy (RMSE of the game total, points)

| Variant | 2023 | 2024 | 2025 |
| --- | --- | --- | --- |
| Baseline (EPA + plays/game) | 16.56 | 17.17 | 15.89 |
| + tempo (time per play) | 16.47 | 17.04 | 15.59 |
| + FCS games at half weight | 16.50 | 17.21 | 15.90 |
| + rout damping (Huber) | 16.55 | 17.16 | 15.86 |
| + FCS weight + Huber | 16.48 | 17.19 | 15.81 |
| All of the above | 16.36 | 17.00 | 15.49 |
| + run/pass matchup, half blend | 16.34 | 17.00 | 15.46 |
| **+ run/pass matchup (current default)** | **16.38** | **17.03** | **15.46** |
| Closing market | 15.9 | 16.8 | 14.9 |

Tempo improved accuracy in all three seasons and is the most reliable single addition.

Source: `data/<year>/totals_backtest_variants.csv`.

## Run/pass matchup

Each side's expected EPA is built from rush EPA (offense's rush rating vs the defense's rush
rating) and pass EPA (same for passing), weighted by the expected run rate. It only differs
from the overall rating when play mix is lopsided: a run-heavy offense aims most of its plays
at a weak run defense, and offenses run more against defenses that can't stop it. Rush and
pass ratings are shrunk half as hard as the overall rating, since each sees about half a
team's plays. (An earlier version shrank them as hard as the overall rating, which flattened
them and made the matchup act as extra shrinkage; that version's results are superseded.)

- **Accuracy: no change** (table above). On average it doesn't improve projections.
- **Ranking the biggest disagreements: better.** With it on, the weekly card did better in all
  eight versions tested, for example P4 top 3 vs closing 56.4% with vs 54.0% without, and all
  games top 5 vs closing 55.5% vs 51.1%. That's why it's on by default (`--matchup 0` turns it off).
- It doesn't help spreads, so the spread model doesn't use it.

## Totals: betting every edge against the closing total

| Edge >= | 2023 | 2024 | 2025 | All three |
| --- | --- | --- | --- | --- |
| 0 | 51.0% (953) | 53.3% (1,096) | 51.4% (1,108) | 51.9% (3,157) |
| 3 | 54.1% (584) | 54.0% (617) | 50.7% (515) | 53.0% (1,716) |
| 5 | 55.3% (365) | 57.3% (351) | 50.6% (243) | 54.8% (959) |
| 7 | 54.0% (187) | 59.0% (183) | 50.0% (110) | 55.0% (480) |

(Bets in parentheses.) Source: `data/<year>/totals_backtest_summary.csv`, per game in
`totals_backtest_games.csv`.

## Totals: betting every edge against the opening total

| Edge >= | 2023 | 2024 | 2025 | All three |
| --- | --- | --- | --- | --- |
| 0 | 53.2% (588) | 52.2% (607) | 53.1% (610) | 52.9% (1,805) |
| 3 | 56.2% (336) | 52.5% (354) | 53.4% (313) | 54.0% (1,003) |
| 5 | 56.2% (201) | 51.7% (201) | 55.0% (151) | 54.2% (553) |
| 7 | 60.4% (106) | 54.0% (100) | 53.8% (80) | 56.3% (286) |

Lines move toward the model between open and close (correlation of edge with line movement
0.19, 0.15 and 0.32 in 2023-2025). Only about half of games have an opening total in the data.

Source: `data/<year>/totals_backtest_open.csv`.

## Spreads

Projected margin (home minus away) against the median home spread. The totals model shrinks
ratings heavily (alpha 1000), which compresses margins to about half the spread of market
lines, so spreads use a separate, lightly shrunk model (alpha 25).

| | 2023 | 2024 | 2025 |
| --- | --- | --- | --- |
| Margin RMSE, totals settings | 19.6 | 19.9 | 20.6 |
| Margin RMSE, spread model (alpha 25) | 16.9 | 17.4 | 17.6 |
| Closing market | 15.3 | 15.3 | 15.1 |
| Win % vs close, edge >= 3 | 51.6% (634) | 49.6% (762) | 50.3% (785) |
| Win % vs close, edge >= 7 | 49.0% (292) | 49.1% (377) | 49.5% (438) |
| Win % vs open, edge >= 3 | 53.3% (415) | 51.3% (411) | 53.3% (443) |

No edge against the closing spread in any season. Early in the season the spread model is
also wildly overconfident (in week 5 of 2026 it had UMass by 38 over Eastern Michigan), so
the board keeps projected margins in the CSV for reference but lists no spread picks.

Source: `data/<year>/spreads_backtest_summary.csv`, `spreads_backtest_variants.csv`,
per game in `spreads_backtest_games.csv`.

## Injury adjustment (starting QB absent)

Games where a team's season-to-date starting QB didn't play, using who actually played as a
stand-in for an injury report. The raw estimate overshot by about 3x, so offensive impacts
are scaled by 0.35 (fit on 2025).

| | 2023 | 2024 | 2025 |
| --- | --- | --- | --- |
| Games | 321 | 351 | 181 |
| RMSE without / with adjustment | 16.36 / 16.61 | 17.82 / 17.87 | 15.75 / 15.51 |
| Market RMSE | 15.82 | 17.39 | 15.06 |
| Pick win % without / with | 50.6% / 50.9% | 53.6% / 54.4% | 53.1% / 53.4% |
| Average adjustment | -2.5 pts | -1.8 pts | -2.1 pts |

No clear effect: accuracy slightly worse in two seasons and better in one, pick rate about the
same. The market already moves on QB news.

Source: `data/<year>/totals_backtest_qb_summary.csv`, per game in `totals_backtest_qb_out.csv`.

## Not backtested

- Defensive-player injury estimates and the Big Ten availability report import. There is no
  historical availability data to test against, so the defensive scale is uncalibrated and
  kept small.
- Weather, rest and travel, and prior-season ratings carried into early weeks.
