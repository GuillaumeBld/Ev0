"""Ajoute team_lineups.lineup_status et published_at.

lineup_type dit d'ou vient la compo et sert la priorite du resolveur.
lineup_status conserve ce que Bzzoiro declare -- "confirmed" pour une compo
officielle, "predicted" pour une probable publiee un a deux jours avant -- et
published_at l'heure de publication.

Sans eux, impossible de repondre apres coup a : ce prix a-t-il ete calcule sur
une compo reelle ou supposee ?

Revision ID: 055
Revises: 054
Create Date: 2026-08-25
"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "055"
down_revision: str | None = "054"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Nullables : les compos manuelles et les lignes existantes n'ont pas de
    # statut Bzzoiro.
    op.add_column(
        "team_lineups",
        sa.Column("lineup_status", sa.String(length=20), nullable=True),
    )
    op.add_column(
        "team_lineups",
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("team_lineups", "published_at")
    op.drop_column("team_lineups", "lineup_status")
