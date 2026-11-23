"""Rattrapage de fixtures.competition_api_id depuis les evenements Bzzoiro.

Revision ID: 068
Revises: 067
Create Date: 2026-11-23

Remplit l'identifiant de competition des matchs deja en base. Ne touche PAS a
`fixtures.league` : le script de reparation corrige les etiquettes et supprime
les recommandations mal rangees.
"""
from collections.abc import Sequence

from alembic import op

revision: str = "068"
down_revision: str | None = "067"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

BACKFILL_SQL = (
    "UPDATE fixtures f SET competition_api_id = e.league_api_id "
    "FROM bzz_events e "
    "WHERE f.external_id = 'bzz_' || e.api_id "
    "AND f.competition_api_id IS NULL "
    "AND e.league_api_id IS NOT NULL"
)


def upgrade() -> None:
    op.execute(BACKFILL_SQL)


def downgrade() -> None:
    # Rattrapage de donnees : rien a defaire.
    pass
