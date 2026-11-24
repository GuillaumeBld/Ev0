"""Variante avec sub / sans sub des cotes joueur.

Revision ID: 070
Revises: 069
Create Date: 2026-11-24

`player_odds_snapshots.variant` : "avec_sub" quand le pari couvre le
remplaçant du joueur, "sans_sub" sinon. Les lignes existantes reçoivent
"inconnu" : le scraper ne notait pas la variante (Betclic mélangeait les deux
marchés buteur) et rien ne permet de la retrouver. Le prochain passage du
scraper réécrit celles des matchs à venir ; celles des matchs terminés
restent "inconnu" — ne jamais les prendre pour du sans sub.
"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "070"
down_revision: str | None = "069"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "player_odds_snapshots",
        sa.Column("variant", sa.String(length=10), nullable=False, server_default="inconnu"),
    )


def downgrade() -> None:
    op.drop_column("player_odds_snapshots", "variant")
