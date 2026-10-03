"""Pull conference availability (injury) reports into an injuries CSV for cfb_stats.totals.

The Big Ten's availability page (bigten.org/sports/2026/9/10/FB_Availability_Reports.aspx)
embeds a report app from app.hdintelligence.com. This reads the same public feed the page
uses, keeps every player not listed as Available (and not Exempt, which marks players the
conference doesn't require teams to report), and matches each one to a CFBD athlete id by
team + jersey number, falling back to name.

Usage:
  python -m cfb_stats.availability                 # Big Ten, writes data/<year>/injuries_week<N>.csv
  python -m cfb_stats.totals --injuries data/2026/injuries_week5.csv
"""

import argparse
import csv
import os
import re
import unicodedata

import requests

from .collect import default_year, upcoming_week

FEED_URL = "https://app.hdintelligence.com"
SKIP_STATUSES = {"available", "exempt"}
DEFENSE_POSITIONS = {"DL", "DE", "DT", "NT", "EDG", "EDGE", "LB", "ILB", "OLB", "MLB",
                     "DB", "CB", "S", "SS", "FS", "NB", "STAR", "SPUR", "JACK"}
SPECIAL_POSITIONS = {"K", "P", "LS", "PK"}
NAME_RE = re.compile(r"^(?P<pos>[A-Z/]+)\s+(?:#(?P<num>\d+)\s+)?(?P<name>.+)$")


def fetch_reports(conf="B10", sport="Football", session=None):
    s = session or requests.Session()
    headers = {"Content-Type": "application/json", "Origin": "https://bigten.org", "Referer": "https://bigten.org/"}
    s.post(f"{FEED_URL}/api/public-load", headers=headers, timeout=30, json={
        "conference_param": conf, "sport_param": sport, "type_param": "report",
        "referrer": "https://bigten.org/", "source": conf})
    r = s.post(f"{FEED_URL}/api/get-publish-public", headers=headers, timeout=60,
               json={"sport": sport, "organization": conf, "conference": conf})
    r.raise_for_status()
    return list(r.json().values())


def parse_entry(raw):
    """'WR #0 Chase Sowell' -> ('WR', 0, 'Chase Sowell'); 'S CJ Christian' -> ('S', None, ...)."""
    m = NAME_RE.match(raw.strip())
    if not m:
        return None, None, raw.strip()
    num = m.group("num")
    return m.group("pos"), int(num) if num else None, m.group("name").strip()


def side_for(position):
    pos = (position or "").upper()
    if pos in DEFENSE_POSITIONS:
        return "def"
    if pos in SPECIAL_POSITIONS:
        return "st"
    return "off"


def _norm(name):
    name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    name = re.sub(r"[\"“”].*?[\"“”]", " ", name)  # nicknames: Anthony "Turbo" Rogers
    name = re.sub(r"\b(jr|sr|ii|iii|iv|v)\b\.?", " ", name.lower())
    return re.sub(r"[^a-z]", "", name)


def match_player(team, number, name, rosters):
    """CFBD athlete id for a report entry, by jersey (and name check), else by name."""
    roster = rosters.get(team) or []
    target = _norm(name)
    by_num = [p for p in roster if number is not None and p.get("jersey") == number]
    full = lambda p: _norm(f"{p.get('firstName', '')} {p.get('lastName', '')}")  # noqa: E731
    for p in by_num:
        if full(p) == target or _norm(p.get("lastName") or "") in target:
            return p["id"]
    for p in roster:
        if full(p) == target:
            return p["id"]
    # Jersey match alone (names differ in spelling) when it's the only player wearing it.
    return by_num[0]["id"] if len(by_num) == 1 else None


def entries(reports, rosters, teams_filter=None):
    """Report rows -> injury rows, one per non-available player."""
    out = []
    for rep in reports:
        label = f"{rep.get('ReportType')} {rep.get('publishDate')} {rep.get('postedTime')}"
        for team_block in rep.get("games") or []:
            team = team_block.get("teamDisplayName") or team_block.get("teamName")
            if teams_filter and team not in teams_filter:
                continue
            for row in team_block.get("rows") or []:
                status = (row.get("status") or "").strip()
                if status.lower() in SKIP_STATUSES or (row.get("exemptStatus") or "").lower() == "exempt":
                    continue
                pos, num, name = parse_entry(row.get("name") or "")
                side = side_for(pos)
                if side == "st":
                    continue  # kickers/punters don't move EPA/play
                out.append({
                    "team": team, "player": name, "status": status, "side": side, "epa_delta": "",
                    "player_id": match_player(team, num, name, rosters) or "",
                    "position": pos, "jersey": num if num is not None else "", "report": label,
                })
    return out


def main(argv=None):
    from .api import CFBDClient

    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--conf", default="B10", help="conference code in the report feed (B10)")
    p.add_argument("--year", type=int, default=default_year())
    p.add_argument("--week", type=int, default=None, help="week number for the output file name")
    p.add_argument("--out", default="data")
    p.add_argument("--api-key", default=None)
    args = p.parse_args(argv)

    client = CFBDClient(api_key=args.api_key)
    reports = fetch_reports(args.conf)
    teams = sorted({b.get("teamDisplayName") for r in reports for b in r.get("games") or []})
    rosters = {t: client.get("/roster", year=args.year, team=t) for t in teams}
    rows = entries(reports, rosters)

    week = args.week
    if week is None:
        games = client.games(args.year, "regular")
        week = upcoming_week(games)
    path = os.path.join(args.out, str(args.year), f"injuries_week{week}.csv")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["team", "player", "status", "side", "epa_delta",
                                          "player_id", "position", "jersey", "report"])
        w.writeheader()
        w.writerows(rows)
    unmatched = [r for r in rows if not r["player_id"]]
    print(f"{len(reports)} reports, {len(teams)} teams -> {len(rows)} players not available, wrote {path}")
    for r in reports:
        names = " vs ".join(b.get("teamDisplayName") for b in r.get("games") or [])
        print(f"  {names}: {r.get('ReportType')} posted {r.get('publishDate')} {r.get('postedTime')}")
    if unmatched:
        print(f"{len(unmatched)} not matched to a CFBD player id (matched by name later if possible):")
        for r in unmatched:
            print(f"  {r['team']} {r['position']} #{r['jersey']} {r['player']}")


if __name__ == "__main__":
    main()
