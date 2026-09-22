"""Puckwire collector.

Pulls fantasy hockey player news, starting goalies, line combos, the NHL
player index and your ESPN roster, and writes them as JSON files to DATA_DIR
(default ./data). In GitHub Actions that folder becomes the repo's `data`
branch, replaced with a single fresh commit every run so history never grows.

    python collector/collect.py news goalies          # every run
    python collector/collect.py lines espn            # hourly
    python collector/collect.py all                   # everything (first run)

Old data is purged every run: news older than NEWS_RETENTION_DAYS (default 3)
and goalie starts from past days.

Env vars: DATA_DIR, NEWS_RETENTION_DAYS, ESPN_LEAGUE_ID, ESPN_TEAM_ID,
ESPN_S2 + ESPN_SWID (private leagues only), ESPN_SEASON (default 2027),
ANTHROPIC_API_KEY (optional, enables AI takes), AI_TAKES_PER_DAY (default 10).
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import html
import json
import os
import re
import sys
import unicodedata
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

UA = {"User-Agent": "Mozilla/5.0 (Puckwire personal fantasy feed)"}
ET = ZoneInfo("America/Toronto")
PT = ZoneInfo("America/Los_Angeles")
UTC = dt.timezone.utc
ROOT = Path(__file__).resolve().parents[1]
DATA = Path(os.environ.get("DATA_DIR") or ROOT / "data")

ROTOWIRE_RSS = "https://www.rotowire.com/rss/news.php?sport=NHL"
CBS_PAGE_1 = "https://www.cbssports.com/fantasy/hockey/players/news/all/"
CBS_PAGE_N = "https://www.cbssports.com/fantasy/hockey/players/news/all/both/content/xhr/?page={n}"
DF_GOALIES = "https://www.dailyfaceoff.com/starting-goalies/{date}"
DF_LINES = "https://www.dailyfaceoff.com/teams/{slug}/line-combinations"
NHL_STANDINGS = "https://api-web.nhle.com/v1/standings/now"
NHL_ROSTER = "https://api-web.nhle.com/v1/roster/{team}/current"

NHL_TEAM_NAMES = {  # nickname -> abbrev, for CBS headlines like "Red Wings' Jacob Bryson"
    "ducks": "ANA", "bruins": "BOS", "sabres": "BUF", "flames": "CGY", "hurricanes": "CAR",
    "blackhawks": "CHI", "avalanche": "COL", "blue jackets": "CBJ", "stars": "DAL",
    "red wings": "DET", "oilers": "EDM", "panthers": "FLA", "kings": "LAK", "wild": "MIN",
    "canadiens": "MTL", "predators": "NSH", "devils": "NJD", "islanders": "NYI",
    "rangers": "NYR", "senators": "OTT", "flyers": "PHI", "penguins": "PIT", "sharks": "SJS",
    "kraken": "SEA", "blues": "STL", "lightning": "TBL", "maple leafs": "TOR",
    "mammoth": "UTA", "canucks": "VAN", "golden knights": "VGK", "capitals": "WSH", "jets": "WPG",
}


# ---------------------------------------------------------------- helpers

def norm(name: str) -> str:
    """Accent-, case- and punctuation-insensitive name key."""
    s = unicodedata.normalize("NFKD", name or "").encode("ascii", "ignore").decode().lower()
    s = re.sub(r"[^a-z ]", "", s.replace("-", " ").replace(".", ""))
    return re.sub(r"\s+", " ", s).strip()


def item_id(player: str, headline: str) -> str:
    """Same note from RotoWire RSS and CBS collapses to one row."""
    return hashlib.sha1(f"{norm(player)}|{norm(headline)}".encode()).hexdigest()[:16]


def get(url: str, **kw) -> requests.Response:
    r = requests.get(url, headers=UA, timeout=20, allow_redirects=True, **kw)
    r.raise_for_status()
    return r


def strip_tags(s: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", s))).strip()


def now_utc() -> dt.datetime:
    return dt.datetime.now(UTC)


def next_data(page_html: str) -> dict:
    m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', page_html, re.S)
    return json.loads(m.group(1))["props"]["pageProps"] if m else {}


CATEGORY_RULES = [
    ("Injury", r"\b(injur|out for|day-to-day|week-to-week|upper[- ]body|lower[- ]body|surgery|IR\b|LTIR|concussion|sidelined|non-contact|illness|questionable|doubtful|return(s|ed)? to (practice|the lineup))"),
    ("Transaction", r"\b(waivers|waiver wire|signed|signs|contract|traded|trade|acquired|recalled|loaned|assigned|claimed|released|activated|extension|PTO|buyout|sent down|reassigned)\b"),
    ("Recap", r"\b(scored|halted|stopped|turned aside|made \d+ saves|notched|tallied|recorded|picked up|posted|registered|dished)\b.*\b(win|loss|victory|defeat|game|contest)\b"),
    ("Goalie start", r"\b(start|starts|starting|the nod|crease|net|blue paint|between the pipes|in goal)\b"),  # goalies only
    ("Lineup", r"\b(line|pairing|power play|PP1|PP2|top six|bottom six|healthy scratch|scratched|lineup|centering)\b"),
]


def categorize(text: str, position: str | None = None) -> str:
    for label, pat in CATEGORY_RULES:
        if label == "Goalie start" and (position or "").upper() != "G":
            continue
        if re.search(pat, text, re.I):
            return label
    return "News"


# ---------------------------------------------------------------- sources

def fetch_rotowire() -> list[dict]:
    xml = get(ROTOWIRE_RSS).text
    items = []
    for block in re.findall(r"<item>(.*?)</item>", xml, re.S):
        def tag(t):
            m = re.search(rf"<{t}>(.*?)</{t}>", block, re.S)
            return html.unescape(m.group(1).strip()) if m else ""
        title, desc = tag("title"), tag("description")
        if ":" not in title:
            continue
        player, headline = [p.strip() for p in title.split(":", 1)]
        desc = re.split(r"\n\s*Visit RotoWire\.com", desc)[0].strip()
        published = None
        raw = re.sub(r"\s+(PDT|PST|EDT|EST)$", "", tag("pubDate"))
        for fmt in ("%a, %d %b %Y %I:%M:%S %p", "%a, %d %b %Y %H:%M:%S"):
            try:
                published = dt.datetime.strptime(raw, fmt).replace(tzinfo=PT).astimezone(UTC)
                break
            except ValueError:
                pass
        items.append({
            "id": item_id(player, headline),
            "player_name": player,
            "headline": headline,
            "news": desc,
            "analysis": "",
            "source": "RotoWire",
            "url": tag("link").replace(".com//", ".com/"),
            "published_at": (published or now_utc()).isoformat(),
            "time_exact": published is not None,
        })
    return items


def _parse_cbs(fragment: str) -> list[dict]:
    items = []
    for li in re.split(r"<li>\s*<div class=\"row\">", fragment)[1:]:
        name_m = re.search(r'players-annotated">\s*<p><a href="([^"]+)">([^<]+)</a>\s*<span>\s*([^<|]*)\|\s*([A-Z]+)', li)
        head_m = re.search(r'<h4><a href="([^"]+)">([^<]+)</a></h4>', li)
        body_m = re.search(r'<div class="latest-updates">(.*?)</div>', li, re.S)
        if not (name_m and head_m and body_m):
            continue
        player = html.unescape(name_m.group(2)).strip()
        full_head = html.unescape(head_m.group(2)).strip()
        headline = full_head.split(":", 1)[1].strip() if ":" in full_head else full_head
        paras = [strip_tags(p) for p in re.findall(r"<p>(.*?)</p>", body_m.group(1), re.S)]
        time_m = re.search(r'<time class="eyebrow">\s*(\d+)\s*([MHD])', li)
        ago = dt.timedelta(0)
        if time_m:
            n, unit = int(time_m.group(1)), time_m.group(2)
            ago = {"M": dt.timedelta(minutes=n), "H": dt.timedelta(hours=n), "D": dt.timedelta(days=n)}[unit]
        img_m = re.search(r'<img src="([^"]+)" class="player-headshot', li)
        items.append({
            "id": item_id(player, headline),
            "player_name": player,
            "headline": headline,
            "news": paras[0] if paras else "",
            "analysis": " ".join(paras[1:]),
            "source": "CBS / RotoWire",
            "url": "https://www.cbssports.com" + head_m.group(1),
            "published_at": (now_utc() - ago).isoformat(),
            "time_exact": False,
            "position": name_m.group(3).strip(),
            "team": name_m.group(4).strip(),
            "headshot_fallback": img_m.group(1) if img_m else None,
        })
    return items


def fetch_cbs(pages: int = 2) -> list[dict]:
    items = _parse_cbs(get(CBS_PAGE_1).text)
    for n in range(2, pages + 1):
        try:
            frag = get(CBS_PAGE_N.format(n=n)).json()["loadMore"]["html"]
            items += _parse_cbs(frag)
        except Exception as e:  # paging is a bonus; page 1 is enough to keep up
            print(f"cbs page {n} skipped: {e}", file=sys.stderr)
    return items


def fetch_goalies() -> list[dict]:
    rows = []
    today = dt.datetime.now(ET).date()
    for day in (today, today + dt.timedelta(days=1)):
        games = next_data(get(DF_GOALIES.format(date=day.isoformat())).text).get("data") or []
        for g in games:
            for side, opp in (("home", "away"), ("away", "home")):
                if not g.get(f"{side}GoalieName"):
                    continue
                rows.append({
                    "id": f"{day.isoformat()}-{g[f'{side}TeamSlug']}",
                    "game_date": day.isoformat(),
                    "team_name": g[f"{side}TeamName"],
                    "opponent": g[f"{opp}TeamName"],
                    "is_home": side == "home",
                    "goalie_name": g[f"{side}GoalieName"],
                    "status": g.get(f"{side}NewsStrengthName") or "Unconfirmed",
                    "details": (g.get(f"{side}NewsDetails") or "").strip(),
                    "source_name": g.get(f"{side}NewsSourceName"),
                    "source_url": g.get(f"{side}NewsSourceUrl"),
                    "headshot": g.get(f"{side}GoalieHeadshotUrl") or g.get(f"{side}GoalieFantasydataFaceUrl"),
                    "updated_at": g.get(f"{side}NewsCreatedAt") or now_utc().isoformat(),
                })
    return rows


def fetch_lines() -> list[dict]:
    first = next_data(get(DF_LINES.format(slug="toronto-maple-leafs")).text)
    slugs = [t["slug"] for t in first.get("sortedTeams", []) if t.get("slug")] or ["toronto-maple-leafs"]
    rows = []
    for slug in slugs:
        try:
            pp = first if slug == "toronto-maple-leafs" else next_data(get(DF_LINES.format(slug=slug)).text)
            c = pp["combinations"]
        except Exception as e:
            print(f"lines {slug} skipped: {e}", file=sys.stderr)
            continue
        players = [{
            "name": p["name"], "pos": p.get("positionIdentifier"), "group": p.get("groupIdentifier"),
            "group_name": p.get("groupName"), "category": p.get("categoryIdentifier"),
            "injury": p.get("injuryStatus"), "gtd": p.get("gameTimeDecision"),
        } for p in c.get("players", [])]
        rows.append({
            "team_abbrev": c.get("teamAbbreviation"), "team_name": c.get("teamName"),
            "source_name": c.get("sourceName"), "source_url": c.get("source"),
            "updated_at": c.get("updatedAt"), "players": players,
        })
    return rows


def fetch_nhl_players() -> list[dict]:
    teams = sorted({t["teamAbbrev"]["default"] for t in get(NHL_STANDINGS).json()["standings"]})
    rows = []
    for team in teams:
        r = get(NHL_ROSTER.format(team=team)).json()
        for group in ("forwards", "defensemen", "goalies"):
            for p in r.get(group, []):
                full = f"{p['firstName']['default']} {p['lastName']['default']}"
                rows.append({
                    "nhl_id": p["id"], "name": full, "name_key": norm(full), "team": team,
                    "position": p.get("positionCode"), "number": p.get("sweaterNumber"),
                    "headshot": p.get("headshot"),
                })
    return rows


def fetch_espn_roster() -> list[dict]:
    from espn_api.hockey import League  # imported lazily: only the hourly job needs it

    league = League(
        league_id=int(os.environ["ESPN_LEAGUE_ID"]),
        year=int(os.environ.get("ESPN_SEASON", "2027")),
        espn_s2=os.environ.get("ESPN_S2") or None,
        swid=os.environ.get("ESPN_SWID") or None,
    )
    team_id = int(os.environ["ESPN_TEAM_ID"])
    team = next(t for t in league.teams if t.team_id == team_id)
    return [{
        "name_key": norm(p.name), "name": p.name, "position": p.position,
        "pro_team": getattr(p, "proTeam", None), "injury_status": getattr(p, "injuryStatus", None),
        "lineup_slot": getattr(p, "lineupSlot", None), "updated_at": now_utc().isoformat(),
    } for p in team.roster]


# ---------------------------------------------------------------- storage

class Store:
    """One JSON file per table in DATA_DIR. Small by design: purged every run."""

    def __init__(self):
        DATA.mkdir(parents=True, exist_ok=True)

    def _path(self, table: str) -> Path:
        return DATA / f"{table}.json"

    def select(self, table: str, col: str | None = None, values: list | None = None) -> list[dict]:
        f = self._path(table)
        rows = json.loads(f.read_text()) if f.exists() else []
        if col is None:
            return rows
        wanted = set(values or [])
        return [r for r in rows if r.get(col) in wanted]

    def write(self, table: str, rows: list[dict]):
        self._path(table).write_text(json.dumps(rows, separators=(",", ":"), default=str))

    def upsert(self, table: str, rows: list[dict], key: str, replace_all: bool = False):
        if not rows and not replace_all:
            return
        merged = {} if replace_all else {r[key]: r for r in self.select(table)}
        merged.update({r[key]: r for r in rows})
        self.write(table, list(merged.values()))

    def prune(self, table: str, keep) -> int:
        rows = self.select(table)
        kept = [r for r in rows if keep(r)]
        self.write(table, kept)
        return len(rows) - len(kept)


# ---------------------------------------------------------------- jobs

def enrich(items: list[dict], store: Store) -> None:
    keys = list({norm(i["player_name"]) for i in items})
    index = {p["name_key"]: p for p in store.select("nhl_players", "name_key", keys)}
    for it in items:
        p = index.get(norm(it["player_name"]))
        it["name_key"] = norm(it["player_name"])
        it["nhl_id"] = p["nhl_id"] if p else None
        it["team"] = (p or {}).get("team") or it.get("team")
        it["position"] = (p or {}).get("position") or it.get("position")
        it["headshot"] = (p or {}).get("headshot") or it.pop("headshot_fallback", None)
        it.pop("headshot_fallback", None)
        it["category"] = categorize(f"{it['headline']} {it['news']}", it.get("position"))


def merge(new: dict, old: dict | None) -> dict:
    """Keep the earliest exact timestamp and the fullest text across sources."""
    if not old:
        return new
    out = {**old, **{k: v for k, v in new.items() if v not in (None, "")}}
    for field in ("news", "analysis"):
        out[field] = max(old.get(field) or "", new.get(field) or "", key=len)
    if old.get("time_exact") and not new.get("time_exact"):
        out["published_at"], out["time_exact"] = old["published_at"], True
    elif not new.get("time_exact"):
        out["published_at"] = min(old["published_at"], new["published_at"])
    if "RotoWire" in (old.get("source") or "") and "CBS" in (new.get("source") or ""):
        out["url"] = new["url"]  # CBS link shows the analysis without a paywall
    out["ai_take"] = old.get("ai_take")
    return out


def job_news(store: Store) -> int:
    fetched = []
    for fn in (fetch_rotowire, fetch_cbs):
        try:
            fetched += fn()
        except Exception as e:
            print(f"{fn.__name__} failed: {e}", file=sys.stderr)
    by_id: dict[str, dict] = {}
    for it in fetched:  # collapse RSS + CBS duplicates within this run
        by_id[it["id"]] = merge(it, by_id.get(it["id"]))
    items = list(by_id.values())
    enrich(items, store)
    existing = {r["id"]: r for r in store.select("news_items", "id", list(by_id))}
    rows = [merge(it, existing.get(it["id"])) for it in items]
    add_ai_takes(rows, existing, store)
    store.upsert("news_items", rows, "id")
    days = float(os.environ.get("NEWS_RETENTION_DAYS", "3"))
    cutoff = (now_utc() - dt.timedelta(days=days)).isoformat()
    purged = store.prune("news_items", lambda r: r["published_at"] >= cutoff)
    store.write("news_items", sorted(store.select("news_items"), key=lambda r: r["published_at"], reverse=True))
    print(f"news: {len(rows)} fetched ({len(rows) - len(existing)} new), {purged} older than {days:g} days purged")
    return len(rows)


def add_ai_takes(rows: list[dict], existing: dict, store: Store) -> None:
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        return
    mine = {r["name_key"] for r in store.select("my_roster")}
    today = dt.datetime.now(ET).date().isoformat()
    used = sum(1 for r in store.select("news_items") if r.get("ai_take") and str(r.get("ai_take_date")) == today)
    budget = int(os.environ.get("AI_TAKES_PER_DAY", "10")) - used
    for r in rows:
        if budget <= 0:
            return
        if r["name_key"] not in mine or r.get("ai_take") or (existing.get(r["id"]) or {}).get("ai_take"):
            continue
        prompt = (
            "You write one-sentence fantasy hockey takes for a manager who rosters this player "
            "in an ESPN league. In your own words (no quoting), under 30 words, say what this "
            "means for starting/sitting or holding the player this week. No preamble.\n\n"
            f"Player: {r['player_name']} ({r.get('position')}, {r.get('team')})\n"
            f"News: {r['headline']}. {r['news']}\nContext: {r.get('analysis', '')}"
        )
        try:
            resp = requests.post(
                "https://api.anthropic.com/v1/messages",
                headers={"x-api-key": key, "anthropic-version": "2023-06-01", "content-type": "application/json"},
                json={"model": "claude-haiku-4-5-20251001", "max_tokens": 120,
                      "messages": [{"role": "user", "content": prompt}]},
                timeout=30,
            )
            resp.raise_for_status()
            r["ai_take"] = "".join(b.get("text", "") for b in resp.json()["content"]).strip()
            r["ai_take_date"] = today
            budget -= 1
        except Exception as e:
            print(f"ai take failed for {r['player_name']}: {e}", file=sys.stderr)


def job_goalies(store: Store):
    rows = fetch_goalies()
    store.upsert("goalie_starts", rows, "id")
    today = dt.datetime.now(ET).date().isoformat()
    purged = store.prune("goalie_starts", lambda r: r["game_date"] >= today)
    print(f"goalies: {len(rows)} starters, {purged} past starts purged")


def job_lines(store: Store):
    rows = fetch_lines()
    store.upsert("line_combos", rows, "team_abbrev", replace_all=bool(rows))
    print(f"lines: {len(rows)} teams")


def job_players(store: Store):
    rows = fetch_nhl_players()
    store.upsert("nhl_players", rows, "nhl_id", replace_all=bool(rows))  # drops players who left the NHL
    print(f"nhl players: {len(rows)}")


def job_espn(store: Store):
    if not os.environ.get("ESPN_LEAGUE_ID"):
        print("espn: skipped (ESPN_LEAGUE_ID not set)")
        return
    rows = fetch_espn_roster()
    store.upsert("my_roster", rows, "name_key", replace_all=True)  # drops players you cut
    print(f"espn roster: {len(rows)} players")


JOBS = {"players": job_players, "espn": job_espn, "lines": job_lines, "goalies": job_goalies, "news": job_news}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("jobs", nargs="+", choices=[*JOBS, "all"])
    args = ap.parse_args()
    store = Store()
    jobs = list(JOBS) if "all" in args.jobs else [j for j in JOBS if j in args.jobs]  # players before news
    failed = False
    for j in jobs:
        try:
            JOBS[j](store)
        except Exception as e:
            failed = True
            print(f"{j} failed: {e}", file=sys.stderr)
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
