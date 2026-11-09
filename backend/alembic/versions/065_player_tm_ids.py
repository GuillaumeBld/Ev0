"""Jalon 1 (contournement rate-limit Transfermarkt) : store player_tm_ids.

Liaison joueur -> identifiant Transfermarkt, tracable par source ('squad' ou
'wikidata'). Fondation du contournement du rate-limit : les jalons suivants
remplissent cette table (effectifs, Wikidata), et l'import carriere la lit
pour sauter la recherche quand le tm_id du joueur est deja connu.

Revision ID: 065
Revises: 064
Create Date: 2026-11-09
"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "065"
down_revision: str | None = "064"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "player_tm_ids",
        sa.Column("player_api_id", sa.Integer(), nullable=False),
        sa.Column("tm_id", sa.Integer(), nullable=False),
        sa.Column("source", sa.String(length=16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["player_api_id"], ["bzz_players.api_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("player_api_id"),
    )
    op.create_index("ix_player_tm_ids_tm_id", "player_tm_ids", ["tm_id"])


def downgrade() -> None:
    op.drop_index("ix_player_tm_ids_tm_id", table_name="player_tm_ids")
    op.drop_table("player_tm_ids")
