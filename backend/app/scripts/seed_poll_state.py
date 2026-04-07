"""Seed oddsportal_poll_state from a CSV file.

CSV format (header required):
    fixture_id,oddsportal_url,betclic_url,unibet_url

betclic_url and unibet_url are optional (leave blank if not available).

Usage:
    python -m app.scripts.seed_poll_state --csv /path/to/gameweek.csv [--dry-run]

The script upserts rows: existing fixture_id records are updated with new URLs.
next_due_at_utc is set to now() on insert, left unchanged on update.
"""

import argparse
import asyncio
import csv
import sys
from datetime import datetime, timezone
from pathlib import Path

async def seed(csv_path: Path, dry_run: bool) -> None:
    rows = []
    with csv_path.open(newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            fixture_id = int(row["fixture_id"])
            op_url = row["oddsportal_url"].strip()
            if not op_url:
                print(f"SKIP fixture {fixture_id}: no oddsportal_url", file=sys.stderr)
                continue
            rows.append({
                "fixture_id": fixture_id,
                "oddsportal_url": op_url,
                "betclic_url": row.get("betclic_url", "").strip() or None,
                "unibet_url": row.get("unibet_url", "").strip() or None,
                "next_due_at_utc": datetime.now(timezone.utc),
                "error_streak": 0,
                "stopped": False,
                "stopped_reason": None,
            })

    print(f"Seeding {len(rows)} fixtures (dry_run={dry_run})")

    if dry_run:
        for r in rows:
            print(f"  fixture={r['fixture_id']} op={r['oddsportal_url']}")
        return

    # Only import DB-related modules when actually connecting
    from sqlalchemy.dialects.postgresql import insert as pg_insert

    from app.db import async_session
    from app.models.poll_state import OddsPortalPollState

    async with async_session() as session:
        stmt = pg_insert(OddsPortalPollState).values(rows)
        stmt = stmt.on_conflict_do_update(
            constraint="uq_poll_state_fixture",
            set_={
                "oddsportal_url": stmt.excluded.oddsportal_url,
                "betclic_url": stmt.excluded.betclic_url,
                "unibet_url": stmt.excluded.unibet_url,
                # Do NOT reset next_due_at_utc or error_streak on update
            },
        )
        await session.execute(stmt)
        await session.commit()
        print("Done.")


def main() -> None:
    parser = argparse.ArgumentParser(description="Seed poll state from CSV")
    parser.add_argument("--csv", required=True, type=Path, help="Path to CSV file")
    parser.add_argument("--dry-run", action="store_true", help="Print rows without inserting")
    args = parser.parse_args()

    if not args.csv.exists():
        print(f"File not found: {args.csv}", file=sys.stderr)
        sys.exit(1)

    asyncio.run(seed(args.csv, args.dry_run))


if __name__ == "__main__":
    main()
