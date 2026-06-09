# backend/alembic/versions/038_fixtures_external_id_unique.py
"""Add UNIQUE constraint on fixtures.external_id.

Revision ID: 038
Revises: 037
Create Date: 2026-06-09
"""
from collections.abc import Sequence

from alembic import op

revision: str = "038"
down_revision: str | None = "037"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.create_unique_constraint("uq_fixtures_external_id", "fixtures", ["external_id"])


def downgrade() -> None:
    op.drop_constraint("uq_fixtures_external_id", "fixtures", type_="unique")
