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
0.10 EPA/rush less than average, 0.10 is added to his EPA/rush for that game. Pass EPA is
adjusted by the opponent's pass defense the same way. `*_opp_adjustment` shows the net
effect: positive means he faced a tougher schedule than average.

Early in the season, teams have played few common opponents, so treat adjusted
numbers as rough until about week 5-6.

Notes:
- Raw stats ending in `Opponent` are what opponents did against the team, so `totalYardsOpponent` is yards allowed and `sacksOpponent` is sacks allowed.
- Advanced stats leave out garbage time by default. Pass `--include-garbage-time` to keep it.
- Points and W/L come from completed regular-season and postseason games, including games against FCS opponents.

### Tests

```bash
python -m unittest
```
