"""Add odds_api_event_id to fixtures table

Revision ID: 006
Revises: 005
Create Date: 2026-02-23

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "006"
down_revision: str | None = "005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("fixtures", sa.Column("odds_api_event_id", sa.String(100), nullable=True))
    op.create_index("ix_fixtures_odds_api_event_id", "fixtures", ["odds_api_event_id"], unique=True)


def downgrade() -> None:
    op.drop_index("ix_fixtures_odds_api_event_id")
    op.drop_column("fixtures", "odds_api_event_id")
