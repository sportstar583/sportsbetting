# sportsbetting

## Power 4 college football team stats

Pulls season stats for every ACC, Big 12, Big Ten and SEC team from the
[CollegeFootballData API](https://collegefootballdata.com) and writes one row per team to CSV.

### API limits

The free CollegeFootballData tier allows **1,000 calls a month** (check with
`curl https://api.collegefootballdata.com/info`). A weekly card run uses about 20-40 calls.
Backtests download whole seasons, so run them with `--cache <dir>` and sparingly.

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

### Weekly card (one command)

```bash
python -m cfb_stats.weekly            # upcoming week; --week N for another
```

Pulls this week's Big Ten availability reports (only for games actually played that week),
builds the totals board with those injuries, and writes `data/<year>/card_week<N>.md`: the 3
biggest over edges and 3 biggest under edges among Power 4 games, the strategy with the best
backtest (114-88 over 2023-2025, see [BACKTEST.md](BACKTEST.md)). Best run twice a week:
Sunday morning, after new lines go up Saturday night and Saturday's results are in, and again
on Friday night after availability reports. If last week's advanced stats aren't loaded yet,
the card says so.

**Line shopping and tracking.** Each card pick shows the best total available (lowest for an
over, highest for an under) and the book offering it; CFBD carries up to three books (DraftKings,
Bovada, ESPN Bet), on about half of games. Every run logs its picks to
`data/<year>/card_log.csv`; once games are final, later runs fill in the closing line, the final
score, closing line value (points the line moved your way after the pick) and the result at the
best line, and the card shows the running record (`cfb_stats.tracking`).

**Board web page.** `python -m cfb_stats.board_page --week N` writes
`data/<year>/board_week<N>.html` from the week's board, injuries and card log (no API calls): every
game with line, model, edge, SP+, injury and wind adjustments and spreads, sortable and filterable
(card picks, edge 3+, not started), with projected score, pace, best lines and key injuries
when a row is opened.

**SP+ for reference.** If `data/<year>/sp_plus_week<N>.csv` exists (`team,sp,off,def`; other
columns ignored; save the week's full SP+ table there, or pass `--sp-plus <file>` to the board),
the board gets `sp_total` (with the model's wind correction added) and `sp_margin` columns
and the card an "SP+ total" column (`cfb_stats.sp_plus`). SP+ isn't used for picks: CFBD's free tier has only final SP+, so it
couldn't be backtested in-season. The card log records it, and the track record shows how picks
did when SP+ agreed or disagreed. With fewer than 100 teams in the file, last season's national
averages are used to turn ratings into points, so a partial table runs less accurate.

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
- **Run/pass matchup:** rush offense vs rush defense and pass offense vs pass defense,
  weighted by how often the offense runs (and how often opponents run on that defense). A
  run-heavy team facing a bad run defense gets credit the overall ratings average away.
  `matchup_adj` on the board shows how many points this moved the total; `--matchup 0` turns
  it off.
- **Tempo (time per play):** from drive data. Each offense's seconds per play, each defense's
  seconds per play allowed, and each team's share of the clock give the plays each side should
  run. A fast offense facing a team that holds the ball (e.g. an option offense) gets fewer plays.
- **Preseason priors (spreads only):** the spread model starts each team from last season's
  rating scaled by returning production, plus roster talent and a new-head-coach term (last
  season carries over much less after a coaching change), instead of from average
  (`python -m cfb_stats.priors --build <year>` once per season; saved in `data/priors/`). This
  made projected margins more accurate in 2023-2025 but made totals worse, so totals don't use it.
- **Home field:** the spread model holds the league-wide home edge at its full-season value
  (about 3 points); fitted week by week it came out 2-4x too big early in the season and tilted
  margins toward home teams. The totals model keeps it fitted, which tested better for totals.
- **Team home field (spreads only):** on top of the league-wide home edge, each team's own home
  field (home minus road performance vs the model over 2023-2025, shrunk) is added to projected
  margins. It made margins more accurate in every season; stadium effects didn't help totals.
- **Weather:** game-time forecasts from Open-Meteo (free; the CFBD weather endpoint is a paid
  tier) for each stadium. Wind over 10 mph lowers the total by 0.75 points per mph. Wind, rain
  and temperature are shown on the board and in the card notes. Over 2023-2025 the model ran
  3-4 points high in 15+ mph wind while the market didn't, so books already price it: the
  correction makes projections slightly more accurate but doesn't change betting results
  (`python -m cfb_stats.weather --backtest`, results in `data/weather_backtest.csv`).
  `--no-weather` turns it off.
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
    sacks, pass breakups, INTs), scaled by a factor fit on 2023-2025 box scores (a regular with
    no stat line counts as out; `python -m cfb_stats.defense --backtest`, see BACKTEST.md).
    The effect is small, usually under 2 points.
  - Offensive linemen: no individual stats, so they get no estimate unless you give
    `epa_delta`.

  Each injured player also gets their high school recruiting stars, 247 composite rating and
  national rank (`cfb_stats.recruiting`; classes are downloaded once to `data/recruiting/`),
  and the card lists QBs and 4-5 star recruits who are out or doubtful in its games. Stars are
  context only, not part of the point estimate.

  `epa_delta` overrides any estimate. `injury_impacts_week<N>.csv` lists each player's
  effect, and the board's `injury_adj` column shows how many points injuries moved each total.

**Read the backtest before betting** ([BACKTEST.md](BACKTEST.md)). Walk-forward over 2023-2025
(each week projected only from earlier games):

| Totals | 2023 | 2024 | 2025 | All three |
| --- | --- | --- | --- | --- |
| Weekly card: top 3 overs + top 3 unders, P4, vs closing | 54.7% | 58.0% | 56.5% | 56.4% (202 bets) |
| Every edge >= 3, vs closing total | 54.1% | 54.0% | 50.7% | 53.0% (1,716 bets) |
| Every edge >= 3, vs opening total | 56.2% | 52.5% | 53.4% | 54.0% (1,003 bets) |
| Model / market RMSE | 16.4 / 15.9 | 17.0 / 16.8 | 15.5 / 14.9 | |

Breakeven at -110 is 52.4%. The model is less accurate than the market, but its biggest
disagreements on Power 4 totals have beaten breakeven in all three seasons. That's a small,
unproven lean (found after trying several slices), so track it on 2026 before trusting it.
Against the spread there is no edge (49-52% everywhere).

The board prints this week's **weekly card** (`--card 3`, P4 games).

Treat the board as a screen for numbers worth a closer look, not as a list of picks. Full
results, including opening-line and QB-absence checks, are in [BACKTEST.md](BACKTEST.md).

### Tests

```bash
python -m unittest
```
