"""Backfill fixtures: bzz_team_ids from bzz_events, canonical_team_ids from canonical_teams.

Two-step backfill:
  1. For every fixture whose external_id = 'bzz_<api_id>', pull home/away_team_api_id
     from bzz_events and store in home/away_bzz_team_id.
  2. Join bzz_team_id → canonical_teams.bzz_team_id to set home/away_canonical_team_id
     for all fixtures that now have bzz_team_ids.

After this migration, canonical_team_id is driven purely by the integer bzz_team_id
join and no longer depends on fragile text name matching.

Revision ID: 031
Revises: 030
Create Date: 2026-05-22
"""
import sqlalchemy as sa
from alembic import op

revision = "031"
down_revision = "030"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()

    # Step 1: backfill home/away_bzz_team_id for fixtures linked to a bzz_event
    result = conn.execute(sa.text("""
        UPDATE fixtures f
        SET
            home_bzz_team_id = be.home_team_api_id,
            away_bzz_team_id = be.away_team_api_id
        FROM bzz_events be
        WHERE f.external_id = 'bzz_' || be.api_id::text
          AND (f.home_bzz_team_id IS NULL OR f.away_bzz_team_id IS NULL)
    """))
    print(f"  Step 1: {result.rowcount} fixtures backfilled with bzz_team_ids")

    # Step 2: backfill home/away_canonical_team_id via bzz_team_id join (each side independently)
    r_home = conn.execute(sa.text("""
        UPDATE fixtures f
        SET home_canonical_team_id = ct.id
        FROM canonical_teams ct
        WHERE ct.bzz_team_id = f.home_bzz_team_id
          AND f.home_bzz_team_id IS NOT NULL
          AND f.home_canonical_team_id IS NULL
    """))
    r_away = conn.execute(sa.text("""
        UPDATE fixtures f
        SET away_canonical_team_id = ct.id
        FROM canonical_teams ct
        WHERE ct.bzz_team_id = f.away_bzz_team_id
          AND f.away_bzz_team_id IS NOT NULL
          AND f.away_canonical_team_id IS NULL
    """))
    print(f"  Step 2: {r_home.rowcount} home + {r_away.rowcount} away canonical_team_ids backfilled")

    # Report remaining gaps
    gaps = conn.execute(sa.text("""
        SELECT COUNT(*) FROM fixtures
        WHERE home_canonical_team_id IS NULL OR away_canonical_team_id IS NULL
    """)).scalar()
    print(f"  Remaining fixtures without canonical_team_id: {gaps}")


def downgrade() -> None:
    pass
