"""High school recruiting ratings (247 composite stars, rating, national rank) for players.

Each recruiting class is downloaded once from CFBD /recruiting/players and saved to
data/recruiting/recruits_<class>.csv; signed classes don't change, so later runs read the
file and spend no API calls. Players are matched by CFBD athlete id (the same id the
injury reports are matched to), falling back to the roster's recruit ids.

Stars are shown with injuries for context only: there's no historical availability data to
calibrate how much a star rating should move a projection.
"""

import csv
import os

FIELDS = ["athleteId", "id", "name", "committedTo", "position", "stars", "rating", "ranking",
          "year", "recruitType"]


def class_path(data_dir, year):
    return os.path.join(data_dir, "recruiting", f"recruits_{year}.csv")


def load_class(client, year, data_dir="data"):
    """Recruits for one class, from the saved CSV or (once) from the API."""
    path = class_path(data_dir, year)
    if os.path.exists(path):
        with open(path, newline="") as f:
            return list(csv.DictReader(f))
    rows = [{k: r.get(k) for k in FIELDS} for r in client.get("/recruiting/players", year=year)]
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(rows)
    return rows


def recruit_index(client, season, data_dir="data", classes_back=5):
    """{"athlete:<id>": recruit, "recruit:<id>": recruit} for classes season-5 .. season."""
    index = {}
    for year in range(season - classes_back, season + 1):
        for r in load_class(client, year, data_dir):
            if r.get("athleteId"):
                index[f"athlete:{r['athleteId']}"] = r
            if r.get("id"):
                index[f"recruit:{r['id']}"] = r
    return index


def lookup(index, player_id, roster_entry=None):
    r = index.get(f"athlete:{player_id}") if player_id else None
    if r is None and roster_entry:
        for rid in roster_entry.get("recruitIds") or []:
            r = index.get(f"recruit:{rid}")
            if r:
                break
    return r


def add_recruiting(rows, index, rosters):
    """Add stars/rating/national_rank/recruit_class to injury rows (in place)."""
    by_id = {str(p.get("id")): p for roster in rosters.values() for p in roster}
    for row in rows:
        pid = str(row.get("player_id") or "")
        r = lookup(index, pid, by_id.get(pid))
        row["stars"] = r.get("stars") if r else ""
        row["rating"] = r.get("rating") if r else ""
        row["national_rank"] = r.get("ranking") if r else ""
        row["recruit_class"] = r.get("year") if r else ""
    return rows


def star_label(row):
    try:
        stars = int(float(row.get("stars") or 0))
    except ValueError:
        stars = 0
    return f"{stars}-star" if stars else "unrated"
