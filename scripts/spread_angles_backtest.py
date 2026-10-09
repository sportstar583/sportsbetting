"""Spread angles, 2023-2025: skill-player absences, rest, and coach tendencies (4th-down
aggressiveness, past record against the spread, and past record against ranked teams).
Results are in BACKTEST.md.

  CFBD_CACHE=.cfbd_cache python scripts/spread_angles_backtest.py

Each angle is tested two ways:
- as a betting rule on every FBS game with a closing spread (median across books), and
- as an adjustment to the spread model's margin (data/<year>/spreads_backtest_games.csv, weeks
  4+), fit on two seasons and tested on the third.

Skill-player absences: college has no historical injury reports or snap counts, so absences come
from box scores. A "regular" is a non-QB who had a carry or catch in his team's previous game and
averaged 8%+ of its carries + receptions over its last 4 games; if he has no carry or catch in
this game, his share counts as lost. That includes surprise scratches nobody could bet, so it is a
best case for the rule (the NFL version used Out/Doubtful reports).

Coach records use his previous 3 seasons at any school (2021 on), never the season being tested.
Rankings are the AP poll entering the week.

API cost the first time: about 55 calls (weekly box scores 2023-2025, season lines and AP polls
2021-2025, 2021 games); none once cached.
"""
import csv, datetime, math, os, statistics
from collections import defaultdict
import numpy as np
from cfb_stats.api import CFBDClient
from cfb_stats.priors import coaches
from cfb_stats.totals import consensus_spread
import special_teams_backtest as st

c = CFBDClient(cache_dir=os.environ.get("CFBD_CACHE"))  # run from the repo root
YEARS, HIST = (2023, 2024, 2025), (2021, 2022)
MIN_SHARE, SHORT, BYE = 0.08, 5, 13
ATS_PRIOR = 20  # pseudo-games at .500 when shrinking a coach's ATS record


def ts(s):
    return datetime.datetime.fromisoformat(s.replace("Z", "+00:00"))


def load(y):
    games = [g for g in c.games(y, "regular") if g.get("startDate")]
    spreads = {lr["id"]: consensus_spread(lr)[0] for lr in c.get("/lines", year=y, seasonType="regular")}
    polls = {}
    for w in c.get("/rankings", year=y):
        if w["seasonType"] == "regular":
            for p in w["polls"]:
                if p["poll"] == "AP Top 25":
                    polls[w["week"]] = {r["school"]: r["rank"] for r in p["ranks"]}
    # rest days (season opener counts as fully rested)
    sched = defaultdict(list)
    for g in games:
        for t in (g["homeTeam"], g["awayTeam"]):
            sched[t].append((ts(g["startDate"]), g["id"]))
    rest = {}
    for t, lst in sched.items():
        lst.sort()
        for i, (when, gid) in enumerate(lst):
            rest[(t, gid)] = min((when - lst[i - 1][0]).days, 14) if i else 14
    rows = []
    for g in games:
        if g.get("homePoints") is None or g.get("awayPoints") is None or spreads.get(g["id"]) is None:
            continue
        if g.get("homeClassification") != "fbs" or g.get("awayClassification") != "fbs":
            continue
        wk = g["week"]
        rk = next((polls[w] for w in range(wk, 0, -1) if w in polls), {})
        margin = g["homePoints"] - g["awayPoints"]
        rows.append({"season": y, "week": wk, "id": g["id"], "home": g["homeTeam"], "away": g["awayTeam"],
                     "spread": spreads[g["id"]], "margin": margin, "ats": margin + spreads[g["id"]],
                     "home_rank": rk.get(g["homeTeam"]), "away_rank": rk.get(g["awayTeam"]),
                     "home_rest": rest[(g["homeTeam"], g["id"])], "away_rest": rest[(g["awayTeam"], g["id"])]})
    return games, rows


def skill_losses(y, games):
    """{(team, game id): share of carries + receptions lost to absent regulars}."""
    weeks = sorted({g["week"] for g in games if g.get("homePoints") is not None})
    box = defaultdict(list)  # team -> [(week, game id, {player: touches})]
    passers = set()
    for w in weeks:
        for g in c.get("/games/players", year=y, week=w, seasonType="regular"):
            for t in g["teams"]:
                touches = defaultdict(int)
                for cat in t["categories"]:
                    for ty in cat["types"]:
                        for a in ty["athletes"]:
                            if cat["name"] == "passing" and ty["name"] == "C/ATT":
                                try:
                                    if int(a["stat"].split("/")[1]) >= 5:
                                        passers.add(a["id"])
                                except (ValueError, IndexError):
                                    pass
                            elif (cat["name"], ty["name"]) in (("rushing", "CAR"), ("receiving", "REC")):
                                try:
                                    touches[a["id"]] += int(a["stat"])
                                except ValueError:
                                    pass
                box[t["team"]].append((w, g["id"], dict(touches)))
    out = {}
    for team, lst in box.items():
        lst.sort()
        for i, (w, gid, cur) in enumerate(lst):
            prior = lst[max(0, i - 4):i]
            if len(prior) < 2:
                continue
            avg = defaultdict(float)
            for _, _, tc in prior:
                tot = sum(v for p, v in tc.items() if p not in passers) or 1
                for p, v in tc.items():
                    if p not in passers:
                        avg[p] += v / tot / len(prior)
            regulars = {p: s for p, s in avg.items() if s >= MIN_SHARE and p in prior[-1][2]}
            out[(team, gid)] = sum(s for p, s in regulars.items() if cur.get(p, 0) == 0)
    return out


def covered(r, team):
    """1 cover, 0 loss, None push, from team's side."""
    if r["ats"] == 0:
        return None
    return int((r["ats"] > 0) == (team == r["home"]))


def coach_records(all_rows, coach_map):
    """{(coach, year): {"ats": (covers, games), "rk25"/"rk10"/"rk5": (su wins, ats covers, games)}}
    from his previous 3 seasons."""
    per = defaultdict(list)  # coach -> [(year, ats, su_win, opp_rank)]
    for r in all_rows:
        for team, opp_rank in ((r["home"], r["away_rank"]), (r["away"], r["home_rank"])):
            coach = coach_map.get((team, r["season"]))
            cov = covered(r, team)
            if coach and cov is not None:
                won = (r["margin"] > 0) == (team == r["home"])
                per[coach].append((r["season"], cov, int(won), opp_rank))
    out = {}
    for coach, lst in per.items():
        for y in YEARS:
            prev = [x for x in lst if y - 3 <= x[0] < y]
            if not prev:
                continue
            rec = {"ats": (sum(x[1] for x in prev), len(prev))}
            for tier in (25, 10, 5):
                vs = [x for x in prev if x[3] is not None and x[3] <= tier]
                rec[f"rk{tier}"] = (sum(x[2] for x in vs), sum(x[1] for x in vs), len(vs))
            out[(coach, y)] = rec
    return out


def shrunk(k, n, prior=ATS_PRIOR):
    return (k + 0.5 * prior) / (n + prior)


def go_rates():
    """{(team, year, week): coach's 4th-down go rate entering the week (history + this season)}."""
    seasons = {y: st.season_events(y) for y in (2022,) + YEARS}
    cmap = coaches(c)
    tsn = [(a / n, m / g) for ev in seasons.values()
           for a, n, m, g in st.go_counts(99, ev[0], ev[1], ev[2], ev[4]).values() if n >= 50 and g >= 6]
    slope = float(np.polyfit([x[1] for x in tsn], [x[0] for x in tsn], 1)[0])
    lg = float(np.mean([x[0] for x in tsn]))
    hist = st.coach_history(seasons, cmap, slope, lg)
    out = {}
    for y in YEARS:
        ev = seasons[y]
        for w in range(1, 18):
            for t, (a, n, m, g) in st.go_counts(w, ev[0], ev[1], ev[2], ev[4]).items():
                h = hist.get((cmap.get((t, y)), y))
                p = h[0] if h else lg
                out[(t, y, w)] = (a + p * st.COACH_WEIGHT) / (n + st.COACH_WEIGHT)
    return out, lg


def record(bets):
    """bets: [(season, 1/0/None)] -> text."""
    w = sum(b == 1 for _, b in bets); l = sum(b == 0 for _, b in bets)
    if not w + l:
        return "no bets"
    by = " ".join(f"{s}:{sum(b == 1 for x, b in bets if x == s)}-{sum(b == 0 for x, b in bets if x == s)}"
                  for s in YEARS)
    units = w - 1.1 * l
    z = (w / (w + l) - 0.5) / math.sqrt(0.25 / (w + l))
    return f"{w}-{l} ({w / (w + l):.1%}, {units:+.1f}u, z {z:+.1f})  [{by}]"


def main():
    coach_map = coaches(c)
    data = {y: load(y) for y in HIST + YEARS}
    all_rows = [r for y in data for r in data[y][1]]
    recs = coach_records(all_rows, coach_map)
    go, lg_go = go_rates()
    test = []
    for y in YEARS:
        games, rows = data[y]
        loss = skill_losses(y, games)
        for r in rows:
            hc, ac = coach_map.get((r["home"], y)), coach_map.get((r["away"], y))
            r.update(home_loss=loss.get((r["home"], r["id"])), away_loss=loss.get((r["away"], r["id"])),
                     home_rec=recs.get((hc, y)), away_rec=recs.get((ac, y)),
                     home_go=go.get((r["home"], y, r["week"])), away_go=go.get((r["away"], y, r["week"])))
            test.append(r)
    print(f"{len(test)} FBS games with a closing spread, 2023-2025 "
          f"(skill-player data for {sum(r['home_loss'] is not None and r['away_loss'] is not None for r in test)})")
    print("Breakeven at -110 is 52.4%. z = standard errors above a coin flip.\n")

    def side_bets(pred):
        """pred(r) -> team to back or None."""
        out = []
        for r in test:
            t = pred(r)
            if t:
                out.append((r["season"], covered(r, t)))
        return out

    print("Skill-player absences (back the team that lost less carry + catch share):")
    for gap in (0.10, 0.20, 0.30):
        def rule(r, gap=gap):
            if r["home_loss"] is None or r["away_loss"] is None:
                return None
            d = r["away_loss"] - r["home_loss"]
            return r["home"] if d >= gap else r["away"] if -d >= gap else None
        print(f"  gap >= {gap:.0%} of touches: {record(side_bets(rule))}")

    print("\nRest:")
    print("  back the team with 3+ more days of rest:   " + record(side_bets(
        lambda r: r["home"] if r["home_rest"] - r["away_rest"] >= 3 else r["away"] if r["away_rest"] - r["home_rest"] >= 3 else None)))
    print("  fade a team on a short week (5 days or less) vs a rested one: " + record(side_bets(
        lambda r: r["away"] if r["home_rest"] <= SHORT < r["away_rest"] else r["home"] if r["away_rest"] <= SHORT < r["home_rest"] else None)))
    print("  back a team off a bye vs one that isn't:   " + record(side_bets(
        lambda r: None if r["week"] <= 1 else r["home"] if r["home_rest"] >= BYE > r["away_rest"] else r["away"] if r["away_rest"] >= BYE > r["home_rest"] else None)))

    print("\nCoach record against the spread (previous 3 seasons, shrunk toward .500):")
    for gap in (0.05, 0.10):
        def rule(r, gap=gap):
            a, b = r["home_rec"], r["away_rec"]
            if not a or not b or a["ats"][1] < 20 or b["ats"][1] < 20:
                return None
            d = shrunk(*a["ats"]) - shrunk(*b["ats"])
            return r["home"] if d >= gap else r["away"] if -d >= gap else None
        print(f"  back the better ATS coach, gap >= {gap:.0%}: {record(side_bets(rule))}")

    print("\nCoach 4th-down aggressiveness (history + this season):")
    for gap in (0.05, 0.10):
        def rule(r, gap=gap):
            a, b = r["home_go"], r["away_go"]
            if a is None or b is None:
                return None
            return r["home"] if a - b >= gap else r["away"] if b - a >= gap else None
        print(f"  back the more aggressive coach, gap >= {gap:.0%} go rate: {record(side_bets(rule))}")

    print("\nCoach record against ranked teams (previous 3 seasons), when facing a ranked team:")
    for tier in (25, 10, 5):
        def facing(r, tier=tier):
            for team, rec, opp_rank in ((r["home"], r["home_rec"], r["away_rank"]), (r["away"], r["away_rec"], r["home_rank"])):
                if opp_rank is not None and opp_rank <= tier and rec and rec[f"rk{tier}"][2] >= 4:
                    yield team, rec[f"rk{tier}"]
        base = [(r["season"], covered(r, t)) for r in test for t, _ in facing(r)]
        good = [(r["season"], covered(r, t)) for r in test for t, (su, ats, n) in facing(r) if shrunk(ats, n, 10) >= 0.55]
        bad = [(r["season"], 1 - covered(r, t) if covered(r, t) is not None else None)
               for r in test for t, (su, ats, n) in facing(r) if shrunk(ats, n, 10) <= 0.45]
        su_good = [(r["season"], covered(r, t)) for r in test for t, (su, ats, n) in facing(r) if su / n >= 0.5]
        print(f"  top {tier}: every team facing one (coach with 4+ such games): {record(base)}")
        print(f"     back coaches who covered 55%+ of them before:   {record(good)}")
        print(f"     fade coaches who covered 45% or less before:    {record(bad)}")
        print(f"     back coaches who won half or more of them:      {record(su_good)}")

    # As adjustments to the spread model: leave-one-season-out on its backtest margins.
    feats = {
        "skill-player absences": lambda r: (r["away_loss"] or 0) - (r["home_loss"] or 0),
        "rest days": lambda r: (r["home_rest"] - r["away_rest"]) / 7,
        "coach ATS record": lambda r: (shrunk(*r["home_rec"]["ats"]) if r["home_rec"] else .5)
                                      - (shrunk(*r["away_rec"]["ats"]) if r["away_rec"] else .5),
        "coach go rate": lambda r: (r["home_go"] or lg_go) - (r["away_go"] or lg_go),
        "coach ATS vs top 25 (when facing one)": lambda r: (
            (shrunk(*r["home_rec"]["rk25"][1:], 10) - .5 if r["home_rec"] and r["away_rank"] else 0)
            - (shrunk(*r["away_rec"]["rk25"][1:], 10) - .5 if r["away_rec"] and r["home_rank"] else 0)),
    }
    by_id = {r["id"]: r for r in test}
    mrows = []
    for y in YEARS:
        for s in csv.DictReader(open(f"data/{y}/spreads_backtest_games.csv")):
            r = by_id.get(int(s["game_id"]))
            if r and s.get("proj_margin") and s.get("actual_margin"):
                mrows.append((y, float(s["proj_margin"]), r))
    season = np.array([y for y, _, _ in mrows]); pm = np.array([p for _, p, _ in mrows])
    am = np.array([r["margin"] for _, _, r in mrows], dtype=float); sp = np.array([r["spread"] for _, _, r in mrows])
    print(f"\nAs adjustments to the spread model ({len(mrows)} games, weeks 4+): margin RMSE by season, ATS at edge >= 3")
    for name, f in [("current model", None)] + list(feats.items()):
        x = np.zeros(len(mrows)) if f is None else np.array([f(r) for _, _, r in mrows], dtype=float)
        rm, w, n = [], 0, 0
        for s in YEARS:
            tr, te = season != s, season == s
            b = np.linalg.lstsq(np.column_stack([np.ones(tr.sum()), x[tr]]), (am - pm)[tr], rcond=None)[0] if f else [0, 0]
            adj = pm + b[1] * x
            rm.append(math.sqrt(((am - adj)[te] ** 2).mean()))
            e, d = adj + sp, am + sp
            k = te & (np.abs(e) >= 3) & (d != 0)
            w += int(((e > 0) == (d > 0))[k].sum()); n += int(k.sum())
        extra = ""
        if f is not None:
            A = np.column_stack([np.ones(len(x)), x]); t = am + sp
            b = np.linalg.lstsq(A, t, rcond=None)[0]
            se = math.sqrt(np.linalg.inv(A.T @ A)[1, 1] * (t - A @ b).var())
            extra = f"  | vs market: {b[1]:+.2f} ({se:.2f}) pts per unit"
        print(f"  {name:<40} RMSE {' / '.join(f'{v:.2f}' for v in rm)}  ATS {w}-{n - w} ({w / n:.1%}){extra}")


if __name__ == "__main__":
    main()
