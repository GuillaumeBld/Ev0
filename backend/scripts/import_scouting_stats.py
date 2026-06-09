#!/usr/bin/env python3
"""Import ScoutingStats WC2026 player data into wc2026_scouting_stats table.

Usage:
    python scripts/import_scouting_stats.py /path/to/scouting_wc2026_full.json
"""
import json
import re
import sys
import unicodedata
from pathlib import Path

import psycopg2
import psycopg2.extras


def norm(name: str) -> str:
    name = unicodedata.normalize("NFD", name)
    name = "".join(c for c in name if unicodedata.category(c) != "Mn")
    name = re.sub(r"[^a-z0-9 ]", "", name.lower())
    return name.strip()


def main() -> None:
    if len(sys.argv) < 2:
        print("Usage: python import_scouting_stats.py <json_file>")
        sys.exit(1)

    json_path = Path(sys.argv[1])
    with open(json_path) as f:
        players = json.load(f)

    print(f"Loaded {len(players)} players from {json_path}")

    import os
    dsn = os.environ.get("DATABASE_URL", "postgresql://ev0:eqv2pWEYjMchXWAVVouiAb4nD2uKBug@localhost:5432/ev0")
    conn = psycopg2.connect(dsn)
    cur = conn.cursor()

    # Truncate existing data for this season
    cur.execute("DELETE FROM wc2026_scouting_stats WHERE season = '2025-2026'")
    print(f"Cleared existing 2025-2026 rows")

    rows = []
    for p in players:
        rows.append({
            "scouting_id": p.get("player_id"),
            "player_name": p["player_name"],
            "normalized_name": norm(p["player_name"]),
            "nation": p.get("nation"),
            "season": "2025-2026",
            "stats": json.dumps(p),
        })

    psycopg2.extras.execute_batch(
        cur,
        """
        INSERT INTO wc2026_scouting_stats
            (scouting_id, player_name, normalized_name, nation, season, stats)
        VALUES
            (%(scouting_id)s, %(player_name)s, %(normalized_name)s, %(nation)s, %(season)s, %(stats)s)
        ON CONFLICT (scouting_id, season) DO UPDATE SET
            player_name = EXCLUDED.player_name,
            normalized_name = EXCLUDED.normalized_name,
            nation = EXCLUDED.nation,
            stats = EXCLUDED.stats,
            scraped_at = NOW()
        """,
        rows,
        page_size=200,
    )
    conn.commit()
    print(f"Inserted/updated {len(rows)} rows")
    cur.close()
    conn.close()


if __name__ == "__main__":
    main()
