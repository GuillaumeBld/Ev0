"""Cree bzz_lineup_slots : la compo d'un match, resolue joueur par joueur.

bzz_events.lineups garde la reponse brute de l'API. Elle n'est pas
exploitable telle quelle : sur 1 468 matchs -- du 15/08/2025 au 13/04/2026 --
Bzzoiro renvoie `"id": null` et ne laisse qu'un nom court, ce qui interdit de
relier la compo aux statistiques du joueur.

Cette table porte le resultat du rapprochement, sans jamais toucher a
l'archive. player_api_id est nullable a dessein : une place non resolue reste
visible avec son nom, plutot que de disparaitre silencieusement.

Revision ID: 057
Revises: 056
Create Date: 2026-09-09
"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "057"
down_revision: str | None = "056"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "bzz_lineup_slots",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("event_api_id", sa.Integer(), nullable=False),
        sa.Column("is_home", sa.Boolean(), nullable=False),
        sa.Column("is_starter", sa.Boolean(), nullable=False),
        sa.Column("player_name", sa.String(length=200), nullable=False),
        sa.Column("player_api_id", sa.Integer(), nullable=True),
        sa.Column("resolution", sa.String(length=12), nullable=False),
        sa.Column("position", sa.String(length=4), nullable=True),
        sa.Column("jersey_number", sa.Integer(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            server_default=sa.text("now()"), nullable=False,
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True),
            server_default=sa.text("now()"), nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "event_api_id", "is_home", "player_name", name="uq_bzz_lineup_slot"
        ),
    )
    op.create_index(
        "ix_bzz_lineup_slots_event", "bzz_lineup_slots", ["event_api_id"]
    )
    op.create_index(
        "ix_bzz_lineup_slots_player", "bzz_lineup_slots", ["player_api_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_bzz_lineup_slots_player", table_name="bzz_lineup_slots")
    op.drop_index("ix_bzz_lineup_slots_event", table_name="bzz_lineup_slots")
    op.drop_table("bzz_lineup_slots")
