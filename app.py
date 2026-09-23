"""Puckwire: a personal Rotoworld-style fantasy hockey news page.

Reads the JSON files the collector keeps on the repo's `data` branch, using
GITHUB_REPO and GITHUB_TOKEN from st.secrets. With no secrets it reads ./data
so you can preview it locally after running the collector.
"""
from __future__ import annotations

import datetime as dt
import html
import json
import re
import unicodedata
from pathlib import Path
from zoneinfo import ZoneInfo

import streamlit as st
import sys

sys.path.insert(0, str(Path(__file__).parent / "collector"))
import collect  # noqa: E402  (shared fetchers: news and goalies are pulled live)

ET = ZoneInfo("America/Toronto")
LOCAL = Path(__file__).parent / "data"

st.set_page_config(page_title="Puckwire", page_icon="🏒", layout="wide")

# Rink palette: ice surface, boards, the red line, the blue line, the crease.
CATEGORY_COLORS = {
    "Injury": "#C8102E",        # red line
    "Suspension": "#8E0C22",
    "Transaction": "#0B5CAD",   # blue line
    "Goalie start": "#6FA8DC",  # crease
    "Lineup": "#2F7D5B",
    "Recap": "#8A99AB",
    "News": "#8A99AB",
}
STATUS_COLORS = {"Confirmed": "#2F7D5B", "Likely": "#C98A12", "Expected": "#C98A12"}

st.markdown(
    """
<style>
@import url('https://fonts.googleapis.com/css2?family=Barlow+Condensed:wght@500;600;700&family=Public+Sans:wght@400;500;600&display=swap');
:root { --ice:#F3F7FA; --board:#FFFFFF; --navy:#13294B; --muted:#5B6B80; --rule:#DCE4EC; }
html, body, [class*="css"], .stMarkdown, .stText { font-family:'Public Sans', system-ui, sans-serif; }
.stApp { background: var(--ice); }
h1, h2, h3 { font-family:'Barlow Condensed', 'Arial Narrow', sans-serif !important; color:var(--navy); letter-spacing:.01em; }
.masthead { display:flex; align-items:baseline; gap:1rem; border-bottom:4px solid #C8102E; padding-bottom:.35rem; margin-bottom:.75rem; }
.brand { font-family:'Barlow Condensed', 'Arial Narrow', sans-serif; font-weight:700; font-size:3.2rem; color:var(--navy); line-height:1; }
.masthead span { color:var(--muted); font-size:.9rem; }
.note { background:var(--board); border-left:6px solid var(--stripe); border-radius:2px 8px 8px 2px;
        padding:.9rem 1.1rem; margin-bottom:.8rem; display:grid; grid-template-columns:64px 1fr; gap:1rem; }
.note.mine { box-shadow: inset 0 0 0 2px #13294B22; }
.note img { width:64px; height:64px; object-fit:cover; border-radius:50%; background:var(--ice); }
.who { display:flex; flex-wrap:wrap; align-items:baseline; gap:.5rem; }
.who a.name { font-family:'Barlow Condensed', sans-serif; font-weight:700; font-size:1.55rem; color:var(--navy); text-decoration:none; line-height:1.1; }
.who a.name:hover { text-decoration:underline; }
.team { font-weight:600; font-size:.85rem; color:var(--muted); }
.star { color:#C8102E; font-size:.95rem; }
.tag { font-size:.75rem; font-weight:600; color:var(--stripe); border:1px solid var(--stripe); border-radius:999px; padding:0 .5rem; margin-left:auto; }
.headline { font-weight:600; color:var(--navy); margin:.25rem 0 .2rem; font-size:1.02rem; }
.news { color:#1F2D3D; margin:0 0 .35rem; max-width:75ch; line-height:1.5; }
.analysis { color:var(--muted); margin:0 0 .35rem; max-width:75ch; line-height:1.55; font-size:.93rem; }
.take { background:#EAF2FB; border-radius:6px; padding:.45rem .7rem; margin:.3rem 0 .4rem; max-width:75ch; font-size:.93rem; }
.take b { color:var(--navy); }
.meta { font-size:.8rem; color:var(--muted); }
.meta a { color:var(--muted); }
.game { background:var(--board); border-radius:8px; padding:.7rem 1rem; margin-bottom:.6rem; display:grid; grid-template-columns:1fr auto 1fr; gap:.75rem; align-items:center; }
.goalie { display:flex; gap:.6rem; align-items:center; }
.goalie.home { flex-direction:row-reverse; text-align:right; }
.goalie img { width:44px; height:44px; border-radius:50%; object-fit:cover; background:var(--ice); }
.gname { font-family:'Barlow Condensed', sans-serif; font-weight:700; font-size:1.2rem; color:var(--navy); }
.gstatus { font-size:.78rem; font-weight:600; }
.vs, a.vs { color:var(--muted); font-size:.8rem; }
.gstatus a { color:var(--muted); font-weight:400; }
.mine-g .gname { text-decoration: underline 3px #C8102E; text-underline-offset: 4px; }
.unit { background:var(--board); border-radius:8px; padding:.6rem .9rem; margin-bottom:.5rem; }
.unit b { font-family:'Barlow Condensed', sans-serif; font-size:1.05rem; color:var(--navy); }
.unit .p { display:inline-block; margin-right:1.1rem; }
.unit .p.mine { color:#C8102E; font-weight:600; }
.inj { color:#C8102E; font-size:.75rem; font-weight:600; }
@media (max-width: 640px) { .note { grid-template-columns:44px 1fr; } .note img { width:44px; height:44px; } .brand { font-size:2.3rem; } }
</style>
""",
    unsafe_allow_html=True,
)


# ---------------------------------------------------------------- data

def has_secrets() -> bool:
    try:
        return "GITHUB_REPO" in st.secrets
    except Exception:  # no secrets.toml at all: local preview
        return False


@st.cache_data(ttl=120, show_spinner=False)
def load(table: str) -> list[dict]:
    if not has_secrets():
        f = LOCAL / f"{table}.json"
        return json.loads(f.read_text()) if f.exists() else []
    import requests
    repo = st.secrets["GITHUB_REPO"]
    token = st.secrets.get("GITHUB_TOKEN")
    if token:  # private repo
        r = requests.get(f"https://api.github.com/repos/{repo}/contents/{table}.json", params={"ref": "data"},
                         headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github.raw+json"},
                         timeout=20)
    else:  # public repo: no key needed
        r = requests.get(f"https://raw.githubusercontent.com/{repo}/data/{table}.json", timeout=20)
    if r.status_code == 404:  # collector hasn't written this file yet
        return []
    r.raise_for_status()
    return r.json()


@st.cache_data(ttl=3600, show_spinner=False)
def nhl_index() -> dict[str, dict]:
    return {p["name_key"]: p for p in load("nhl_players")}


@st.cache_data(ttl=300, show_spinner="Pulling the latest notes...")
def live_news() -> tuple[list[dict], str]:
    """Fetched straight from RotoWire, CBS and Daily Faceoff when you open the page,
    then cached for 5 minutes. Keeps only the last 3 days."""
    cutoff = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=3)).isoformat()
    items = [i for i in collect.fetch_all_news(nhl_index()) if i["published_at"] >= cutoff]
    return items, dt.datetime.now(dt.timezone.utc).isoformat()


@st.cache_data(ttl=300, show_spinner=False)
def live_goalies() -> list[dict]:
    try:
        return collect.fetch_goalies()
    except Exception:
        return []


def player_history(name_key: str) -> list[dict]:
    return [r for r in live_news()[0] if r["name_key"] == name_key]


def ago(ts: str) -> str:
    t = dt.datetime.fromisoformat(ts.replace("Z", "+00:00"))
    mins = int((dt.datetime.now(dt.timezone.utc) - t).total_seconds() // 60)
    if mins < 60:
        return f"{max(mins, 1)}m ago"
    if mins < 60 * 24:
        return f"{mins // 60}h ago"
    return t.astimezone(ET).strftime("%b %-d, %-I:%M %p")


def norm(name: str) -> str:
    """Must match collector.norm so roster names line up with news and lines."""
    s = unicodedata.normalize("NFKD", name or "").encode("ascii", "ignore").decode().lower()
    s = re.sub(r"[^a-z ]", "", s.replace("-", " ").replace(".", ""))
    return re.sub(r"\s+", " ", s).strip()


def e(s) -> str:
    return html.escape(str(s or ""))


def card(n: dict, mine: set[str]) -> str:
    is_mine = n["name_key"] in mine
    color = CATEGORY_COLORS.get(n.get("category"), "#8A99AB")
    img = n.get("headshot") or "https://assets.nhle.com/mugs/nhl/default-skater.png"
    team = " ".join(x for x in (n.get("team"), n.get("position")) if x)
    parts = [
        f'<div class="note{" mine" if is_mine else ""}" style="--stripe:{color}">',
        f'<img src="{e(img)}" alt="" loading="lazy">',
        "<div>",
        '<div class="who">',
        f'<a class="name" href="?player={e(n["name_key"])}" target="_self">{e(n["player_name"])}</a>',
        f'<span class="team">{e(team)}</span>',
        '<span class="star" title="On your roster">★</span>' if is_mine else "",
        f'<span class="tag">{e(n.get("category"))}</span>',
        "</div>",
        f'<p class="headline">{e(n["headline"])}</p>',
        f'<p class="news">{e(n.get("news"))}</p>',
    ]
    if n.get("analysis"):
        parts.append(f'<p class="analysis">{e(n["analysis"])}</p>')
    if n.get("ai_take"):
        parts.append(f'<div class="take"><b>For your team:</b> {e(n["ai_take"])}</div>')
    parts.append(
        f'<div class="meta">{ago(n["published_at"])}  |  <a href="{e(n.get("url"))}" target="_blank">{e(n.get("source"))}</a></div>'
    )
    parts.append("</div></div>")
    return "".join(parts)


# ---------------------------------------------------------------- page

news, fetched_at = live_news()
roster = load("my_roster")
mine = {r["name_key"] for r in roster}

latest = ago(news[0]["published_at"]) if news else "none yet"
head_l, head_r = st.columns([5, 1])
head_l.markdown(
    f'<div class="masthead"><div class="brand">Puckwire</div><span>Checked {ago(fetched_at)}'
    f'  |  newest note {latest}</span></div>',
    unsafe_allow_html=True,
)
if head_r.button("Refresh", use_container_width=True):
    live_news.clear()
    live_goalies.clear()
    st.rerun()

player_key = st.query_params.get("player")
if player_key:
    hist = player_history(player_key)
    name = hist[0]["player_name"] if hist else player_key
    if st.button("Back to all news"):
        st.query_params.clear()
        st.rerun()
    st.subheader(f"{name}: last few days")
    if not hist:
        st.info("No notes for this player yet.")
    st.markdown("".join(card(n, mine) for n in hist), unsafe_allow_html=True)
    st.stop()

with st.sidebar:
    st.header("Filter")
    only_mine = st.toggle("Only my players", value=False, disabled=not mine,
                          help="Syncs from your ESPN roster every hour.")
    cats = st.multiselect("Type", list(CATEGORY_COLORS), placeholder="All types")
    teams = sorted({n["team"] for n in news if n.get("team")})
    team_pick = st.multiselect("Team", teams, placeholder="All teams")
    pos_pick = st.multiselect("Position", ["C", "L", "R", "D", "G"], placeholder="All positions")
    query = st.text_input("Search players or text")
    st.caption("Notes via RotoWire, CBS Sports and Daily Faceoff, pulled live. Goalies and lines via Daily Faceoff. For personal use.")

tab_news, tab_goalies, tab_lines, tab_roster = st.tabs(["News", "Starting goalies", "Lines", "My roster"])

with tab_news:
    rows = news
    if only_mine:
        rows = [n for n in rows if n["name_key"] in mine]
    if cats:
        rows = [n for n in rows if n.get("category") in cats]
    if team_pick:
        rows = [n for n in rows if n.get("team") in team_pick]
    if pos_pick:
        rows = [n for n in rows if (n.get("position") or "")[:1] in pos_pick]
    if query:
        ql = query.lower()
        rows = [n for n in rows if ql in f'{n["player_name"]} {n["headline"]} {n.get("news", "")}'.lower()]
    if not rows:
        st.info("Nothing matches these filters. Clear a filter or widen the search.")
    shown = st.session_state.get("shown", 40)
    st.markdown("".join(card(n, mine) for n in rows[:shown]), unsafe_allow_html=True)
    if len(rows) > shown and st.button("Load more notes"):
        st.session_state["shown"] = shown + 40
        st.rerun()

with tab_goalies:
    today_iso = dt.datetime.now(ET).date().isoformat()
    goalies = [g for g in live_goalies() if g["game_date"] >= today_iso]
    dates = sorted({g["game_date"] for g in goalies})
    if not dates:
        st.info("No starters posted yet. Daily Faceoff fills these in on game days, usually by the morning skate.")
    else:
        today = dt.datetime.now(ET).date().isoformat()
        day = st.radio("Day", dates, index=dates.index(today) if today in dates else 0, horizontal=True,
                       format_func=lambda d: "Today" if d == today else dt.date.fromisoformat(d).strftime("%a %b %-d"))
        games: dict[tuple, dict] = {}
        for g in (g for g in goalies if g["game_date"] == day):
            key = tuple(sorted((g["team_name"], g["opponent"])))
            games.setdefault(key, {})["home" if g["is_home"] else "away"] = g

        def side(g: dict | None, where: str) -> str:
            if not g:
                return f'<div class="goalie {where}"><span class="vs">Not posted</span></div>'
            color = STATUS_COLORS.get(g["status"], "#8A99AB")
            m = " mine-g" if norm(g["goalie_name"]) in mine else ""
            src = f' <a class="vs" href="{e(g["source_url"])}" target="_blank">{e(g["source_name"])}</a>' if g.get("source_url") else ""
            return (f'<div class="goalie {where}{m}"><img src="{e(g.get("headshot"))}" alt="">'
                    f'<div><div class="gname">{e(g["goalie_name"])}</div>'
                    f'<div class="gstatus" style="color:{color}">{e(g["status"])}{src}</div>'
                    f'<div class="vs">{e(g["team_name"])}</div></div></div>')

        st.markdown("".join(
            f'<div class="game">{side(v.get("away"), "away")}<div class="vs">at</div>{side(v.get("home"), "home")}</div>'
            for v in games.values()), unsafe_allow_html=True)

with tab_lines:
    lines = {l["team_abbrev"]: l for l in load("line_combos")}
    if not lines:
        st.info("Line combinations load hourly.")
    else:
        my_teams = sorted({n["team"] for n in news if n["name_key"] in mine and n.get("team")} | {"TOR"})
        default = my_teams[0] if my_teams and my_teams[0] in lines else sorted(lines)[0]
        team = st.selectbox("Team", sorted(lines), index=sorted(lines).index(default))
        t = lines[team]
        st.caption(f'Updated {ago(t["updated_at"]) if t.get("updated_at") else "unknown"} from {t.get("source_name") or "Daily Faceoff"}')
        groups: dict[str, list] = {}
        for p in t["players"]:
            groups.setdefault(p["group_name"], []).append(p)
        order = ["Forwards 1", "Forwards 2", "Forwards 3", "Forwards 4", "Defense 1", "Defense 2", "Defense 3",
                 "1st Powerplay Unit", "2nd Powerplay Unit", "Goalies", "Injured Reserve"]
        for gname in [g for g in order if g in groups]:
            ps = "".join(
                f'<span class="p{" mine" if norm(p["name"]) in mine else ""}">{e(p["name"])}'
                f'{f" <span class=inj>{e(p["injury"]).upper()}</span>" if p.get("injury") else ""}</span>'
                for p in groups[gname])
            st.markdown(f'<div class="unit"><b>{e(gname)}</b><br>{ps}</div>', unsafe_allow_html=True)

with tab_roster:
    if not roster:
        st.info("Add ESPN_LEAGUE_ID and ESPN_TEAM_ID to the collector's secrets to sync your roster.")
    else:
        latest_by_player = {}
        for n in news:
            latest_by_player.setdefault(n["name_key"], n)
        cards = []
        for r in sorted(roster, key=lambda r: r["name"]):
            n = latest_by_player.get(r["name_key"])
            status = (r.get("injury_status") or "").replace("_", " ").title()
            flag = f' <span class="inj">{e(status)}</span>' if status and status != "Active" else ""
            note = f'{e(n["headline"])} <span class="meta">{ago(n["published_at"])}</span>' if n else '<span class="meta">No recent notes</span>'
            cards.append(
                f'<div class="unit"><a class="gname" href="?player={e(r["name_key"])}" target="_self">{e(r["name"])}</a> '
                f'<span class="team">{e(r.get("position"))} {e(r.get("pro_team"))}</span>{flag}<br>{note}</div>')
        st.markdown("".join(cards), unsafe_allow_html=True)
