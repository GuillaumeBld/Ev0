"""Add fixtures.ps3838_event_id (ancrage des cotes par identifiant).

Revision ID: 051
Revises: 050
Create Date: 2026-08-19
"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "051"
down_revision: str | None = "050"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("fixtures", sa.Column("ps3838_event_id", sa.Integer(), nullable=True))
    op.create_index(
        "ix_fixtures_ps3838_event_id", "fixtures", ["ps3838_event_id"], unique=True
    )


def downgrade() -> None:
    op.drop_index("ix_fixtures_ps3838_event_id", table_name="fixtures")
    op.drop_column("fixtures", "ps3838_event_id")
