#!/usr/bin/env python3
"""Import WC2026 match events (goals/assists) from Sofascore.

Must be run LOCALLY — Sofascore blocks VPS IPs (Cloudflare).
Writes directly to production DB via DATABASE_URL.

If the DB is not accessible from your machine, open an SSH tunnel first:
    ssh -L 5432:db:5432 root@213.130.144.204 -N &
    DATABASE_URL=postgresql://ev0:eqv2pWEYjMchXWAVVouiAb4nD2uKBug@localhost:5432/ev0 python ...

Usage:
    python scripts/import_sofascore_wc2026_events.py --discover       # list WC seasons on Sofascore
    python scripts/import_sofascore_wc2026_events.py --dry-run        # preview without DB writes
    python scripts/import_sofascore_wc2026_events.py                   # full import
    python scripts/import_sofascore_wc2026_events.py --reset           # clear stats and reimport all

Tables (created automatically):
    wc2026_synced_matches  — Sofascore event IDs already processed (idempotency)
    wc2026_live_stats      — per-player tournament goals/assists totals
"""

import argparse
import json
import os
import time
from pathlib import Path
from typing import Optional

import psycopg2
import requests

# FIFA World Cup tournament ID on Sofascore (stable across editions).
# Run --discover to confirm the WC2026 season_id before first use.
TOURNAMENT_ID = 16
SEASON_ID_DEFAULT: Optional[int] = None  # auto-detected via --discover if None

BASE_API = "https://api.sofascore.com/api/v1"
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json",
    "Referer": "https://www.sofascore.com/",
}
DELAY = 0.8  # seconds between requests — stay polite

CACHE_DIR = Path(__file__).parent / ".sofascore_cache"

DSN = os.environ.get(
    "DATABASE_URL",
    "postgresql://ev0:eqv2pWEYjMchXWAVVouiAb4nD2uKBug@213.130.144.204:5432/ev0",
)


# --------------------------------------------------------------------------- #
# HTTP + cache                                                                 #
# --------------------------------------------------------------------------- #

def _get(path: str, cache_key: Optional[str] = None) -> dict:
    if cache_key:
        cache_file = CACHE_DIR / f"{cache_key}.json"
        if cache_file.exists():
            with open(cache_file) as f:
                return json.load(f)

    url = f"{BASE_API}{path}"
    r = requests.get(url, headers=HEADERS, timeout=15)
    r.raise_for_status()
    data = r.json()
    time.sleep(DELAY)

    if cache_key:
        CACHE_DIR.mkdir(exist_ok=True)
        with open(CACHE_DIR / f"{cache_key}.json", "w") as f:
            json.dump(data, f, ensure_ascii=False)

    return data


# --------------------------------------------------------------------------- #
# Sofascore API                                                                #
# --------------------------------------------------------------------------- #

def fetch_seasons() -> list[dict]:
    data = _get(f"/tournament/{TOURNAMENT_ID}/seasons")
    return data.get("seasons", [])


def fetch_events_page(season_id: int, page: int) -> list[dict]:
    # Sofascore paginates finished matches with /events/last/{page}
    data = _get(
        f"/tournament/{TOURNAMENT_ID}/season/{season_id}/events/last/{page}",
        cache_key=f"events_{season_id}_p{page}",
    )
    return data.get("events", [])


def fetch_all_finished_events(season_id: int) -> list[dict]:
    events: list[dict] = []
    page = 0
    while True:
        batch = fetch_events_page(season_id, page)
        if not batch:
            break
        finished = [e for e in batch if e.get("status", {}).get("type") == "finished"]
        events.extend(finished)
        # Sofascore returns ≤10 events per page; fewer means last page
        if len(batch) < 10:
            break
        page += 1
    return events


def fetch_incidents(event_id: int) -> list[dict]:
    # Finished matches are immutable — cache indefinitely
    data = _get(f"/event/{event_id}/incidents", cache_key=f"incidents_{event_id}")
    return data.get("incidents", [])


# --------------------------------------------------------------------------- #
# Parsing                                                                      #
# --------------------------------------------------------------------------- #

def parse_goals_assists(event: dict, incidents: list[dict]) -> list[dict]:
    """Extract one row per goal/assist from a match's incidents."""
    home = event.get("homeTeam", {}).get("name", "")
    away = event.get("awayTeam", {}).get("name", "")
    rows: list[dict] = []

    for inc in incidents:
        if inc.get("incidentType") not in ("goal", "addedGoal"):
            continue

        scorer = inc.get("player") or {}
        scorer_name = scorer.get("name", "")
        if not scorer_name:
            continue

        team = home if inc.get("isHome") else away
        minute = inc.get("time", 0)

        rows.append({
            "event_id":    event["id"],
            "player_name": scorer_name,
            "team":        team,
            "kind":        "goal",
            "minute":      minute,
        })

        assist = inc.get("assist1") or {}
        assist_name = assist.get("name", "")
        if assist_name:
            rows.append({
                "event_id":    event["id"],
                "player_name": assist_name,
                "team":        team,
                "kind":        "assist",
                "minute":      minute,
            })

    return rows


# --------------------------------------------------------------------------- #
# DB                                                                           #
# --------------------------------------------------------------------------- #

DDL = """
CREATE TABLE IF NOT EXISTS wc2026_synced_matches (
    event_id   INTEGER     PRIMARY KEY,
    home_team  TEXT        NOT NULL DEFAULT '',
    away_team  TEXT        NOT NULL DEFAULT '',
    synced_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS wc2026_live_stats (
    player_name  TEXT        NOT NULL,
    team         TEXT        NOT NULL,
    goals        INTEGER     NOT NULL DEFAULT 0,
    assists      INTEGER     NOT NULL DEFAULT 0,
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (player_name, team)
);
"""


def ensure_tables(cur) -> None:
    for stmt in DDL.strip().split(";"):
        stmt = stmt.strip()
        if stmt:
            cur.execute(stmt)


def get_synced_ids(cur) -> set[int]:
    cur.execute("SELECT event_id FROM wc2026_synced_matches")
    return {r[0] for r in cur.fetchall()}


def upsert_rows(cur, rows: list[dict]) -> None:
    for row in rows:
        goals   = 1 if row["kind"] == "goal"   else 0
        assists = 1 if row["kind"] == "assist" else 0
        cur.execute(
            """
            INSERT INTO wc2026_live_stats (player_name, team, goals, assists, updated_at)
            VALUES (%s, %s, %s, %s, NOW())
            ON CONFLICT (player_name, team) DO UPDATE SET
                goals      = wc2026_live_stats.goals      + EXCLUDED.goals,
                assists    = wc2026_live_stats.assists    + EXCLUDED.assists,
                updated_at = NOW()
            """,
            (row["player_name"], row["team"], goals, assists),
        )


def mark_synced(cur, event: dict) -> None:
    cur.execute(
        """
        INSERT INTO wc2026_synced_matches (event_id, home_team, away_team)
        VALUES (%s, %s, %s) ON CONFLICT DO NOTHING
        """,
        (
            event["id"],
            event.get("homeTeam", {}).get("name", ""),
            event.get("awayTeam", {}).get("name", ""),
        ),
    )


def print_top_scorers(cur, n: int = 15) -> None:
    cur.execute(
        """
        SELECT player_name, team, goals, assists
        FROM wc2026_live_stats
        ORDER BY goals DESC, assists DESC
        LIMIT %s
        """,
        (n,),
    )
    print(f"\n{'Top buteurs':=<50}")
    for name, team, g, a in cur.fetchall():
        print(f"  {name:<32} {team:<20}  {g}g  {a}a")


# --------------------------------------------------------------------------- #
# Main                                                                         #
# --------------------------------------------------------------------------- #

def resolve_season_id(args_season: Optional[int]) -> Optional[int]:
    if args_season:
        return args_season
    seasons = fetch_seasons()
    for s in seasons:
        if "2026" in str(s.get("year", "")):
            print(f"CDM 2026 détectée : season_id={s['id']}  ({s.get('name', '')})")
            return s["id"]
    return None


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--discover", action="store_true", help="Lister les saisons FIFA World Cup")
    parser.add_argument("--dry-run",  action="store_true", help="Afficher sans écrire en DB")
    parser.add_argument("--season",   type=int, default=None, help="Forcer le season_id Sofascore")
    parser.add_argument("--reset",    action="store_true", help="Vider les stats et réimporter tout")
    args = parser.parse_args()

    if args.discover:
        seasons = fetch_seasons()
        print("Saisons disponibles — FIFA World Cup (tournament_id=16) :")
        for s in seasons:
            print(f"  id={s['id']:>6}  year={str(s.get('year','?')):<10}  {s.get('name','')}")
        return

    season_id = resolve_season_id(args.season)
    if season_id is None:
        print(
            "Saison CDM 2026 introuvable sur Sofascore.\n"
            "Lance --discover pour voir les saisons disponibles, puis relance avec --season <id>."
        )
        return

    print(f"Récupération des matchs (season_id={season_id}) …")
    all_events = fetch_all_finished_events(season_id)
    print(f"{len(all_events)} match(s) terminé(s) trouvé(s)")

    conn = psycopg2.connect(DSN)
    cur = conn.cursor()
    ensure_tables(cur)
    conn.commit()

    if args.reset and not args.dry_run:
        cur.execute("DELETE FROM wc2026_live_stats")
        cur.execute("DELETE FROM wc2026_synced_matches")
        conn.commit()
        print("Tables réinitialisées.")
        synced: set[int] = set()
        new_events = all_events
    else:
        synced = get_synced_ids(cur)
        new_events = [e for e in all_events if e["id"] not in synced]

    print(f"{len(new_events)} nouveau(x) match(s) à synchroniser\n")

    total_goals = total_assists = 0

    for event in new_events:
        home = event.get("homeTeam", {}).get("name", "?")
        away = event.get("awayTeam", {}).get("name", "?")
        eid  = event["id"]

        incidents = fetch_incidents(eid)
        rows = parse_goals_assists(event, incidents)

        match_goals   = sum(1 for r in rows if r["kind"] == "goal")
        match_assists = sum(1 for r in rows if r["kind"] == "assist")
        total_goals   += match_goals
        total_assists += match_assists

        score_home = event.get("homeScore", {}).get("current", "?")
        score_away = event.get("awayScore", {}).get("current", "?")
        print(f"  {home} {score_home}-{score_away} {away}  ({match_goals}g {match_assists}a)")
        for r in rows:
            sym = "⚽" if r["kind"] == "goal" else "🅰️ "
            print(f"      {sym} {r['player_name']} ({r['team']}) {r['minute']}'")

        if not args.dry_run:
            upsert_rows(cur, rows)
            mark_synced(cur, event)

    if not args.dry_run:
        conn.commit()

    print(f"\n{'='*60}")
    print(f"Matchs traités : {len(new_events)}  |  Buts : {total_goals}  Passes : {total_assists}")

    if not args.dry_run and new_events:
        print_top_scorers(cur)

    cur.close()
    conn.close()


if __name__ == "__main__":
    main()
