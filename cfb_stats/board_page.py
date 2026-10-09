"""Write the week's totals board as a sortable, filterable web page.

  python -m cfb_stats.board_page --week 6        # data/<year>/board_week6.html

Reads what the weekly run already wrote (totals_week<N>.csv, injuries_week<N>.csv,
card_log.csv), so it makes no API calls. The page is self-contained: the board is embedded as
JSON, and kickoff times are compared with the viewer's clock to mark games already started.
"""

import argparse
import csv
import datetime
import json
import os
import zoneinfo

from . import tracking
from .collect import default_year
from .weekly import card_picks, key_injuries

ET = zoneinfo.ZoneInfo("America/New_York")


def _f(r, k):
    v = r.get(k)
    return None if v in (None, "") else float(v)


def game_data(rows, injuries, n_card=3):
    card = {r["game_id"]: side for r, side in card_picks(rows, n_card)}
    out = []
    for r in rows:
        kick = datetime.datetime.fromisoformat(r["start"].replace("Z", "+00:00"))
        best_over, best_under = _f(r, "best_over"), _f(r, "best_under")
        out.append({
            "id": r["game_id"], "kick": kick.isoformat(),
            "kickLabel": kick.astimezone(ET).strftime("%a %-I:%M %p"),
            "away": r["away"], "home": r["home"], "neutral": r["neutral"] == "True",
            "line": _f(r, "market_total"), "open": _f(r, "market_open"), "books": r.get("books") or "",
            "bestOver": best_over, "bestOverBook": r.get("best_over_book") or "",
            "bestUnder": best_under, "bestUnderBook": r.get("best_under_book") or "",
            "model": _f(r, "proj_total"), "awayPts": _f(r, "proj_away"), "homePts": _f(r, "proj_home"),
            "edge": _f(r, "edge"), "pick": r["pick"], "card": card.get(r["game_id"]),
            "enough": r.get("enough_data") == "True",
            "sp": _f(r, "sp_total"), "spMargin": _f(r, "sp_margin"),
            "injury": _f(r, "injury_adj") or 0.0, "weather": _f(r, "weather_adj") or 0.0,
            "matchup": _f(r, "matchup_adj") or 0.0,
            "wind": _f(r, "wind_mph"), "gust": _f(r, "gust_mph"), "rain": _f(r, "precip_in"),
            "temp": _f(r, "temp_f"), "dome": r.get("dome") == "True",
            "playsAway": _f(r, "exp_plays_away"), "playsHome": _f(r, "exp_plays_home"),
            "spread": _f(r, "market_spread"), "spreadOpen": _f(r, "market_spread_open"),
            "margin": _f(r, "proj_margin"),
            "keyInjuries": {t: key_injuries(injuries, t) for t in (r["away"], r["home"]) if key_injuries(injuries, t)},
        })
    return out


def render(games, week, year, generated, record):
    data = json.dumps({"games": games, "week": week, "year": year, "generated": generated, "record": record},
                      separators=(",", ":")).replace("</", "<\\/")
    return TEMPLATE.replace("__DATA__", data).replace("__WEEK__", str(week)).replace("__YEAR__", str(year))


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--year", type=int, default=default_year())
    p.add_argument("--week", type=int, required=True)
    p.add_argument("--out", default="data")
    args = p.parse_args(argv)
    d = os.path.join(args.out, str(args.year))
    board_path = os.path.join(d, f"totals_week{args.week}.csv")
    with open(board_path, newline="") as f:
        rows = list(csv.DictReader(f))
    inj_path = os.path.join(d, f"injuries_week{args.week}.csv")
    injuries = list(csv.DictReader(open(inj_path, newline=""))) if os.path.exists(inj_path) else []
    generated = datetime.datetime.fromtimestamp(os.path.getmtime(board_path), datetime.timezone.utc)
    record = tracking.summary(tracking.read_log(tracking.log_path(d)))
    html = render(game_data(rows, injuries), args.week, args.year,
                  generated.astimezone(ET).strftime("%a %b %-d, %-I:%M %p ET"), record)
    path = os.path.join(d, f"board_week{args.week}.html")
    with open(path, "w") as f:
        f.write(html)
    print(f"wrote {path} ({len(rows)} games)")


TEMPLATE = r"""<title>Totals Board · Week __WEEK__</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Barlow+Condensed:wght@600;700&family=IBM+Plex+Mono:wght@400;500&family=Source+Sans+3:wght@400;600&display=swap">
<style>
/* Layout: a sportsbook-style sheet. Masthead with the week's numbers, filter rail, one dense
   sortable table (rows expand for detail), method notes at the foot. */
:root {
  --bg: #f3f5f1; --surface: #ffffff; --ink: #18221d; --muted: #5b6a61; --line: #d6ddd7;
  --accent: #1e4d34; --over: #b9440f; --under: #0b6f86; --warn: #9a6a00;
  --over-soft: #f8e6dc; --under-soft: #dcf0f4; --chip: #e9eee9;
  --display: "Barlow Condensed", "Arial Narrow", sans-serif;
  --body: "Source Sans 3", "Segoe UI", system-ui, sans-serif;
  --mono: "IBM Plex Mono", ui-monospace, "SFMono-Regular", Menlo, monospace;
}
@media (prefers-color-scheme: dark) { :root:not([data-theme="light"]) {
  --bg: #0e1411; --surface: #151d19; --ink: #e4ebe6; --muted: #93a39a; --line: #27332d;
  --accent: #86c49d; --over: #f39a5f; --under: #4cc3dc; --warn: #e0b54a;
  --over-soft: #3a2418; --under-soft: #12303a; --chip: #1e2924; color-scheme: dark } }
:root[data-theme="dark"] {
  --bg: #0e1411; --surface: #151d19; --ink: #e4ebe6; --muted: #93a39a; --line: #27332d;
  --accent: #86c49d; --over: #f39a5f; --under: #4cc3dc; --warn: #e0b54a;
  --over-soft: #3a2418; --under-soft: #12303a; --chip: #1e2924; color-scheme: dark }
* { box-sizing: border-box }
body { background: var(--bg); color: var(--ink); font: 15px/1.45 var(--body); margin: 0 }
.wrap { max-width: 1240px; margin: 0 auto; padding-inline: 16px; padding-block: 20px 40px; display: grid; gap: 18px }
header { display: grid; gap: 10px }
.eyebrow { font: 600 12px/1 var(--body); letter-spacing: .12em; text-transform: uppercase; color: var(--accent) }
h1 { font: 700 clamp(30px, 6vw, 46px)/1 var(--display); letter-spacing: .01em; margin: 0; text-wrap: balance }
.meta { color: var(--muted); font-size: 14px; max-width: 70ch }
.stats { display: flex; flex-wrap: wrap; gap: 8px 22px; font-family: var(--mono); font-size: 13px; color: var(--muted) }
.stats b { color: var(--ink); font-weight: 500 }
.controls { display: flex; flex-wrap: wrap; gap: 10px; align-items: center }
.seg { display: flex; flex-wrap: wrap; gap: 6px }
.seg button { font: 600 13px/1 var(--body); padding: 8px 12px; border-radius: 999px; border: 1px solid var(--line);
  background: var(--surface); color: var(--ink); cursor: pointer }
.seg button[aria-pressed="true"] { background: var(--accent); border-color: var(--accent); color: var(--bg) }
.seg button:focus-visible, input:focus-visible, th button:focus-visible, tr.game:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px }
#q { font: 14px var(--body); padding: 8px 12px; border-radius: 8px; border: 1px solid var(--line); background: var(--surface);
  color: var(--ink); min-width: 0; flex: 1 1 180px; max-width: 280px }
.tablebox { overflow-x: auto; background: var(--surface); border: 1px solid var(--line); border-radius: 10px }
table { border-collapse: collapse; width: 100%; min-width: 960px; font-variant-numeric: tabular-nums }
th, td { padding: 9px 10px; text-align: right; white-space: nowrap; border-bottom: 1px solid var(--line) }
th { font: 600 11px/1.2 var(--body); letter-spacing: .08em; text-transform: uppercase; color: var(--muted);
  position: sticky; top: 0; background: var(--surface); z-index: 1 }
th button { all: unset; cursor: pointer }
th button[data-dir]::after { content: attr(data-dir); margin-left: 4px; color: var(--accent) }
th:first-child, td:first-child { text-align: left; position: sticky; left: 0; background: var(--surface); z-index: 2 }
td { font-family: var(--mono); font-size: 13px }
td.game { font-family: var(--body); font-size: 14px; white-space: normal; min-width: 210px }
td.game .teams { font-weight: 600 }
td.game .kick { color: var(--muted); font-size: 12px; display: flex; gap: 6px; flex-wrap: wrap; align-items: center }
tr.game { cursor: pointer }
tr.game:hover td { background: color-mix(in srgb, var(--chip) 60%, var(--surface)) }
tr.game.started td { opacity: .55 }
tr.game.card td:first-child { box-shadow: inset 3px 0 0 var(--side) }
.pill { display: inline-block; font: 600 11px/1 var(--body); letter-spacing: .06em; text-transform: uppercase;
  padding: 4px 7px; border-radius: 4px; background: var(--chip); color: var(--muted) }
.pill.over { background: var(--over-soft); color: var(--over) }
.pill.under { background: var(--under-soft); color: var(--under) }
.pill.live { background: transparent; border: 1px solid var(--line) }
.edge { display: inline-grid; grid-template-columns: 52px 46px; align-items: center; gap: 8px; justify-content: end }
.bar { position: relative; height: 8px; background: var(--chip); border-radius: 2px }
.bar::before { content: ""; position: absolute; left: 50%; top: -2px; bottom: -2px; width: 1px; background: var(--muted) }
.bar i { position: absolute; top: 0; bottom: 0; border-radius: 2px }
.o { color: var(--over) } .u { color: var(--under) } .dim { color: var(--muted) }
.agree { font-family: var(--body); font-size: 11px; margin-left: 6px }
tr.detail td { text-align: left; white-space: normal; background: color-mix(in srgb, var(--chip) 45%, var(--surface)); font-family: var(--body); font-size: 14px }
tr.detail td:first-child { position: static }
.dgrid { display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 14px 24px; padding-block: 6px }
.dgrid h3 { font: 600 11px/1 var(--body); letter-spacing: .1em; text-transform: uppercase; color: var(--muted); margin: 0 0 6px }
.dgrid p, .dgrid ul { margin: 0; min-width: 0 }
.dgrid ul { padding-left: 18px }
.num { font-family: var(--mono); font-size: 13px }
.empty { padding: 28px; text-align: center; color: var(--muted); font-family: var(--body) }
footer { color: var(--muted); font-size: 13px; display: grid; gap: 6px; max-width: 80ch }
footer p { margin: 0 }
@media (prefers-reduced-motion: no-preference) { tr.game td { transition: background .12s } }
</style>

<div class="wrap">
  <header>
    <div class="eyebrow">College football totals · __YEAR__</div>
    <h1>Week __WEEK__ board</h1>
    <p class="meta" id="meta"></p>
    <div class="stats" id="stats"></div>
  </header>

  <div class="controls">
    <div class="seg" role="group" aria-label="Show">
      <button id="f-all" data-f="all" aria-pressed="true">All games</button>
      <button id="f-card" data-f="card" aria-pressed="false">Card picks</button>
      <button id="f-edge" data-f="edge" aria-pressed="false">Edge 3+</button>
      <button id="f-upcoming" data-f="upcoming" aria-pressed="false">Not started</button>
    </div>
    <input id="q" type="search" placeholder="Find a team" aria-label="Find a team">
  </div>

  <div class="tablebox">
    <table>
      <thead><tr>
        <th><button data-k="kick">Game</button></th>
        <th><button data-k="line">Line</button></th>
        <th><button data-k="model">Model</button></th>
        <th><button data-k="absEdge" data-dir="▼">Edge</button></th>
        <th><button data-k="sp">SP+</button></th>
        <th><button data-k="injury">Injuries</button></th>
        <th><button data-k="weather">Wind</button></th>
        <th><button data-k="spread">Spread</button></th>
        <th><button data-k="margin">Model margin</button></th>
      </tr></thead>
      <tbody id="rows"></tbody>
    </table>
  </div>

  <footer>
    <p id="record"></p>
    <p><b>Edge</b> is the model total minus the median line across books. The model lists a lean at 3+ points; the card is the 3 biggest over and 3 biggest under edges among Power 4 games (backtest 2023-2025: 114-88, 56.4%). Rows marked with a colored stripe are card picks.</p>
    <p><b>SP+</b> is what Bill Connelly's SP+ ratings imply, with the same wind correction as the model. Reference only: it isn't used for picks. ✓ means SP+ is on the same side of the line as the model.</p>
    <p><b>Spread</b> is the home team's line (−10 = home favored by 10); <b>model margin</b> is the projected home winning margin. The model has shown no edge against the spread, so spreads are for reference. Games that have kicked off are dimmed, using your clock. Tap a game for projected score, pace, best lines and key injuries.</p>
  </footer>
</div>

<script>
const DATA = __DATA__;
const $ = s => document.querySelector(s);
const fmt = (v, d = 1) => v == null ? "–" : Number(v).toFixed(d);
const sgn = (v, d = 1) => v == null ? "–" : (v > 0 ? "+" : v < 0 ? "−" : "") + Math.abs(v).toFixed(d);
const line = v => v == null ? "–" : String(v);
const pm = v => v == null ? "–" : (v > 0 ? "+" : v < 0 ? "−" : "") + Math.abs(v);
const state = { f: "all", q: "", k: "absEdge", dir: -1, open: new Set() };
const games = DATA.games.map(g => ({ ...g, absEdge: Math.abs(g.edge), started: Date.now() >= Date.parse(g.kick) }));

$("#meta").textContent = `Lines, forecasts and injury reports as of ${DATA.generated}. Projected totals for every Power 4 game with a posted total.`;
const leans = games.filter(g => g.enough && g.absEdge >= 3).length;
const cardN = games.filter(g => g.card).length;
$("#stats").innerHTML = `<span><b>${games.length}</b> games</span><span><b>${cardN}</b> card picks</span><span><b>${leans}</b> with edge 3+</span><span><b>${games.filter(g => !g.started).length}</b> not started</span>`;
$("#record").textContent = (DATA.record || []).join(" ");

function edgeCell(g) {
  const max = 12, w = Math.min(Math.abs(g.edge), max) / max * 50;
  const col = g.edge > 0 ? "var(--over)" : "var(--under)";
  const pos = g.edge > 0 ? `left:50%;width:${w}%` : `right:50%;width:${w}%`;
  return `<span class="edge"><span class="bar"><i style="${pos};background:${col}"></i></span><span class="${g.edge > 0 ? "o" : "u"}">${sgn(g.edge)}</span></span>`;
}
function spCell(g) {
  if (g.sp == null) return `<span class="dim">–</span>`;
  const agree = (g.sp - g.line > 0) === (g.edge > 0);
  return `${fmt(g.sp)}<span class="agree ${agree ? "" : "dim"}" title="${agree ? "SP+ agrees with the model's side" : "SP+ is on the other side of the line"}">${agree ? "✓" : "✗"}</span>`;
}
function kickCell(g) {
  const tags = [];
  if (g.card) tags.push(`<span class="pill ${g.card === "OVER" ? "over" : "under"}">Card ${g.card.toLowerCase()}</span>`);
  if (g.started) tags.push(`<span class="pill live">Started</span>`);
  if (!g.enough) tags.push(`<span class="pill">Thin data</span>`);
  return `<div class="teams">${g.away} ${g.neutral ? "vs" : "@"} ${g.home}</div><div class="kick">${g.kickLabel} ET ${tags.join("")}</div>`;
}
function lineCell(g) {
  const moved = g.open != null && g.open !== g.line ? `<div class="dim" style="font-size:11px">opened ${line(g.open)}</div>` : "";
  return `${line(g.line)}${moved}`;
}
function detail(g) {
  const wx = g.dome ? "Dome" : g.wind == null ? "No forecast" :
    `Wind ${fmt(g.wind, 0)} mph${g.gust ? ` (gusts ${fmt(g.gust, 0)})` : ""}${g.rain ? `, rain ${fmt(g.rain, 2)} in` : ""}${g.temp != null ? `, ${fmt(g.temp, 0)}°F` : ""}`;
  const best = [];
  if (g.bestOver != null) best.push(`Over: ${line(g.bestOver)}${g.bestOverBook ? ` (${g.bestOverBook})` : ""}`);
  if (g.bestUnder != null) best.push(`Under: ${line(g.bestUnder)}${g.bestUnderBook ? ` (${g.bestUnderBook})` : ""}`);
  const inj = Object.entries(g.keyInjuries || {}).map(([t, ps]) => `<li><b>${t}:</b> ${ps.join("; ")}</li>`).join("");
  const adj = [`injuries ${sgn(g.injury)}`, `run/pass matchup ${sgn(g.matchup)}`, `wind ${sgn(g.weather)}`].join(", ");
  return `<div class="dgrid">
    <div><h3>Projected score</h3><p class="num">${g.away} ${fmt(g.awayPts)} · ${g.home} ${fmt(g.homePts)}</p><p class="dim" style="font-size:13px">Adjustments in the total: ${adj}</p></div>
    <div><h3>Pace</h3><p class="num">${g.away} ${fmt(g.playsAway, 0)} plays · ${g.home} ${fmt(g.playsHome, 0)} plays</p></div>
    <div><h3>Weather</h3><p>${wx}</p></div>
    <div><h3>Best lines</h3><p class="num">${best.join(" · ") || "–"}</p><p class="dim" style="font-size:13px">Books: ${g.books || "–"}</p></div>
    <div><h3>Spread</h3><p class="num">Line ${pm(g.spread)}${g.spreadOpen != null && g.spreadOpen !== g.spread ? ` (opened ${pm(g.spreadOpen)})` : ""} · model ${sgn(g.margin)} · SP+ ${g.spMargin == null ? "–" : sgn(g.spMargin)}</p></div>
    <div><h3>Key injuries</h3>${inj ? `<ul>${inj}</ul>` : `<p class="dim">None listed (QBs and 4-5 star recruits out, doubtful or game-time)</p>`}</div>
  </div>`;
}
function render() {
  let list = games.filter(g =>
    (state.f === "all") || (state.f === "card" && g.card) || (state.f === "edge" && g.enough && g.absEdge >= 3) ||
    (state.f === "upcoming" && !g.started));
  const q = state.q.trim().toLowerCase();
  if (q) list = list.filter(g => (g.away + " " + g.home).toLowerCase().includes(q));
  const key = state.k;
  list.sort((a, b) => {
    const va = key === "kick" ? Date.parse(a.kick) : a[key], vb = key === "kick" ? Date.parse(b.kick) : b[key];
    if (va == null) return 1; if (vb == null) return -1;
    return (va - vb) * state.dir || Date.parse(a.kick) - Date.parse(b.kick);
  });
  if (!list.length) { $("#rows").innerHTML = `<tr><td colspan="9" class="empty">No games match. Clear the search or pick another filter.</td></tr>`; return; }
  $("#rows").innerHTML = list.map(g => {
    const cls = ["game", g.card ? "card" : "", g.started ? "started" : ""].join(" ");
    const side = g.card === "OVER" ? "var(--over)" : "var(--under)";
    const row = `<tr class="${cls}" data-id="${g.id}" tabindex="0" aria-expanded="${state.open.has(g.id)}" style="--side:${side}">
      <td class="game">${kickCell(g)}</td><td>${lineCell(g)}</td><td>${fmt(g.model)}</td><td>${edgeCell(g)}</td>
      <td>${spCell(g)}</td><td>${g.injury ? sgn(g.injury) : '<span class="dim">–</span>'}</td>
      <td>${g.dome ? '<span class="dim">dome</span>' : g.weather ? sgn(g.weather) : '<span class="dim">–</span>'}</td>
      <td>${pm(g.spread)}</td><td>${sgn(g.margin)}</td></tr>`;
    return row + (state.open.has(g.id) ? `<tr class="detail"><td colspan="9">${detail(g)}</td></tr>` : "");
  }).join("");
}
document.querySelectorAll(".seg button").forEach(b => b.addEventListener("click", () => {
  state.f = b.dataset.f;
  document.querySelectorAll(".seg button").forEach(x => x.setAttribute("aria-pressed", String(x === b)));
  try { localStorage.setItem("board-filter", state.f); } catch (e) {}
  render();
}));
document.querySelectorAll("th button").forEach(b => b.addEventListener("click", () => {
  const k = b.dataset.k;
  state.dir = state.k === k ? -state.dir : (k === "kick" ? 1 : -1);
  state.k = k;
  document.querySelectorAll("th button").forEach(x => x.removeAttribute("data-dir"));
  b.setAttribute("data-dir", state.dir > 0 ? "▲" : "▼");
  render();
}));
$("#q").addEventListener("input", e => { state.q = e.target.value; render(); });
function toggle(tr) { const id = tr.dataset.id; state.open.has(id) ? state.open.delete(id) : state.open.add(id); render(); }
$("#rows").addEventListener("click", e => { const tr = e.target.closest("tr.game"); if (tr) toggle(tr); });
$("#rows").addEventListener("keydown", e => {
  const tr = e.target.closest("tr.game");
  if (tr && (e.key === "Enter" || e.key === " ")) { e.preventDefault(); toggle(tr); document.querySelector(`tr.game[data-id="${tr.dataset.id}"]`)?.focus(); }
});
try { const f = localStorage.getItem("board-filter"); if (f) document.querySelector(`.seg button[data-f="${f}"]`)?.click(); } catch (e) {}
render();
</script>
"""


if __name__ == "__main__":
    main()
