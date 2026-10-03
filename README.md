# sportsbetting

## Power 4 college football team stats

Pulls season stats for every ACC, Big 12, Big Ten and SEC team from the
[CollegeFootballData API](https://collegefootballdata.com) and writes one row per team to CSV.

### Setup

1. Get a free API key: https://collegefootballdata.com/key
2. `pip install -r requirements.txt`
3. `export CFBD_API_KEY=your_key`

### Run

```bash
python -m cfb_stats.collect                  # current season (2026 from August on)
python -m cfb_stats.collect --year 2025      # a past season
python -m cfb_stats.collect --include-notre-dame
```

Output goes to `data/<year>/`:

| File | What's in it |
| --- | --- |
| `offense.csv` | points/game, yards/game, yards/play, rush & pass splits, 3rd/4th down %, turnovers, sacks allowed, plus advanced offense (`adv_off_*`: EPA/PPA, success rate, explosiveness, line yards, havoc allowed, standard/passing-down splits) |
| `defense.csv` | points & yards allowed, yards/play allowed, 3rd down % allowed, sacks, TFLs, takeaways, turnover margin, plus advanced defense (`adv_def_*`) |
| `special_teams.csv` | kick and punt return totals |
| `all_stats.csv` | all of the above in one wide table |
| `adjusted_team_epa.csv` | raw vs **opponent-adjusted** EPA/play, success rate and explosiveness (overall, rush, pass) for offense and defense, FBS rank for each, net adjusted EPA, and strength of schedule |
| `adjusted_player_epa.csv` | raw vs **opponent-adjusted** EPA/play (all, rush, pass) for every P4 player, plus how much the schedule moved it |

### Opponent adjustment

Raw stats reward teams and players who face bad defenses. 200 rushing yards against the
best run defense in the country should count for more than 200 against the worst.

`cfb_stats/adjust.py` fits each metric over **every FBS game** of the season (not just P4,
so non-conference opponents are rated too):

```
EPA on a team's plays in a game = average + offense strength + opponent defense strength + home field
```

It uses a ridge regression weighted by play count. "Adjusted offense" is what a team
would average against an average defense at a neutral site. "Adjusted defense" is the EPA
it would allow to an average offense (lower is better). The ridge penalty (`--alpha`,
default 150 plays) pulls teams with few games toward average. That matters early in
the season. Raise it for more shrinkage.

Players are adjusted game by game. If a running back faces a run defense that allows
0.10 EPA/rush less than average, 0.10 is added to their EPA/rush for that game. Pass EPA is
adjusted by the opponent's pass defense the same way. `*_opp_adjustment` shows the net
effect: positive means they faced a tougher schedule than average.

Early in the season, teams have played few common opponents, so treat adjusted
numbers as rough until about week 5-6.

Notes:
- Raw stats ending in `Opponent` are what opponents did against the team, so `totalYardsOpponent` is yards allowed and `sacksOpponent` is sacks allowed.
- Advanced stats leave out garbage time by default. Pass `--include-garbage-time` to keep it.
- Points and W/L come from completed regular-season and postseason games, including games against FCS opponents.

### Over/under board

`cfb_stats/totals.py` projects each game's total from adjusted EPA/play and tempo, then
compares it to the market total (median across books from the CFBD `/lines` endpoint):

```bash
python -m cfb_stats.totals                     # this week -> data/<year>/totals_week<N>.csv
python -m cfb_stats.totals --week 6 --min-edge 5
python -m cfb_stats.totals --injuries injuries.csv
python -m cfb_stats.totals --year 2025 --backtest --cache .cfbd_cache
```

What goes into a projection:

- **Efficiency:** opponent-adjusted EPA/play for each offense against the other defense, plus home field.
- **Tempo (time per play):** from drive data. Each offense's seconds per play, each defense's
  seconds per play allowed, and each team's share of the clock give the plays each side should
  run. A fast offense facing a team that holds the ball (e.g. an option offense) gets fewer plays.
- **Not over-rewarding routs of weak teams:** games against FCS opponents count half
  (`--fcs-weight 0.5`). Single games where a team beat its expected EPA by a lot are down-weighted
  (`--huber-k 1.5`, a Huber fit), so a 63-7 win over a bad team moves a rating less than its
  raw margin would. Garbage time is already excluded.
- **Injuries:** `--injuries file.csv` with columns `team,player,status,side,epa_delta`
  (optional `player_id`, `position`; template: `injuries.example.csv`). The CFBD API has no
  injury data. For Big Ten games, pull the conference availability report automatically:

  ```bash
  python -m cfb_stats.availability --week 5      # -> data/2026/injuries_week5.csv
  python -m cfb_stats.totals --week 5 --injuries data/2026/injuries_week5.csv
  ```

  It reads the public feed behind bigten.org's availability page, skips Available and Exempt
  players (Exempt players aren't required to be reported) and kickers/punters, and matches
  players to CFBD by jersey number. `status` is out/doubtful/questionable/probable/game time
  decision/out - (1st half), or a 0-1 chance of missing the game.
  - Offensive skill players: estimated from season EPA and usage share against a backup-level
    player at the position (calibrated on QB absences, see below).
  - Defenders: estimated from their share of the team's defensive production (tackles, TFLs,
    sacks, pass breakups, INTs). This scale is **not calibrated**, since there is no historical
    availability data to fit it to, so it is kept small.
  - Offensive linemen: no individual stats, so they get no estimate unless you give
    `epa_delta`.

  `epa_delta` overrides any estimate. `injury_impacts_week<N>.csv` lists each player's
  effect, and the board's `injury_adj` column shows how many points injuries moved each total.

**Read the backtest before betting.** Walk-forward (each week projected only from earlier
games), weeks 4+, games where both teams have 3+ games of data:

| | 2025 (~1,120 games) | 2024 (~1,120 games, out of sample) |
| --- | --- | --- |
| Model RMSE (baseline / with tempo + rout damping) | 15.9 / 15.5 | 17.2 / 17.0 |
| Closing market RMSE | 14.9 | 16.8 |
| Win % vs closing total, edge >= 3 | 50.0% | 56.8% |
| Win % vs closing total, edge >= 5 | 49.2% | 56.5% |

Breakeven at -110 is 52.4%. The two seasons disagree, and the plain baseline model also hit
~55% in 2024, so that season looks like a good year for this style of model, not proof of
an edge. Tempo is the one addition that improved accuracy in both seasons.

The injury estimate was checked on games where a team's season-to-date starting QB didn't
play (who actually played stands in for an injury report). The raw estimate overshot by about
3x, so it is scaled by 0.35, which was fit on 2025. On 2024, out of sample, it left accuracy
slightly worsened accuracy (RMSE 17.77 to 17.85) and nudged pick rate from 54.2% to 54.9%. Markets already
move on QB news.

The board also has projected margins against the spread (`proj_margin`, `spread_edge`),
from a separately tuned, lightly shrunk model. The backtest found **no edge against the
spread** (49-51% at every threshold in 2024 and 2025), so no spread picks are listed.

Treat the board as a screen for numbers worth a closer look, not as a list of picks. Full
results, including opening-line and QB-absence checks, are in [BACKTEST.md](BACKTEST.md).

### Tests

```bash
python -m unittest
```
