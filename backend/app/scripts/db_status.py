"""Database status and backfill coverage report.

Uses **sync psycopg2** directly to avoid greenlet/asyncpg issues when running
as a standalone script.

Usage::

    python -m app.scripts.db_status
"""

from app.config import settings

# Build sync connection string from the async-compatible DATABASE_URL
_DB_URL = settings.database_url
# Ensure we have a plain postgresql:// URL for psycopg2
if _DB_URL.startswith("postgresql+asyncpg://"):
    _DB_URL = _DB_URL.replace("postgresql+asyncpg://", "postgresql://")


def _connect():
    """Create a sync psycopg2 connection."""
    import psycopg2

    return psycopg2.connect(_DB_URL)


def _query(conn, sql: str, params: tuple = ()) -> list[tuple]:
    """Execute a query and return all rows."""
    with conn.cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchall()


def _scalar(conn, sql: str, params: tuple = ()):
    """Execute a query and return the first column of the first row."""
    rows = _query(conn, sql, params)
    return rows[0][0] if rows else 0


def report() -> None:
    """Print a full database status report."""
    conn = _connect()

    print("=" * 60)
    print("  Ev0 Database Status Report")
    print("=" * 60)

    # ── Table counts ──────────────────────────────────────────
    print("\n--- Record Counts ---")
    tables = ["fixtures", "match_events", "players", "player_stats", "odds_snapshots", "recommendations"]
    for table in tables:
        try:
            count = _scalar(conn, f"SELECT COUNT(*) FROM {table}")  # noqa: S608
            print(f"  {table:25s} {count:>8,}")
        except Exception:
            print(f"  {table:25s}     N/A")

    # ── Fixtures by season/league/status ──────────────────────
    print("\n--- Fixtures by Season / League / Status ---")
    rows = _query(
        conn,
        """
        SELECT season, league, status, COUNT(*)
        FROM fixtures
        GROUP BY season, league, status
        ORDER BY season, league, status
        """,
    )
    for season, league, status, count in rows:
        print(f"  {season:12s} {league:20s} {status:12s} {count:>6,}")

    # ── Backfill coverage ─────────────────────────────────────
    print("\n--- Backfill Coverage (finished fixtures) ---")
    rows = _query(
        conn,
        """
        SELECT
            f.season,
            f.league,
            COUNT(*) AS total_finished,
            COUNT(DISTINCT me.fixture_id) AS has_events,
            COUNT(DISTINCT os.fixture_id) AS has_odds,
            COUNT(DISTINCT CASE
                WHEN me.fixture_id IS NOT NULL AND os.fixture_id IS NOT NULL
                THEN f.id
            END) AS has_both
        FROM fixtures f
        LEFT JOIN (SELECT DISTINCT fixture_id FROM match_events) me ON me.fixture_id = f.id
        LEFT JOIN (SELECT DISTINCT fixture_id FROM player_odds_snapshots) os ON os.fixture_id = f.id
        WHERE f.status = 'finished'
        GROUP BY f.season, f.league
        ORDER BY f.season, f.league
        """,
    )
    for season, league, total, events, odds, both in rows:
        print(f"  {season:12s} {league:20s}")
        print(f"    Finished:     {total:>6,}")
        print(f"    Has events:   {events:>6,}  ({events/total*100:.0f}%)" if total > 0 else "    Has events:        0")
        print(f"    Has odds:     {odds:>6,}  ({odds/total*100:.0f}%)" if total > 0 else "    Has odds:          0")
        print(f"    Has both:     {both:>6,}  ({both/total*100:.0f}%)" if total > 0 else "    Has both:          0")

    # ── Odds by bookmaker ─────────────────────────────────────
    print("\n--- Odds Breakdown by Bookmaker ---")
    rows = _query(
        conn,
        """
        SELECT bookmaker, COUNT(*), COUNT(DISTINCT fixture_id)
        FROM player_odds_snapshots
        GROUP BY bookmaker
        ORDER BY COUNT(*) DESC
        """,
    )
    for bookmaker, count, fixtures in rows:
        print(f"  {bookmaker:20s} {count:>8,} odds across {fixtures:>6,} fixtures")

    # ── Player stats by source/season ─────────────────────────
    print("\n--- Player Stats by Source / Season ---")
    rows = _query(
        conn,
        """
        SELECT source, season, COUNT(*), COUNT(DISTINCT player_id)
        FROM player_stats
        GROUP BY source, season
        ORDER BY season, source
        """,
    )
    for source, season, count, players in rows:
        print(f"  {source:15s} {season:12s} {count:>6,} snapshots, {players:>5,} players")

    # ── Forward test readiness ────────────────────────────────
    print("\n--- Forward Test Readiness (2025-2026) ---")
    fwd_fixtures = _scalar(
        conn,
        "SELECT COUNT(*) FROM fixtures WHERE season = '2025-2026' AND status = 'finished'",
    )
    fwd_events = _scalar(
        conn,
        """
        SELECT COUNT(DISTINCT me.fixture_id)
        FROM match_events me
        JOIN fixtures f ON f.id = me.fixture_id
        WHERE f.season = '2025-2026'
        """,
    )
    fwd_real_odds = _scalar(
        conn,
        """
        SELECT COUNT(*)
        FROM player_odds_snapshots os
        JOIN fixtures f ON f.id = os.fixture_id
        WHERE f.season = '2025-2026' AND os.bookmaker != 'synthetic'
        """,
    )
    print(f"  Finished fixtures:   {fwd_fixtures:>6,}")
    print(f"  With match events:   {fwd_events:>6,}")
    print(f"  Real odds snapshots: {fwd_real_odds:>6,}")

    if fwd_fixtures > 0 and fwd_events > 0 and fwd_real_odds > 0:
        print("  Status: READY for forward testing")
    else:
        missing = []
        if fwd_fixtures == 0:
            missing.append("fixtures")
        if fwd_events == 0:
            missing.append("match events")
        if fwd_real_odds == 0:
            missing.append("real odds")
        print(f"  Status: NOT READY — missing {', '.join(missing)}")

    print("\n" + "=" * 60)
    conn.close()


if __name__ == "__main__":
    report()
