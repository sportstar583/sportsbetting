"""Special teams ratings from drive data: field goals, punts and kickoffs, in points per game.

The EPA ratings cover scrimmage plays only, and the points fit has no field position term,
so a team that kicks well or flips field position on punts and kickoffs scores (or allows)
points the model doesn't see. Every drive carries where it started and ended, its result and
the score, which is enough to value each kicking play against the league:

- Expected points of a drive starting X yards from goal (EP), from all drives in the data.
- Field goals: kick distance = yards to goal + 17. Value to the kicking team =
  3 x (made - league make rate at that distance).
- Punts: the receiving team's next drive start vs the league's typical start after a punt from
  that spot, in EP. Credited to the punting team, debited to the receiving team, so it covers
  punting, coverage and returns together. Punt return TDs count as 7 for the returner.
- Kickoffs (after a score): the receiving team's start vs the league's average, in EP, credited
  to the kicking team and debited to the receiver.

Each team's rating is its value per game in each phase, shrunk toward 0 by PRIOR_GAMES.
Backtest: scripts/special_teams_backtest.py (results in BACKTEST.md).
"""

import math
from collections import defaultdict

PRIOR_GAMES = 4
FG_EXTRA = 17  # end zone (10) + snap distance (7)
BIN = 5  # yards


def _score(d):
    so, sd = d.get("startOffenseScore"), d.get("startDefenseScore")
    eo, ed = d.get("endOffenseScore"), d.get("endDefenseScore")
    if None in (so, sd, eo, ed):
        return None
    return eo - so, ed - sd


def _bin(y):
    return min(max(int(y) // BIN, 0), 100 // BIN)


def _smooth(sums, counts, prior):
    """Per-bin averages, each shrunk toward its neighbours' average."""
    out = {}
    keys = sorted(counts)
    total = sum(sums.values()) / max(sum(counts.values()), 1)
    for k in keys:
        nb = [j for j in (k - 1, k + 1) if counts.get(j)]
        base = sum(sums[j] for j in nb) / sum(counts[j] for j in nb) if nb else total
        out[k] = (sums[k] + base * prior) / (counts[k] + prior)
    return out, total


def _by_game(drives):
    games = defaultdict(list)
    for d in drives:
        if d.get("gameId") is not None:
            games[d["gameId"]].append(d)
    for ds in games.values():
        ds.sort(key=lambda d: d.get("driveNumber") or 0)
    return games


def _drive_pts(d):
    s = _score(d)
    return max(s[0], 0) if s else 0


def expected_points(drives):
    """EP by start bin -> (dict, league average)."""
    sums, counts = defaultdict(float), defaultdict(int)
    for d in drives:
        y = d.get("startYardsToGoal")
        if y is None or not 0 < y <= 100 or d.get("driveResult") in ("END OF HALF", "END OF GAME"):
            continue
        sums[_bin(y)] += _drive_pts(d)
        counts[_bin(y)] += 1
    return _smooth(sums, counts, 50)


class EP:
    def __init__(self, drives):
        self.table, self.avg = expected_points(drives)

    def __call__(self, y):
        return self.table.get(_bin(y), self.avg) if y is not None else self.avg


def _is_fg(res):
    return "FG" in res


def _fg_made(d, res):
    s = _score(d)
    if s is not None:
        return s[0] == 3
    return res == "FG"


def events(drives, ep=None):
    """Kicking plays as (game id, phase, kicking team, receiving team or None, raw, expected key)."""
    ep = ep or EP(drives)
    out = []
    for gid, ds in _by_game(drives).items():
        for d, nxt in zip(ds, ds[1:] + [None]):
            res = (d.get("driveResult") or "").upper()
            o, df = d.get("offense"), d.get("defense")
            s = _score(d)
            if _is_fg(res) and d.get("endYardsToGoal") is not None:
                dist = d["endYardsToGoal"] + FG_EXTRA
                out.append({"game": gid, "phase": "fg", "kicker": o, "receiver": None, "key": dist,
                            "raw": 3.0 if _fg_made(d, res) else 0.0})
            elif "PUNT" in res and d.get("endYardsToGoal") is not None:
                if "TD" in res:
                    val = 7.0  # returned for a score: worth a TD to the receiver
                elif nxt is not None and nxt.get("offense") == df and nxt.get("startYardsToGoal") is not None:
                    val = ep(nxt["startYardsToGoal"])
                else:
                    continue
                out.append({"game": gid, "phase": "punt", "kicker": o, "receiver": df,
                            "key": _bin(d["endYardsToGoal"]), "raw": val})
            if s and nxt is not None and res != "SF" and nxt.get("startYardsToGoal") is not None \
                    and "PUNT" not in res and (nxt.get("startPeriod") == d.get("endPeriod") or not d.get("endPeriod")):
                kicker = o if s[0] > 0 else df if s[1] > 0 else None
                if kicker and nxt.get("offense") != kicker:
                    out.append({"game": gid, "phase": "kick", "kicker": kicker, "receiver": nxt.get("offense"),
                                "key": 0, "raw": ep(nxt["startYardsToGoal"])})
    return out


def _fg_curve(evts):
    """Make rate by distance: logistic fit on attempts."""
    xs = [(e["key"], e["raw"] / 3) for e in evts if e["phase"] == "fg" and 15 <= e["key"] <= 70]
    if len(xs) < 20:
        return lambda dist: 0.75
    a, b = 5.0, -0.1
    for _ in range(25):  # Newton steps on the log-likelihood
        ga = gb = haa = hab = hbb = 0.0
        for x, y in xs:
            p = 1 / (1 + math.exp(-(a + b * x)))
            w = p * (1 - p)
            ga += y - p; gb += (y - p) * x
            haa += w; hab += w * x; hbb += w * x * x
        det = haa * hbb - hab * hab
        if not det:
            break
        a += (hbb * ga - hab * gb) / det
        b += (haa * gb - hab * ga) / det
    return lambda dist: 1 / (1 + math.exp(-(a + b * dist)))


def ratings(drives, prior=PRIOR_GAMES, ep=None):
    """{team: {"fg", "punt", "kick", "total", "games"}}: points per game above league, shrunk."""
    evts = events(drives, ep)
    fg_p = _fg_curve(evts)
    exp = {}
    for phase in ("punt", "kick"):
        sums, counts = defaultdict(float), defaultdict(int)
        for e in evts:
            if e["phase"] == phase:
                sums[e["key"]] += e["raw"]
                counts[e["key"]] += 1
        exp[phase] = _smooth(sums, counts, 30)
    val = defaultdict(lambda: defaultdict(float))
    for e in evts:
        if e["phase"] == "fg":
            val[e["kicker"]]["fg"] += e["raw"] - 3 * fg_p(e["key"])
        else:
            table, avg = exp[e["phase"]]
            v = table.get(e["key"], avg) - e["raw"]  # receiver starting worse than usual = good for kicker
            val[e["kicker"]][e["phase"]] += v
            val[e["receiver"]][e["phase"]] -= v
    games = defaultdict(set)
    for d in drives:
        for t in (d.get("offense"), d.get("defense")):
            if t and d.get("gameId") is not None:
                games[t].add(d["gameId"])
    out = {}
    for t, g in games.items():
        n = len(g)
        row = {k: val[t][k] / (n + prior) if n + prior else 0.0 for k in ("fg", "punt", "kick")}
        row["total"] = row["fg"] + row["punt"] + row["kick"]
        row["games"] = n
        out[t] = row
    return out
