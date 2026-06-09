#!/usr/bin/env python3
# backend/scripts/seed_wc2026_fixtures.py
"""Seed WC2026 fixtures into the fixtures table.

Queries nations and group assignments from wc2026_squad_players,
generates all C(4,2) group-stage matchups and 32 KO-round placeholders.

Usage:
    DATABASE_URL=postgresql+psycopg2://... python scripts/seed_wc2026_fixtures.py
    # Or inside backend container:
    python scripts/seed_wc2026_fixtures.py
"""
from __future__ import annotations

import itertools
import os
import re
import sys
import unicodedata
from datetime import datetime, timezone

import psycopg2

# ---------------------------------------------------------------------------
# Approximate kickoff times per (group_letter, round_number)
# Derived from FIFA WC 2026 official schedule. Times are UTC.
# Round 1: Jun 11-15 | Round 2: Jun 15-19 | Round 3: Jun 22-26
# ---------------------------------------------------------------------------
_GROUP_ROUND_KO: dict[tuple[str, int], datetime] = {
    ("A", 1): datetime(2026, 6, 11, 23, 0, tzinfo=timezone.utc),
    ("A", 2): datetime(2026, 6, 15, 23, 0, tzinfo=timezone.utc),
    ("A", 3): datetime(2026, 6, 22, 22, 0, tzinfo=timezone.utc),
    ("B", 1): datetime(2026, 6, 12, 2, 0, tzinfo=timezone.utc),
    ("B", 2): datetime(2026, 6, 16, 2, 0, tzinfo=timezone.utc),
    ("B", 3): datetime(2026, 6, 23, 2, 0, tzinfo=timezone.utc),
    ("C", 1): datetime(2026, 6, 12, 20, 0, tzinfo=timezone.utc),
    ("C", 2): datetime(2026, 6, 16, 20, 0, tzinfo=timezone.utc),
    ("C", 3): datetime(2026, 6, 23, 22, 0, tzinfo=timezone.utc),
    ("D", 1): datetime(2026, 6, 13, 2, 0, tzinfo=timezone.utc),
    ("D", 2): datetime(2026, 6, 17, 2, 0, tzinfo=timezone.utc),
    ("D", 3): datetime(2026, 6, 24, 2, 0, tzinfo=timezone.utc),
    ("E", 1): datetime(2026, 6, 13, 20, 0, tzinfo=timezone.utc),
    ("E", 2): datetime(2026, 6, 17, 20, 0, tzinfo=timezone.utc),
    ("E", 3): datetime(2026, 6, 24, 22, 0, tzinfo=timezone.utc),
    ("F", 1): datetime(2026, 6, 14, 2, 0, tzinfo=timezone.utc),
    ("F", 2): datetime(2026, 6, 18, 2, 0, tzinfo=timezone.utc),
    ("F", 3): datetime(2026, 6, 25, 2, 0, tzinfo=timezone.utc),
    ("G", 1): datetime(2026, 6, 14, 20, 0, tzinfo=timezone.utc),
    ("G", 2): datetime(2026, 6, 18, 20, 0, tzinfo=timezone.utc),
    ("G", 3): datetime(2026, 6, 25, 22, 0, tzinfo=timezone.utc),
    ("H", 1): datetime(2026, 6, 15, 2, 0, tzinfo=timezone.utc),
    ("H", 2): datetime(2026, 6, 19, 2, 0, tzinfo=timezone.utc),
    ("H", 3): datetime(2026, 6, 26, 2, 0, tzinfo=timezone.utc),
    ("I", 1): datetime(2026, 6, 15, 20, 0, tzinfo=timezone.utc),
    ("I", 2): datetime(2026, 6, 19, 20, 0, tzinfo=timezone.utc),
    ("I", 3): datetime(2026, 6, 26, 22, 0, tzinfo=timezone.utc),
    ("J", 1): datetime(2026, 6, 11, 20, 0, tzinfo=timezone.utc),
    ("J", 2): datetime(2026, 6, 15, 20, 0, tzinfo=timezone.utc),
    ("J", 3): datetime(2026, 6, 22, 22, 0, tzinfo=timezone.utc),
    ("K", 1): datetime(2026, 6, 12, 2, 0, tzinfo=timezone.utc),
    ("K", 2): datetime(2026, 6, 16, 2, 0, tzinfo=timezone.utc),
    ("K", 3): datetime(2026, 6, 23, 2, 0, tzinfo=timezone.utc),
    ("L", 1): datetime(2026, 6, 12, 20, 0, tzinfo=timezone.utc),
    ("L", 2): datetime(2026, 6, 16, 20, 0, tzinfo=timezone.utc),
    ("L", 3): datetime(2026, 6, 23, 22, 0, tzinfo=timezone.utc),
}

# KO round placeholder kickoffs
_KO_FIXTURES: list[dict] = [
    # R32: July 1-6 (16 matches)
    *[
        {
            "external_id": f"wc2026_r32_m{i+1}",
            "home_team": f"R32 TBD {2*i+1}",
            "away_team": f"R32 TBD {2*i+2}",
            "kickoff_utc": datetime(2026, 7, 1 + (i // 3), 2 + (i % 2) * 20, 0, tzinfo=timezone.utc),
            "round": "r32",
            "matchweek": None,
        }
        for i in range(16)
    ],
    # R16: July 9-12 (8 matches)
    *[
        {
            "external_id": f"wc2026_r16_m{i+1}",
            "home_team": f"R16 TBD {2*i+1}",
            "away_team": f"R16 TBD {2*i+2}",
            "kickoff_utc": datetime(2026, 7, 9 + (i // 2), 2 + (i % 2) * 20, 0, tzinfo=timezone.utc),
            "round": "r16",
            "matchweek": None,
        }
        for i in range(8)
    ],
    # QF: July 15-16 (4 matches)
    *[
        {
            "external_id": f"wc2026_qf_m{i+1}",
            "home_team": f"QF TBD {2*i+1}",
            "away_team": f"QF TBD {2*i+2}",
            "kickoff_utc": datetime(2026, 7, 15 + i // 2, 2 + (i % 2) * 20, 0, tzinfo=timezone.utc),
            "round": "qf",
            "matchweek": None,
        }
        for i in range(4)
    ],
    # SF: July 19-20 (2 matches)
    {
        "external_id": "wc2026_sf_m1",
        "home_team": "SF TBD 1",
        "away_team": "SF TBD 2",
        "kickoff_utc": datetime(2026, 7, 19, 22, 0, tzinfo=timezone.utc),
        "round": "sf",
        "matchweek": None,
    },
    {
        "external_id": "wc2026_sf_m2",
        "home_team": "SF TBD 3",
        "away_team": "SF TBD 4",
        "kickoff_utc": datetime(2026, 7, 20, 22, 0, tzinfo=timezone.utc),
        "round": "sf",
        "matchweek": None,
    },
    # 3rd place + Final
    {
        "external_id": "wc2026_3rd_place",
        "home_team": "3RD TBD 1",
        "away_team": "3RD TBD 2",
        "kickoff_utc": datetime(2026, 7, 22, 22, 0, tzinfo=timezone.utc),
        "round": "3rd_place",
        "matchweek": None,
    },
    {
        "external_id": "wc2026_final",
        "home_team": "FINAL TBD 1",
        "away_team": "FINAL TBD 2",
        "kickoff_utc": datetime(2026, 7, 23, 21, 0, tzinfo=timezone.utc),
        "round": "final",
        "matchweek": None,
    },
]


def _normalize_ext_id(name: str) -> str:
    """Normalize a team name for use in external_id."""
    n = unicodedata.normalize("NFKD", name.lower().strip())
    n = "".join(c for c in n if not unicodedata.combining(c))
    n = re.sub(r"[^a-z0-9]+", "_", n)
    return n.strip("_")


def _generate_group_pairs(group_letter: str, nations: list[str]) -> list[dict]:
    """Generate C(n,2) pairs for a group. Assigns 2 pairs per round.

    Returns list of dicts with keys: external_id, home_team, away_team,
    round_num, group_letter.
    """
    sorted_nations = sorted(nations)
    all_pairs = list(itertools.combinations(sorted_nations, 2))
    # 6 pairs -> 3 rounds of 2 matches each
    round_map = {0: 1, 1: 1, 2: 2, 3: 2, 4: 3, 5: 3}
    result = []
    for idx, (home, away) in enumerate(all_pairs):
        round_num = round_map.get(idx, 1)
        ext_id = f"wc2026_group_{group_letter.lower()}_{_normalize_ext_id(home)}_vs_{_normalize_ext_id(away)}"
        result.append({
            "external_id": ext_id,
            "home_team": home,
            "away_team": away,
            "round_num": round_num,
            "group_letter": group_letter,
        })
    return result


def main() -> None:
    dsn = os.environ.get(
        "DATABASE_URL",
        "postgresql://ev0:eqv2pWEYjMchXWAVVouiAb4nD2uKBug@localhost:5432/ev0",
    ).replace("postgresql+asyncpg://", "postgresql://").replace("postgresql+psycopg2://", "postgresql://")

    conn = psycopg2.connect(dsn)
    cur = conn.cursor()

    # 1. Get nations + groups from DB
    cur.execute(
        "SELECT DISTINCT nation, group_letter FROM wc2026_squad_players ORDER BY group_letter, nation"
    )
    rows = cur.fetchall()
    if not rows:
        print("ERROR: wc2026_squad_players is empty -- run squad seeder first.")
        sys.exit(1)

    groups: dict[str, list[str]] = {}
    for nation, group in rows:
        groups.setdefault(group, []).append(nation)

    print(f"Found {len(groups)} groups, {len(rows)} distinct nations")

    # 2. Generate group stage fixtures
    group_fixtures = []
    for group_letter, nations in sorted(groups.items()):
        pairs = _generate_group_pairs(group_letter, nations)
        for p in pairs:
            ko = _GROUP_ROUND_KO.get((group_letter, p["round_num"]))
            if ko is None:
                print(f"WARNING: no kickoff time for group {group_letter} round {p['round_num']}, using fallback")
                ko = datetime(2026, 6, 15, 20, 0, tzinfo=timezone.utc)
            group_fixtures.append({
                "external_id": p["external_id"],
                "league": "world_cup_2026",
                "season": "2025-2026",
                "matchweek": p["round_num"],
                "home_team": p["home_team"],
                "away_team": p["away_team"],
                "kickoff_utc": ko,
                "status": "scheduled",
            })

    all_fixtures = group_fixtures + [
        {
            "external_id": f["external_id"],
            "league": "world_cup_2026",
            "season": "2025-2026",
            "matchweek": f["matchweek"],
            "home_team": f["home_team"],
            "away_team": f["away_team"],
            "kickoff_utc": f["kickoff_utc"],
            "status": "scheduled",
        }
        for f in _KO_FIXTURES
    ]

    # 3. Insert with ON CONFLICT DO NOTHING
    inserted = 0
    skipped = 0
    for fx in all_fixtures:
        cur.execute(
            """
            INSERT INTO fixtures
                (external_id, league, season, matchweek, home_team, away_team, kickoff_utc, status)
            VALUES
                (%(external_id)s, %(league)s, %(season)s, %(matchweek)s,
                 %(home_team)s, %(away_team)s, %(kickoff_utc)s, %(status)s)
            ON CONFLICT (external_id) DO NOTHING
            """,
            fx,
        )
        if cur.rowcount == 1:
            inserted += 1
        else:
            skipped += 1

    conn.commit()
    cur.close()
    conn.close()
    print(f"Done: {inserted} inserted, {skipped} skipped (already existed)")
    print(f"  Group stage: {len(group_fixtures)} fixtures")
    print(f"  KO rounds:   {len(_KO_FIXTURES)} placeholders")


if __name__ == "__main__":
    main()
