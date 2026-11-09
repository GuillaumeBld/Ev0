"""Ajoute la colonne `is_national` a player_career_seasons.

Separe le compartiment Selection (equipe nationale) du compartiment Club a
partir du meme flux Transfermarkt : un bucket (saison, competition) est
"selection" si TOUS ses matchs joues sont internationaux
(gameInformation.isNationalGame). Colonne NOT NULL, defaut `false` : un
bucket club normal, ou tout bucket ancien/mixte/ambigu deja en base, n'est
jamais marque selection a tort par cette migration.

Revision ID: 064
Revises: 063
Create Date: 2026-11-09
"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "064"
down_revision: str | None = "063"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "player_career_seasons",
        sa.Column("is_national", sa.Boolean(), nullable=False, server_default=sa.text("false")),
    )


def downgrade() -> None:
    op.drop_column("player_career_seasons", "is_national")
