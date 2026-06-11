#!/usr/bin/env python3
"""Import national team expected minutes into wc2026_expected_lineup_players.

Source: national-football-teams.com
Columns scraped: M (total matches), S (as substitute), G (goals)
Formula: avg_mins = ((M - S) × 80 + S × 25) / M, capped at 90.

Usage:
    python scripts/import_transfermarkt_national_minutes.py [--dry-run] [--nation NATION]

Examples:
    python scripts/import_transfermarkt_national_minutes.py --dry-run
    python scripts/import_transfermarkt_national_minutes.py --nation Espagne
    python scripts/import_transfermarkt_national_minutes.py          # all nations
"""

import argparse
import json
import os
import re
import sys
import time
import unicodedata
from pathlib import Path
from typing import Optional

import psycopg2
import requests
from bs4 import BeautifulSoup
from urllib.parse import quote

DSN = os.environ.get(
    "DATABASE_URL",
    "postgresql://ev0:eqv2pWEYjMchXWAVVouiAb4nD2uKBug@213.130.144.204:5432/ev0",
)
BASE = "https://www.national-football-teams.com"
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}
DELAY = 1.5
CACHE_FILE = Path(__file__).parent / ".nft_cache.json"


# --------------------------------------------------------------------------- #
# Helpers                                                                       #
# --------------------------------------------------------------------------- #

def _norm(name: str) -> str:
    n = unicodedata.normalize("NFKD", name.lower().strip())
    return "".join(c for c in n if not unicodedata.combining(c))


def _load_cache() -> dict:
    if CACHE_FILE.exists():
        with open(CACHE_FILE) as f:
            return json.load(f)
    return {}


def _save_cache(cache: dict) -> None:
    with open(CACHE_FILE, "w") as f:
        json.dump(cache, f, ensure_ascii=False, indent=2)


# --------------------------------------------------------------------------- #
# Scraping                                                                      #
# --------------------------------------------------------------------------- #

def search_player(name: str) -> Optional[str]:
    """Return player page path (e.g. /player/67592.html) or None."""
    url = f"{BASE}/search.html?primarySearchType=0&ajax=false&term={quote(name)}"
    try:
        r = requests.get(url, headers=HEADERS, timeout=12)
        r.raise_for_status()
    except Exception as e:
        print(f"    [search error] {name}: {e}")
        return None

    soup = BeautifulSoup(r.text, "html.parser")
    norm_name = _norm(name)

    # Pick the result whose normalised name best matches
    best_href = None
    best_score = -1
    for a in soup.select("a[href^='/player/']"):
        candidate = _norm(a.get_text(strip=True))
        # count matching tokens
        name_tokens = set(norm_name.split())
        cand_tokens = set(candidate.split(",")[0].split())  # "Mbappé,Kylian" → "mbappe"
        cand_tokens |= set(candidate.replace(",", " ").split())
        score = len(name_tokens & cand_tokens)
        if score > best_score:
            best_score = score
            best_href = a["href"]

    if best_score == 0:
        return None
    return best_href


def get_national_stats(player_path: str) -> Optional[dict]:
    """
    Scrape player page and return:
        { matches, sub_apps, avg_mins }
    from the FIFA career totals row.
    """
    url = BASE + player_path
    try:
        r = requests.get(url, headers=HEADERS, timeout=12)
        r.raise_for_status()
    except Exception as e:
        print(f"    [stats error] {player_path}: {e}")
        return None

    soup = BeautifulSoup(r.text, "html.parser")
    table = soup.find("table", class_="countries")
    if table is None:
        return None

    # tfoot contains the career totals; columns: M | S | G (FIFA) | M | S | G (Non-FIFA)
    tfoot = table.find("tfoot")
    if tfoot is None:
        return None

    cells = [td.get_text(strip=True) for td in tfoot.find_all("td")]
    nums = []
    for c in cells:
        try:
            nums.append(int(c))
        except ValueError:
            pass

    if len(nums) < 2:
        return None

    # FIFA totals: nums[0]=M, nums[1]=S, nums[2]=G (when present)
    matches = nums[0]
    sub_apps = nums[1] if len(nums) > 1 else 0

    if matches == 0:
        return None

    starts = max(0, matches - sub_apps)
    avg_mins = round(min(90.0, (starts * 80 + sub_apps * 25) / matches), 1)
    return {"matches": matches, "sub_apps": sub_apps, "avg_mins": avg_mins}


# --------------------------------------------------------------------------- #
# DB helpers                                                                    #
# --------------------------------------------------------------------------- #

def load_lineup_players(cur, nation_filter: Optional[str]) -> list[dict]:
    sql = """
        SELECT lp.id, lp.player_name, l.nation
        FROM wc2026_expected_lineup_players lp
        JOIN wc2026_expected_lineups l ON lp.lineup_id = l.id
        WHERE l.context = 'default'
    """
    params: tuple = ()
    if nation_filter:
        sql += " AND l.nation = %s"
        params = (nation_filter,)
    sql += " ORDER BY l.nation, lp.player_name"
    cur.execute(sql, params)
    return [{"id": r[0], "player_name": r[1], "nation": r[2]} for r in cur.fetchall()]


def update_minutes(cur, lineup_id: int, mins: float) -> None:
    cur.execute(
        "UPDATE wc2026_expected_lineup_players SET expected_minutes = %s WHERE id = %s",
        (mins, lineup_id),
    )


# --------------------------------------------------------------------------- #
# Main                                                                          #
# --------------------------------------------------------------------------- #

def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--nation", default=None, help="Nom de nation tel qu'en DB (ex: Espagne)")
    args = parser.parse_args()

    cache = _load_cache()

    conn = psycopg2.connect(DSN)
    cur = conn.cursor()

    players = load_lineup_players(cur, args.nation)
    print(f"{len(players)} joueurs à traiter{' (dry-run)' if args.dry_run else ''}\n")

    updated = 0
    not_found: list[str] = []
    prev_nation = None

    for p in players:
        if p["nation"] != prev_nation:
            print(f"=== {p['nation']} ===")
            prev_nation = p["nation"]

        name = p["player_name"]
        cache_key = _norm(name)

        if cache_key in cache:
            result = cache[cache_key]
            source = "cache"
        else:
            source = "web"
            path = search_player(name)
            time.sleep(DELAY)
            if path is None:
                print(f"  ✗ not found:  {name}")
                not_found.append(name)
                cache[cache_key] = None
                _save_cache(cache)
                continue

            result = get_national_stats(path)
            time.sleep(DELAY)
            cache[cache_key] = result
            _save_cache(cache)

        if result is None:
            print(f"  ✗ no stats:   {name}")
            not_found.append(name)
            continue

        avg = result["avg_mins"]
        print(
            f"  ✓ {name:<32} {result['matches']:>3} caps  "
            f"{result['sub_apps']:>2} sub  →  {avg:.0f} min  [{source}]"
        )

        if not args.dry_run:
            update_minutes(cur, p["id"], avg)
            updated += 1

    if not args.dry_run:
        conn.commit()

    print(f"\n{'='*60}")
    print(f"Mis à jour : {updated}/{len(players)}")
    if not_found:
        print(f"Non trouvés ({len(not_found)}) :")
        for n in not_found[:20]:
            print(f"  - {n}")
        if len(not_found) > 20:
            print(f"  ... et {len(not_found)-20} autres")

    cur.close()
    conn.close()


if __name__ == "__main__":
    main()
