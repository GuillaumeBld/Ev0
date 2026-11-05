"""Ecussons de clubs via footylogos.com: canonical_teams.crest_url.

Les ecussons etaient resolus cote front par un dictionnaire nom -> id
code en dur (`frontend/src/lib/teamLogos.ts`, big-5/UCL uniquement) puis,
pour l'API-Football, via `canonical_teams.api_football_id` (migration 060
et anterieures) — mais cette colonne reste NULL pour l'immense majorite
des ~507 clubs engages (pas de cle API-Football en production, cf.
`app/ingestion/apifootball/resolve_crests.py`).

`crest_url` est la nouvelle cible, alimentee par
`app/ingestion/footylogos/resolve_crests.py` (footylogos.com, gratuit,
sans cle) : stocke l'URL image complete (deja verifiee 200 avant d'etre
posee — jamais une URL devinee), pas juste un identifiant a reconstituer
cote client. Nullable : la resolution est executee manuellement (comme
`build_full_referentiel`), pas cablee dans le worker, et une partie des
clubs restera sans ecusson resolu (le front retombe sur
`api_football_id` puis le placeholder existant).

Revision ID: 061
Revises: 060
Create Date: 2026-11-05
"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "061"
down_revision: str | None = "060"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "canonical_teams",
        sa.Column("crest_url", sa.String(length=500), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("canonical_teams", "crest_url")
