"""Identifie une place de compo par son rang, et non par le nom du joueur.

La migration 057 posait la cle d'unicite sur (match, camp, nom). Elle est
fausse : deux joueurs d'un meme camp peuvent porter le meme nom abrege.
Osasuna aligne deux "R. Garcia" (maillots 9 et 14), Middlesbrough deux
"J. Jones" (51 et 52) -- 29 collisions de ce type dans le perimetre, relevees
le 09/09/2026.

Consequence de l'ancienne cle : l'insertion echouait sur ces matchs, et si
elle avait abouti, elle aurait fondu deux joueurs distincts en une ligne.

Le rang sur la feuille -- titulaires puis remplacants, par camp -- est la
seule chose qui distingue deux places a coup sur. La table etant encore vide
au moment de cette migration, aucune donnee n'est a reprendre.

Revision ID: 058
Revises: 057
Create Date: 2026-09-09
"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "058"
down_revision: str | None = "057"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # server_default le temps de la creation, puis retire : le rang est
    # toujours ecrit explicitement par le resolveur.
    op.add_column(
        "bzz_lineup_slots",
        sa.Column("slot", sa.Integer(), nullable=False, server_default="0"),
    )
    op.alter_column("bzz_lineup_slots", "slot", server_default=None)
    op.drop_constraint("uq_bzz_lineup_slot", "bzz_lineup_slots", type_="unique")
    op.create_unique_constraint(
        "uq_bzz_lineup_slot", "bzz_lineup_slots", ["event_api_id", "is_home", "slot"]
    )


def downgrade() -> None:
    op.drop_constraint("uq_bzz_lineup_slot", "bzz_lineup_slots", type_="unique")
    op.create_unique_constraint(
        "uq_bzz_lineup_slot",
        "bzz_lineup_slots",
        ["event_api_id", "is_home", "player_name"],
    )
    op.drop_column("bzz_lineup_slots", "slot")
