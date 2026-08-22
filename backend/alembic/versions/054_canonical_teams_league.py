"""Ajoute canonical_teams.league_api_id et season (engagement de la saison).

Le championnat d'une equipe n'etait stocke nulle part : il etait deduit en
regroupant les joueurs par current_team_name puis en retenant la competition
majoritaire. Cette colonne est fausse pour 886 joueurs sur 2 401 (37 %), d'ou
des filtres qui melangeaient les championnats -- Serie A remontant "Alcione
Milano", Ligue des champions remontant "Inter Club d'Escaldes" (Andorre), et
le vrai Milan absent d'Italie.

Revision ID: 054
Revises: 053
Create Date: 2026-08-22
"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "054"
down_revision: str | None = "053"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Nullables : un club relegue garde sa ligne et son historique, il perd
    # seulement son engagement pour la saison courante.
    op.add_column(
        "canonical_teams",
        sa.Column("league_api_id", sa.Integer(), nullable=True),
    )
    op.add_column(
        "canonical_teams",
        sa.Column("season", sa.String(length=10), nullable=True),
    )
    # Sert les filtres de championnat, qui interrogent toujours la paire.
    op.create_index(
        "ix_canonical_teams_league_season",
        "canonical_teams",
        ["league_api_id", "season"],
    )


def downgrade() -> None:
    op.drop_index("ix_canonical_teams_league_season", table_name="canonical_teams")
    op.drop_column("canonical_teams", "season")
    op.drop_column("canonical_teams", "league_api_id")
