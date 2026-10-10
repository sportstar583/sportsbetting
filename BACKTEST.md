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

**Which seasons are clean tests.** The ridge penalty and QB injury scale were chosen by looking
at 2024 and 2025; the run/pass matchup, weekly card, weather and defensive injury scale were
judged on all three seasons.
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

## Weather

Game-time weather (Open-Meteo archive) for every backtest game, 2023-2025. Reproduce with
`python -m cfb_stats.weather --backtest`.

| Wind at kickoff | Games | Actual minus model | Actual minus market |
| --- | --- | --- | --- |
| Under 10 mph | 2,616 | +0.6 | +0.9 |
| 10-15 mph | 514 | -1.7 | +0.2 |
| 15+ mph | 84 | -3.6 | +0.8 |

The model's totals ran high in wind (about 0.75 points per mph over 10 mph); the market's
didn't, so books already price it. Rain and cold estimates were too noisy to use.

Wind correction, fit on two seasons and tested on the third:

| Season | Wind coef | RMSE before / after | Edge >= 3 before / after | P4 card before / after |
| --- | --- | --- | --- | --- |
| 2023 | -0.61 | 16.38 / 16.32 | 316-268 / 312-256 | 35-29 / 33-30 |
| 2024 | -1.07 | 17.03 / 17.09 | 333-284 / 315-284 | 40-29 / 40-29 |
| 2025 | -0.65 | 15.46 / 15.40 | 261-254 / 262-243 | 39-30 / 40-29 |

Slightly more accurate in two of three seasons, no change in betting results. It's applied
(-0.75 per mph over 10) because it removes a known bias, not because it finds edges.

Source: `data/weather_backtest.csv`.

## Injury adjustment (defender absent)

There are no historical availability reports, so this uses box scores after the fact: a
regular (a stat line in 60%+ of his team's games) with no defensive stat line in a game counts
as out for it. Weeks 4+, 2023-2025, games where the season-to-date starting QB sat dropped.
Reproduce with `python -m cfb_stats.defense --backtest --cache .cfbd_cache`.

Missing defenders do push totals up, and the market only partly prices it:

| Starters (5%+ of team production) missing | Games | Actual minus healthy model | Actual minus market |
| --- | --- | --- | --- |
| None | 1,279 | -0.1 | +0.4 |
| Adjustment would be under 10 pts at scale 1.0 | 349 | +0.7 | +0.9 |
| 10-25 pts | 431 | +1.7 | +2.0 |
| 25+ pts | 96 | +1.3 | +2.3 |

Scale fit on two seasons and tested on the third (RMSE and records with the fitted scale):

| Season | Fit (5%+ starters) | Fit (3%+ regulars) | RMSE healthy / fit | Edge >= 3 healthy / fit | P4 card healthy / fit |
| --- | --- | --- | --- | --- | --- |
| 2023 | 0.096 | 0.040 | 16.57 / 16.57 | 212-177 / 221-176 | 28-34 / 32-30 |
| 2024 | 0.071 | 0.050 | 16.50 / 16.46 | 233-187 / 230-182 | 42-26 / 39-30 |
| 2025 | 0.072 | 0.042 | 15.51 / 15.50 | 184-161 / 183-157 | 39-31 / 39-31 |
| All | 0.079 (90% bootstrap 0.02-0.14) | 0.043 (0.00-0.08) | | | |

`DEF_SCALE` is set to 0.08, the starters-only fit; counting 3% rotational players adds quiet
games as false absences and pulls the slope toward zero. The old guess of 0.10 was inside the
interval. Either way the adjustment is small (a 10%-share starter is worth under a point), so
accuracy and betting results barely move: the edge >= 3 win rate ticked up about half a
point in each season, the card is a wash.

Source: `data/defense_backtest.csv`, per game in `defense_backtest_games.csv`.

## Preseason priors

Instead of shrinking every team toward league average, start each from last season's final
rating scaled by returning production, plus roster talent (`cfb_stats.priors`). Weights were fit
on two seasons and tested on the third. Fitted weights: a team keeps about 40% of last season's
rating, more when its offensive production returns; talent adds little once last season is known.

| Totals model RMSE | 2023 | 2024 | 2025 |
| --- | --- | --- | --- |
| Without priors | 16.38 | 17.03 | 15.46 |
| With priors | 16.49 | 17.10 | 15.71 |
| Weeks 4-6 without / with | 17.44 / 17.70 | 17.77 / 17.87 | 15.73 / 15.69 |

| Spread model margin RMSE | 2023 | 2024 | 2025 |
| --- | --- | --- | --- |
| Without priors | 16.90 | 17.43 | 17.61 |
| With priors | 16.73 | 17.31 | 17.48 |
| Weeks 4-6 without / with | 16.13 / 15.66 | 21.47 / 21.25 | 19.04 / 18.67 |
| ATS edge >= 3 with priors | 51.0% | 49.5% | 50.6% |

Priors make totals slightly worse (totals do best with every team shrunk hard toward average)
and spreads more accurate in every season, most in weeks 4-6, still with no edge against the
spread. So only the spread model uses them. Source: `data/priors_backtest.csv` (totals).

## Red zone and turnover rates

Red zone points per trip (drives starting or ending inside the 20; TD = 7, FG = 3) and turnovers
per drive, for each offense and each defense allowed, shrunk toward league rates (20 trips / 60
drives), added to the points model as extra matchup terms (`finishing=True`).

| | 2023 | 2024 | 2025 | P4 card, all three |
| --- | --- | --- | --- | --- |
| RMSE, current | 16.38 | 17.03 | 15.46 | 114-88 (56.4%) |
| RMSE, with red zone + turnovers | 16.73 | 17.49 | 16.17 | 103-96 (51.8%) |

Worse in every season. Red zone and turnover rates swing on a handful of plays, and the part
that's real is already in EPA (which counts TDs vs FGs and turnovers), so the extra terms mostly
add noise. Off by default.

## Rest, travel, altitude and game script

Adjustments to the model's backtest totals, fit on two seasons and tested on the third
(`scripts/situational_backtest.py`, no API calls once the backtests are cached):

| Added factor | RMSE 2023 / 2024 / 2025 | Every edge >= 3 | P4 card |
| --- | --- | --- | --- |
| None (current model) | 16.38 / 17.03 / 15.46 | 53.0% | 114-88 (56.4%) |
| Rest: short weeks, off a bye | 16.39 / 17.05 / 15.48 | 52.5% | 111-90 |
| Travel distance, time zones | 16.38 / 17.00 / 15.47 | 53.2% | 113-89 |
| Altitude | 16.39 / 17.03 / 15.47 | 52.9% | 113-89 |
| Game script (spread over 14) | 16.37 / 17.00 / 15.45 | 52.5% | 110-92 |
| All four | 16.39 / 17.00 / 15.51 | 52.6% | 109-94 |

- Game script is a real model bias: with spreads over 14 the model's totals ran low by about
  1.6 points per extra 10 points of spread. The market didn't share it, and correcting it didn't
  help the picks.
- The model ran slightly low when the road team traveled far (about 2.5 points per 1,000 miles);
  correcting it didn't help the picks.
- Rest and altitude: no detectable effect (altitude covers only about 200 games).

None is used.

## Team consistency

How much each offense's and defense's game-by-game EPA swings around what the opponent-adjusted
ratings expected, shrunk toward the league norm. The board's `volatility` column combines the
four units in a game (1.0 = typical; most games fall between 0.88 and 1.11).

| Games by consistency | Model avg miss | Market avg miss | Every edge >= 3 |
| --- | --- | --- | --- |
| Most consistent 25% | 12.5 | 12.0 | 50.5% |
| 25-50% | 12.9 | 12.6 | 54.9% |
| 50-75% | 13.3 | 13.0 | 52.8% |
| Least consistent 25% | 13.4 | 13.0 | 53.9% |

| P4 weekly card, 2023-2025 | Record |
| --- | --- |
| Ranked by edge (current) | 114-88 (56.4%) |
| Ranked by edge / volatility | 106-96 (52.5%) |
| Consistent-team games only | 100-98 (50.5%) |
| Inconsistent-team games only | 96-90 (51.6%) |

Inconsistent teams are a little harder to predict, but equally so for the market, and using
consistency to choose picks made the card worse. Shown on the board for reference only.

## Coaching changes

A "new head coach" term in the preseason priors (head coach differs from last season's primary
coach; CFBD /coaches, saved in `data/priors/`): last season's rating times a new-coach flag.

- The term's weight was negative in all three folds (-0.24 to -0.40 for overall EPA), so with a
  new coach last season's rating carries over much less (roughly 10-15% instead of 40%).
- In-season spread accuracy barely moved (margin RMSE 16.73 -> 16.72, 17.31 -> 17.32,
  17.48 -> 17.46; weeks 4-6 15.66 -> 15.64, 21.25 -> 21.28, 18.67 -> 18.63), ATS unchanged.

Used in the spread priors because the effect is consistent, but it changes little once a few
games are played. CFBD has head coaches only, not coordinators.

## Transfer portal

Net transfer talent per team (CFBD /player/portal, one call per season, saved in
`data/priors/`): for each transfer, rating above a typical 3-star (0.80), floored at zero, added
to the new school and subtracted from the old, split into offense and defense and z-scored.
Added to the spread priors (leave-one-season-out):

| Spread margin RMSE | 2023 | 2024 | 2025 | ATS edge >= 3 |
| --- | --- | --- | --- | --- |
| Priors (last season, returning production, talent, coaching) | 16.72 | 17.32 | 17.46 | 50.3% |
| + net transfer talent | 16.71 | 17.32 | 17.46 | 50.2% |

The fitted weights were near zero (about 0.002 EPA/play per standard deviation). The 247
team talent composite already rates the roster including incoming transfers, so the portal adds
nothing new. Off by default (`USE_TRANSFERS`).

## Quarterback ratings

Each QB's EPA per pass, shrunk toward the league by 150 attempts; the expected starter is the QB
with the most attempts in the team's latest game; the offense is adjusted by (expected QB -
team's season-average QB) x pass share (`qb_offsets`, `qb=True`). No hindsight is used.

| | Every edge >= 3 | P4 card | Picks in games the adjustment moved 1+ pt |
| --- | --- | --- | --- |
| Current model | 53.0% | 116-86 (57.4%) | 50.9% |
| With QB ratings (scale fit on the other seasons) | 52.4% | 110-91 (54.7%) | 51.7% |

(The card here breaks ties slightly differently from the 114-88 elsewhere; both rows use the
same method.) The raw adjustment moved a third of projections by 1+ point, but fitting showed
only about 12% of it is real (fold estimates -0.07 to 0.30); RMSE didn't improve even in the
1,093 games with a QB change. A QB's EPA over a few hundred attempts is mostly noise, and the
team rating already reflects who has been playing. Off by default.

## Home-field bias in spreads (fixed)

The model's home-field term was fit each week. Early in the season it came out 2-4x its true
size (0.06-0.09 EPA/play per side through week 4 vs about 0.022 over full seasons of FBS-vs-FBS
games), because home routs of FCS teams and heavily shrunk team ratings get read as home field.
Projected margins leaned toward home teams by +0.7, +1.6 and +2.4 points in 2023-2025.

The spread model now holds home field at its full-season value (`adjust.HOME_FIELD`: 0.022
EPA/play overall, 0.014 rush, 0.026 pass):

| Spread model | 2023 | 2024 | 2025 |
| --- | --- | --- | --- |
| Margin RMSE, home field fitted weekly | 16.9 | 17.4 | 17.6 |
| Margin RMSE, home field fixed (+ priors) | 16.6 | 17.2 | 17.3 |
| Home-game bias (model minus actual), fitted | +0.7 | +1.6 | +2.4 |
| Home-game bias, fixed | -0.1 | -0.8 | -0.4 |

Still no ATS edge. The totals model keeps the weekly-fitted term: fixing it there made totals
much worse (RMSE 16.38 -> 16.71, 17.03 -> 17.62, 15.46 -> 16.13; card 114-88 -> 94-107). For a
total the home and road shifts cancel, and the inflated term soaks up early home routs of weak
teams that would otherwise inflate offensive ratings. Totals backtest results are unchanged.

## Home field

The model already has a league-wide home field term (about +0.085 EPA/play for the home
offense, none at neutral sites). Team-specific home field was tested with
`scripts/home_field_backtest.py`, fit on two seasons and tested on the third:

- Team home edge = (team's actual-minus-model margin at home - on the road) / 2, shrunk by 12
  games. Using home minus road cancels out a team the model simply under-rates.

| Spread model margin RMSE | 2023 | 2024 | 2025 |
| --- | --- | --- | --- |
| League-wide home field only (after the bias fix) | 16.68 | 17.26 | 17.26 |
| + team-specific home field | 16.69 | 17.18 | 17.18 |

(Before the bias fix: 16.94 -> 16.90, 17.51 -> 17.38, 17.61 -> 17.45.) ATS edge >= 3 stayed about
50%: slightly more accurate margins in two seasons, no betting edge. The largest home
edges are teams like Hawai'i (opponents cross the Pacific), UTSA, NC State and TCU, plus small
FCS programs. Used for the spread model's margins (`data/priors/team_home_field.csv`).

Home-stadium scoring effects for totals (does a stadium run high or low vs the model) didn't
carry over between seasons: RMSE 16.45 -> 16.44, 17.00 -> 17.05, 15.43 -> 15.55, card
107-83 -> 104-85 (non-neutral games only). Not used.

## Team game script (foot on the gas, prevent defense, fighting back)

Garbage time is left out of the ratings, but final scores include it, so this tested whether
teams differ in how they play it (`cfb_stats.game_script`, `scripts/game_script_backtest.py`).
From drive data, with each drive's starting score, for each team (league-relative, shrunk by 12 drives):

- **gas:** points per drive its offense scores while up 14+ in the 2nd half, vs its close-game
  rate. Kneeling or running out the clock counts as a 0-point drive.
- **prevent:** points per drive its defense allows while its team is up 14+ in the 2nd half.
- **fight:** points per drive its offense scores while down 14+ in the 2nd half.
- Reported only: **hurry** (seconds per play when down big vs close games) and **downs rate**
  (share of drives down big that end on downs; misses 4th-down tries that convert).

Each game's adjustment weights these by how many 2nd-half drives each team should spend up
14+, from the market spread (about 1.1 per team-game on average, 5 for a huge favorite).
Walk-forward (tendencies from earlier weeks only), 2024-2025 only so far (34 API calls; 2023 not
downloaded):

| Year-to-year correlation (same team) | gas | prevent | fight | hurry | downs rate |
| --- | --- | --- | --- | --- | --- |
| 2024 -> 2025 | +0.05 | +0.19 | +0.19 | +0.14 | +0.08 |

| Slope of the result on the adjustment (1 = fully real, SE in parentheses) | Totals vs model | Totals vs market | Margin vs model | Margin vs market |
| --- | --- | --- | --- | --- |
| gas | +0.21 (0.26) | +0.18 (0.25) | -0.04 (0.28) | +0.09 (0.24) |
| prevent | +0.05 (0.26) | +0.10 (0.25) | +0.31 (0.27) | +0.03 (0.24) |
| fight | -0.43 (0.27) | -0.30 (0.26) | +0.27 (0.28) | +0.07 (0.25) |
| All together | -0.05 (0.15) | +0.00 (0.15) | +0.18 (0.16) | +0.06 (0.14) |

| Fit on one season, tested on the other | RMSE | Every edge >= 3 | P4 card |
| --- | --- | --- | --- |
| Totals 2024, current / with game script | 17.03 / 17.03 | 333-284 / 333-284 | 40-29 / 40-29 |
| Totals 2025, current / with game script | 15.46 / 15.46 | 261-254 / 260-250 | 39-30 / 37-32 |
| Spreads 2024, current / with game script | 17.17 / 17.17 | 362-371 / 352-375 | |
| Spreads 2025, current / with game script | 17.28 / 17.27 | 386-382 / 385-376 | |

No signal. The adjustment moves half of totals by a point or more, but final scores don't follow
it (fitted scale about 0 for totals, under 0.2 for spreads). Teams barely repeat their late-game
habits from one season to the next, and a team only spends about one drive per game up 14+, so a
half-season says little. Heavier shrinkage (40 drives) didn't change this. Not used.

**Other lead sizes and quarters** (`--sweep`, `data/game_script_sweep.csv`). The 14-point,
2nd-half cutoff was arbitrary, so leads of 3, 7, 10, 14, 17 and 21+ were tried, counting from
the 1st, 3rd or 4th quarter (18 versions; slope of the result on the adjustment, SE in parentheses):

| Lead, from quarter | Lead drives / team-game | Totals vs model | Totals vs market | Margin vs model | Margin vs market |
| --- | --- | --- | --- | --- | --- |
| 3+, any | 4.1 | +0.01 (0.04) | +0.04 (0.04) | +0.04 (0.04) | -0.01 (0.03) |
| 7+, any | 3.1 | -0.05 (0.06) | +0.02 (0.06) | +0.01 (0.06) | +0.00 (0.05) |
| 7+, 2nd half | 1.9 | -0.04 (0.10) | +0.07 (0.10) | +0.05 (0.11) | -0.00 (0.10) |
| 10+, 2nd half | 1.5 | +0.01 (0.14) | +0.09 (0.14) | +0.10 (0.15) | +0.02 (0.13) |
| 14+, 2nd half | 1.1 | -0.06 (0.19) | +0.01 (0.18) | +0.22 (0.20) | +0.07 (0.17) |
| 17+, 2nd half | 0.9 | -0.10 (0.23) | -0.06 (0.22) | +0.44 (0.23) | +0.28 (0.20) |
| 21+, 2nd half | 0.65 | -0.30 (0.30) | -0.31 (0.30) | +0.59 (0.30) | +0.26 (0.27) |
| 7+, 4th quarter | 0.9 | -0.04 (0.25) | +0.27 (0.25) | +0.03 (0.27) | -0.07 (0.24) |
| 17+, 4th quarter | 0.5 | +0.05 (0.52) | +0.25 (0.51) | +0.89 (0.50) | +0.56 (0.44) |

- **Totals: nothing at any cutoff.** Small leads give lots of drives and tight estimates, and the
  slope is still about 0.
- **Spreads: a faint hint at big leads (17+).** Slopes are positive in every big-lead version,
  but none is clearly above zero against the market (at most about 1.3 SE, after trying 18
  versions), and the adjustment there is small (typically under half a point after scaling).
  Prevent defense is the most repeatable habit at big leads (year-to-year correlation 0.25-0.32).
  Worth rechecking with 2023 before using; not used.

## Special teams (spreads)

The EPA ratings cover scrimmage plays only, and the points fit has no field-position term, so
kicking and punting were missing from the model entirely. `cfb_stats.special_teams` values each
kicking play from drive data (no extra API calls once drives are cached):

- **Field goals:** 3 x (made - league make rate at that distance; distance = yards to goal + 17).
- **Punts:** expected points of the receiving team's next drive start vs the league's usual start
  after a punt from that spot. Covers punting, coverage and returns; return TDs count 7.
- **Kickoffs** (after scores): the receiving team's start vs the league average, in expected points.

Each team's rating is points per game above the league, shrunk by 4 games. A standard deviation
between teams is about 1.2 points per game. Walk-forward (ratings from earlier weeks only),
2024-2025 (`scripts/special_teams_backtest.py`, `data/special_teams_backtest.csv`):

| Is it a trait? | Field goals | Punts | Kickoffs | Total |
| --- | --- | --- | --- | --- |
| Year-to-year correlation | +0.20 | +0.21 | +0.35 | +0.37 |
| First half of season vs second | +0.20 | +0.12 | +0.17 | +0.30 |

| Slope of the result on the home-minus-away rating (1 = fully real) | vs model margin | vs market spread |
| --- | --- | --- |
| Field goals | +0.89 (0.63) | +0.13 (0.56) |
| Punts | +1.12 (0.50) | +0.93 (0.44) |
| Kickoffs | +1.89 (0.74) | +1.24 (0.66) |
| **Total** | **+0.98 (0.31)** | **+0.61 (0.28)** |

| Fit on one season, tested on the other | Scale | Margin RMSE | ATS edge >= 3 | P4 ATS edge >= 3 |
| --- | --- | --- | --- | --- |
| 2024 without / with | 0.97 | 17.17 / 17.13 | 362-371 / 360-366 | 105-116 / 105-118 |
| 2025 without / with | 0.99 | 17.28 / 17.24 | 386-382 / 386-373 | 116-108 / 119-106 |

- **Real for margins:** results follow the ratings at full size (fitted scale about 1 in both
  seasons, 3 standard errors from zero) and margin RMSE improved in both seasons. Added to the
  spread model's projected margins at full weight (`st_margin_adj`).
- **The market prices it only partly** (slope +0.61 against the spread, about 2 SE), mostly in
  punts and kickoffs. Betting the side with the better special teams by itself:

  | Special teams edge | 2024 | 2025 | Both |
  | --- | --- | --- | --- |
  | 1+ pt, all games | 215-196 | 232-182 | 447-378 (54.2%) |
  | 1+ pt, P4 games | 61-65 | 64-59 | 125-124 (50.2%) |
  | 2+ pts, all games | 48-44 | 48-30 | 96-74 (56.5%) |

  Above breakeven in both seasons, but all of it comes from non-P4 games, and it was found after
  a few slices. Promising, not proven: check it on 2023 (about 17 API calls) and track it in 2026.
- **ATS for the model's own picks barely moved** (about +0.5 to +1 point of win rate), still
  around 50%. Field goal kicking adds nothing for totals (slope +0.03 on the total).
- 2023 hasn't been tested (drives not downloaded).

## Penalties and officiating crews

Per-game penalties (CFBD /games/teams, 32 API calls for 2024-2025) fit as
`team's flags = league + commit[team] + draw[opponent] + crew[conference] + home`
(`cfb_stats.penalties`, `scripts/penalties_backtest.py`). CFBD has no crew data: conference games
use the conference's crew. One data error (an FCS team listed with 743 flags) is filtered out.

**Whose crew works non-conference games?** Each team's flags in non-conference road/home games,
vs its own conference-game rate, against the other conference's strictness (slope ~1 = that
team was flagged like the other conference's crew):

| | Home team | Visiting team |
| --- | --- | --- |
| 2024 | +0.57 (0.17) | +0.20 (0.17) |
| 2025 | -0.07 (0.18) | +0.70 (0.18) |

2024 looks like the visitors' crew, 2025 like the home crew, and neither assumption predicts
flags better (RMSE 2.918 home vs 2.898 visiting). Likely it varies by game contract. The model
uses the home crew; it changes little.

**Crews do differ, and it repeats** (flags per team-game vs average; double for the game):

| Conference crew | 2024 | 2025 |
| --- | --- | --- |
| Conference USA | +0.60 | +0.91 |
| Mountain West | +0.51 | +0.39 |
| Sun Belt | +0.31 | +0.14 |
| ACC | +0.01 | +0.15 |
| SEC | +0.16 | -0.20 |
| Big 12 | -0.54 | -0.36 |
| Big Ten | -0.18 | -1.38 |

Year-to-year correlation: crews 0.59 (flags) / 0.68 (yards); team flags committed 0.39, drawn 0.25.
Full table: `data/penalties_crews.csv`.

**Does it help the picks?** Walk-forward (penalty ratings from earlier weeks only), 2,240 games:

- Totals: each extra expected flag in a game goes with +0.50 points vs the model (SE 0.17) but
  only +0.15 (0.16) vs the market, so books already price most of it. Fit on one season and
  tested on the other: RMSE 17.03 -> 17.00 and 15.46 -> 15.43; every edge >= 3 333-284 -> 320-268
  and 261-254 -> 267-247; P4 card 40-29 -> 39-30 and 39-30 -> 38-31.
- Spreads: expected penalty yards don't move margins vs the model (-0.003 pts per yard, SE 0.028).

Slightly more accurate totals, no better picks. Not used.

## Turnover luck

From box scores (CFBD /games/teams, already downloaded for the penalty test): each team's
turnovers vs what it "deserved". Fumbles: 50% expected recovery. Interceptions: the league share of
passes defended (INTs + breakups) that are picked off, about 20%. A team that lost 1 of 6 fumbles,
or picked off 10 passes on 15 passes defended, was lucky. (CFBD omits zero stats; a missing
defensive stat counts as 0 when the box score has defensive stats at all.)
`scripts/turnovers_trenches_backtest.py`, 2024-2025, walk-forward:

- **It's luck:** first-half vs second-half correlation +0.07 (offense) and 0.00 (defense).
- **Margins:** teams whose ratings were flattered by turnover luck did a bit worse than the model
  projected (+0.52 points per turnover per game of luck, SE 0.33), but exactly as the market
  expected (0.00, SE 0.30). Books already strip out turnover luck.
- **Totals:** nothing vs the model (-0.12, SE 0.31).
- **Fading a team after a big turnover win** (the "market overreacts" angle): last game +2 or
  better 269-235 (53.4%), +3 or better 108-116 (48.2%), +4 or better 33-32. Noise.

Not used.

## Line of scrimmage (pass protection, pass rush, run blocking, run stopping)

Opponent-adjusted rates from box scores, each offense's line against each defense's front:
sack rate (sacks / dropbacks), pressure rate ((sacks + QB hurries) / dropbacks) and run stuff
rate (non-sack tackles for loss / rushes). Then two questions, walk-forward on 2024-2025: does the
expected rate add anything beyond EPA, and does a weak line facing a strong front do worse than
the two ratings add up to (the interaction)?

| | Repeats (weeks 1-7 vs 8+), offense / defense |
| --- | --- |
| Sack rate | +0.42 / +0.13 |
| Pressure rate | +0.49 / +0.29 |
| Run stuff rate | +0.44 / +0.42 |

| Slope (SE) | Totals vs model | Totals vs market | Margin vs model | Margin vs market |
| --- | --- | --- | --- | --- |
| Sack rate, per 10 points of rate | +0.55 (0.93) | +0.26 (0.90) | -2.60 (0.92) | -0.47 (0.82) |
| Pressure rate | +0.57 (0.49) | +0.41 (0.48) | -1.05 (0.57) | +0.11 (0.50) |
| Run stuff rate | +1.31 (0.60) | +0.11 (0.59) | -0.15 (0.62) | +0.89 (0.54) |
| Weak line x strong front, all three | within 1.8 SE of 0 | within 1.8 SE | within 1 SE | within 1.1 SE |

- Line play is a real, fairly stable trait, but EPA already captures nearly all of it.
- The mismatch itself (bad line vs great front) adds nothing beyond the two ratings.
- Two things the model gets slightly wrong (teams expected to take more sacks beat their projected
  margin; games with more expected run stuffs score a bit more than projected) are already priced
  by the market.

Not used. Box scores have no line yards; CFBD's advanced game stats do (line yards, stuff rate,
havoc by front seven), about 16 calls per season, if this is worth another look.

## Not backtested

- The conference availability report import itself (which players get listed, and how the
  reported statuses map to the chance of missing the game).
- Weather forecasts (the backtest uses observed weather; Sunday forecasts are 6 days out).
