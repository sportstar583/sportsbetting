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

Notes:
- Raw stats ending in `Opponent` are what opponents did against the team, so `totalYardsOpponent` is yards allowed and `sacksOpponent` is sacks allowed.
- Advanced stats leave out garbage time by default. Pass `--include-garbage-time` to keep it.
- Points and W/L come from completed regular-season and postseason games, including games against FCS opponents.

### Tests

```bash
python -m unittest
```
