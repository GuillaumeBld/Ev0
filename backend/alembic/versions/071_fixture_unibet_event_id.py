"""Accrochage Unibet : identifiant du match chez Unibet.

Revision ID: 071
Revises: 070
Create Date: 2026-11-24

`fixtures.unibet_event_id`, nullable et unique, sans backfill : le scraper le
pose lui-même la première fois qu'il reconnaît le match dans la liste d'un
championnat, puis relit le match par cet identifiant.
"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "071"
down_revision: str | None = "070"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("fixtures", sa.Column("unibet_event_id", sa.Integer(), nullable=True))
    op.create_unique_constraint("uq_fixtures_unibet_event_id", "fixtures", ["unibet_event_id"])


def downgrade() -> None:
    op.drop_constraint("uq_fixtures_unibet_event_id", "fixtures", type_="unique")
    op.drop_column("fixtures", "unibet_event_id")
