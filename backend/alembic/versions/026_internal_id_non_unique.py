"""Drop UNIQUE constraint on bzz_players.internal_id.

Bzzoiro reuses id values across player contexts, so the unique index blocks
valid upserts. The column is only used as an API query parameter in
/api/player-stats/?player=<internal_id>; uniqueness is not required.

Revision ID: 026
Revises: 025
Create Date: 2026-05-01
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "026"
down_revision: str | None = "025"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_index("ix_bzz_players_internal_id", table_name="bzz_players")
    op.create_index("ix_bzz_players_internal_id", "bzz_players", ["internal_id"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_bzz_players_internal_id", table_name="bzz_players")
    op.create_index("ix_bzz_players_internal_id", "bzz_players", ["internal_id"], unique=True)
