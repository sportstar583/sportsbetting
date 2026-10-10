"""Game script: does a team keep its foot on the gas with a big lead, and does its defense
sit back in prevent?

Garbage time is left out of the EPA ratings, so projections describe competitive football.
Final scores (what totals and spreads settle on) include the rest. Teams differ there: some
keep throwing and scoring up 21, some run the clock out with backups; some defenses keep
attacking, others play prevent and give up late points.

From drive data (every drive carries the score when it started), for each team:

    gas     = points per drive its offense scores while leading by LEAD+ in the 2nd half
              minus its points per drive in close games (score within LEAD),
              minus the league's average of that same difference
    prevent = points per drive its defense allows while its team leads by LEAD+ in the 2nd
              half, minus what it allows in close games, minus the league average difference
    fight   = points per drive its offense scores while trailing by LEAD+ in the 2nd half,
              minus its close-game rate, minus the league average difference

Both are shrunk toward 0 by PRIOR_DRIVES pseudo-drives. gas > 0: keeps scoring more than a
typical team does with a lead (pedal to the metal). gas < 0: takes its foot off the gas.
prevent > 0: its defense gives up more late than a typical defense does (soft prevent).
fight > 0: keeps attacking when down big (going for it, hurrying) instead of running out the clock.

Also reported, not used in the adjustment: hurry (how much faster its offense plays when
trailing big than in close games, seconds per play, league-relative) and downs_rate (share of
its trailing drives that end on downs, a rough read on 4th-down aggressiveness; it misses
attempts that convert). Kneel-downs aren't identified separately: a kneel with a big lead counts
as a 0-point drive, which lowers gas.

A game's adjustment weights each team's tendencies by how many drives it should spend
leading big, from the market spread (lead_drives):

    total  += SCALE * (N_home * (gas_home + prevent_home + fight_away)
                       + N_away * (gas_away + prevent_away + fight_home))
    margin += SCALE * (N_home * (gas_home - prevent_home - fight_away)
                       - N_away * (gas_away - prevent_away - fight_home))

Backtest: scripts/game_script_backtest.py (results in BACKTEST.md).
"""

import math
from collections import defaultdict

LEAD = 14  # points
PRIOR_DRIVES = 12
# Expected 2nd-half drives a team starts up LEAD+ given the spread in its favor:
# LEAD_MAX * Phi((spread - LEAD_MID) / LEAD_SD). Fit on 2023-2025 drives
# (scripts/game_script_backtest.py).
LEAD_MAX = 6.0
LEAD_MID = 14.0
LEAD_SD = 12.0
# Share of the raw tendency that holds up out of sample (fit leave-one-season-out).
SCALE = 1.0
TOTALS_SCALE = SCALE
SPREAD_SCALE = SCALE


def drive_points(d):
    """Points the offense scored on a drive (0 when unknown)."""
    s, e = d.get("startOffenseScore"), d.get("endOffenseScore")
    if s is not None and e is not None:
        return min(max(e - s, 0), 8)
    res = (d.get("driveResult") or "").upper()
    if "TD" in res and "INT" not in res and "FUMBLE" not in res:
        return 7
    if res == "FG":
        return 3
    return 0


def situation(d):
    """'lead' (offense up LEAD+ in the 2nd half), 'trail' (down LEAD+ in the 2nd half),
    'close' (score within LEAD, any time) or None."""
    so, sd = d.get("startOffenseScore"), d.get("startDefenseScore")
    if so is None or sd is None:
        return None
    diff = so - sd
    if abs(diff) < LEAD:
        return "close"
    if (d.get("startPeriod") or 0) < 3:
        return None
    return "lead" if diff > 0 else "trail"


def tendencies(drives, prior=PRIOR_DRIVES):
    """{team: {"gas", "prevent", "fight", "hurry", "downs_rate", "lead_drives", "prevent_drives",
    "trail_drives"}} from drive data."""
    pts = defaultdict(float)
    n = defaultdict(int)
    secs, plays, downs = defaultdict(float), defaultdict(float), defaultdict(int)
    for d in drives:
        sit = situation(d)
        if sit is None or (d.get("plays") or 0) <= 0 and drive_points(d) == 0:
            continue
        p = drive_points(d)
        o, df = d.get("offense"), d.get("defense")
        if sit == "close":
            for key in ((o, "off_close"), (df, "def_close")):
                pts[key] += p
                n[key] += 1
        elif sit == "lead":
            pts[(o, "off_lead")] += p
            n[(o, "off_lead")] += 1
        else:  # offense trails big: the defense's team is the one leading
            pts[(df, "def_lead")] += p
            n[(df, "def_lead")] += 1
            pts[(o, "off_trail")] += p
            n[(o, "off_trail")] += 1
            if "DOWNS" in (d.get("driveResult") or "").upper():
                downs[o] += 1
        if sit in ("close", "trail"):
            t = _secs(d.get("elapsed"))
            if t and 0 < t <= 900 and d.get("plays"):
                secs[(o, sit)] += t
                plays[(o, sit)] += d["plays"]

    def rate(t, k):
        return pts[(t, k)] / n[(t, k)] if n[(t, k)] else None

    teams = {t for t, _ in n}
    # League differences, weighted by lead drives so they match the team terms on average.
    raw = {"gas": {}, "prevent": {}, "fight": {}}
    for t in teams:
        for name, lead_k, close_k in (("gas", "off_lead", "off_close"), ("prevent", "def_lead", "def_close"),
                                      ("fight", "off_trail", "off_close")):
            a, b = rate(t, lead_k), rate(t, close_k)
            if a is not None and b is not None:
                raw[name][t] = (a - b, n[(t, lead_k)])
    lg = {}
    for name, vals in raw.items():
        w = sum(k for _, k in vals.values())
        lg[name] = sum(v * k for v, k in vals.values()) / w if w else 0.0
    # Seconds per play trailing big minus close games (negative = hurries up), league-relative.
    hurry = {t: (secs[(t, "trail")] / plays[(t, "trail")] - secs[(t, "close")] / plays[(t, "close")],
                 plays[(t, "trail")]) for t in teams if plays[(t, "trail")] and plays[(t, "close")]}
    hw = sum(k for _, k in hurry.values())
    lg_hurry = sum(v * k for v, k in hurry.values()) / hw if hw else 0.0
    n_trail = sum(n[(t, "off_trail")] for t in teams)
    lg_downs = sum(downs.values()) / n_trail if n_trail else 0.0
    out = {}
    for t in teams:
        nt = n[(t, "off_trail")]
        row = {"lead_drives": n[(t, "off_lead")], "prevent_drives": n[(t, "def_lead")], "trail_drives": nt}
        for name in ("gas", "prevent", "fight"):
            v, k = raw[name].get(t, (lg[name], 0))
            row[name] = (v - lg[name]) * k / (k + prior) if k else 0.0
        v, k = hurry.get(t, (lg_hurry, 0))
        k /= 6  # plays -> about drives, so the shrinkage matches the other terms
        row["hurry"] = (v - lg_hurry) * k / (k + prior) if k else 0.0
        row["downs_rate"] = (downs[t] + lg_downs * prior) / (nt + prior) if nt + prior else lg_downs
        out[t] = row
    return out


def _secs(t):
    if not isinstance(t, dict):
        return None
    return (t.get("minutes") or 0) * 60 + (t.get("seconds") or 0)


def lead_drives(spread_for):
    """Expected 2nd-half drives a team starts leading by LEAD+, given the points it's favored by."""
    return LEAD_MAX * 0.5 * (1 + math.erf((spread_for - LEAD_MID) / (LEAD_SD * math.sqrt(2))))


def adjustments(tend, home, away, home_favored_by, total_scale=TOTALS_SCALE, spread_scale=SPREAD_SCALE):
    """(points added to the total, points added to the home margin) for one game.

    home_favored_by: points the home team is favored by (market spread, -7 home line -> 7)."""
    h, a = tend.get(home, {}), tend.get(away, {})
    nh, na = lead_drives(home_favored_by), lead_drives(-home_favored_by)
    gh, ph, fh = h.get("gas", 0.0), h.get("prevent", 0.0), h.get("fight", 0.0)
    ga, pa, fa = a.get("gas", 0.0), a.get("prevent", 0.0), a.get("fight", 0.0)
    total = nh * (gh + ph + fa) + na * (ga + pa + fh)
    margin = nh * (gh - ph - fa) - na * (ga - pa - fh)
    return total_scale * total, spread_scale * margin
