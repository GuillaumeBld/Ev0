"""Trace les tentatives d'import de carriere Transfermarkt.

L'import de carriere n'a tourne que les 29 et 30 juillet 2026, a la main, via
`python -m app.scripts.import_career --input <fichier>.json`. Aucun job ne le
relance : tout joueur arrive depuis (transferts de fin de mercato, promus,
jeunes qui percent) est reste sans carriere. Au 10/09/2026, 840 joueurs des
cinq grands championnats et de la C1 sont dans ce cas.

Cette table permet d'automatiser l'import sans marteler Transfermarkt : un
joueur que le resolveur ne sait pas identifier ne doit pas etre retente a
chaque passage. Elle sert aussi de cache persistant du tm_id resolu, le
worker n'ayant aucun volume ou le fichier de cache survivrait a une
reconstruction d'image.

Revision ID: 059
Revises: 058
Create Date: 2026-09-10
"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "059"
down_revision: str | None = "058"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "career_import_attempts",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("player_api_id", sa.Integer(), nullable=False),
        sa.Column("attempted_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("outcome", sa.String(length=10), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("tm_id", sa.String(length=20), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("player_api_id", name="uq_career_import_attempt_player"),
    )
    op.create_index(
        "ix_career_import_attempts_player_api_id",
        "career_import_attempts",
        ["player_api_id"],
    )
    # La file d'attente filtre sur (outcome, attempted_at) pour ecarter les
    # echecs recents : index dedie, la table grandit avec le referentiel.
    op.create_index(
        "ix_career_import_attempts_outcome_attempted",
        "career_import_attempts",
        ["outcome", "attempted_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_career_import_attempts_outcome_attempted", table_name="career_import_attempts")
    op.drop_index("ix_career_import_attempts_player_api_id", table_name="career_import_attempts")
    op.drop_table("career_import_attempts")
