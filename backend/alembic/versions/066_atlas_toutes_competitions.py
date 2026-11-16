"""Atlas : les 46 compétitions Bzzoiro restantes dans `leagues`.

Revision ID: 066
Revises: 065
Create Date: 2026-11-14

Instantané figé (comme 060) : on n'importe jamais le registre vivant depuis une
migration. Championnats, coupes nationales (kind `cup`), féminins (kind
`women`), sélections et compétitions internationales de clubs.
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "066"
down_revision: str | None = "065"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_IDS = [28, 29, 30, 31, 34, 35, 36, 39, 40, 41, 42, 43, 44, 46, 48, 51, 56, 57, 60, 61, 68, 69, 70, 71, 72, 79, 81, 82, 86, 87, 91, 92, 93, 95, 96, 97, 98, 105, 106, 107, 108, 109, 110, 111, 112, 113]


def upgrade() -> None:
    leagues_table = sa.table(
        "leagues",
        sa.column("id", sa.Integer),
        sa.column("name", sa.String),
        sa.column("country", sa.String),
        sa.column("tier", sa.String),
        sa.column("kind", sa.String),
        sa.column("format", sa.String),
        sa.column("expected_count", sa.Integer),
        sa.column("rank", sa.Integer),
    )
    op.bulk_insert(
        leagues_table,
        [
            {'id': 96, 'name': 'Bundesliga', 'country': 'Austria', 'tier': '1', 'kind': 'domestic_1', 'format': 'round_robin', 'expected_count': 12, 'rank': 58},
            {'id': 109, 'name': 'Niké liga', 'country': 'Slovakia', 'tier': '1', 'kind': 'domestic_1', 'format': 'round_robin', 'expected_count': 12, 'rank': 59},
            {'id': 97, 'name': 'Challenger Pro League', 'country': 'Belgium', 'tier': '3', 'kind': 'domestic_2', 'format': 'round_robin', 'expected_count': 15, 'rank': 60},
            {'id': 86, 'name': 'League One', 'country': 'England', 'tier': '3', 'kind': 'domestic_2', 'format': 'round_robin', 'expected_count': 24, 'rank': 61},
            {'id': 87, 'name': 'League Two', 'country': 'England', 'tier': '3', 'kind': 'domestic_2', 'format': 'round_robin', 'expected_count': 24, 'rank': 62},
            {'id': 91, 'name': 'National League', 'country': 'England', 'tier': '3', 'kind': 'domestic_2', 'format': 'round_robin', 'expected_count': 24, 'rank': 63},
            {'id': 105, 'name': 'Ligue 3', 'country': 'France', 'tier': '3', 'kind': 'domestic_2', 'format': 'round_robin', 'expected_count': 18, 'rank': 64},
            {'id': 108, 'name': 'Eerste Divisie', 'country': 'Netherlands', 'tier': '3', 'kind': 'domestic_2', 'format': 'round_robin', 'expected_count': 20, 'rank': 65},
            {'id': 107, 'name': '1. Lig', 'country': 'Turkey', 'tier': '3', 'kind': 'domestic_2', 'format': 'round_robin', 'expected_count': 20, 'rank': 66},
            {'id': 106, 'name': 'Superettan', 'country': 'Sweden', 'tier': '3', 'kind': 'domestic_2', 'format': 'round_robin', 'expected_count': 18, 'rank': 67},
            {'id': 82, 'name': 'Liga 3', 'country': 'Portugal', 'tier': '3', 'kind': 'domestic_2', 'format': 'groups', 'expected_count': None, 'rank': 68},
            {'id': 95, 'name': 'Campeonato de Portugal', 'country': 'Portugal', 'tier': '3', 'kind': 'domestic_2', 'format': 'groups', 'expected_count': None, 'rank': 69},
            {'id': 34, 'name': 'Brasileirão Série B', 'country': 'Brazil', 'tier': '4', 'kind': 'world', 'format': 'round_robin', 'expected_count': 20, 'rank': 70},
            {'id': 111, 'name': 'Primera División', 'country': 'Chile', 'tier': '4', 'kind': 'world', 'format': 'round_robin', 'expected_count': 16, 'rank': 71},
            {'id': 112, 'name': 'LigaPro Serie A', 'country': 'Ecuador', 'tier': '4', 'kind': 'world', 'format': 'round_robin', 'expected_count': 16, 'rank': 72},
            {'id': 113, 'name': 'Liga 1', 'country': 'Peru', 'tier': '4', 'kind': 'world', 'format': 'round_robin', 'expected_count': 18, 'rank': 73},
            {'id': 57, 'name': 'USL Championship', 'country': 'USA', 'tier': '4', 'kind': 'world', 'format': 'groups', 'expected_count': None, 'rank': 74},
            {'id': 28, 'name': 'NPFL', 'country': 'Nigeria', 'tier': '4', 'kind': 'world', 'format': 'round_robin', 'expected_count': 20, 'rank': 75},
            {'id': 98, 'name': 'Girabola', 'country': 'Angola', 'tier': '4', 'kind': 'world', 'format': 'round_robin', 'expected_count': 16, 'rank': 76},
            {'id': 70, 'name': 'NPL Queensland', 'country': 'Australia', 'tier': '4', 'kind': 'world', 'format': 'round_robin', 'expected_count': 12, 'rank': 77},
            {'id': 39, 'name': 'FA Cup', 'country': 'England', 'tier': '5', 'kind': 'cup', 'format': 'knockout', 'expected_count': None, 'rank': 78},
            {'id': 40, 'name': 'Carabao Cup', 'country': 'England', 'tier': '5', 'kind': 'cup', 'format': 'knockout', 'expected_count': None, 'rank': 79},
            {'id': 44, 'name': 'Coupe de France', 'country': 'France', 'tier': '5', 'kind': 'cup', 'format': 'knockout', 'expected_count': None, 'rank': 80},
            {'id': 43, 'name': 'DFB-Pokal', 'country': 'Germany', 'tier': '5', 'kind': 'cup', 'format': 'knockout', 'expected_count': None, 'rank': 81},
            {'id': 41, 'name': 'Copa del Rey', 'country': 'Spain', 'tier': '5', 'kind': 'cup', 'format': 'knockout', 'expected_count': None, 'rank': 82},
            {'id': 42, 'name': 'Coppa Italia', 'country': 'Italy', 'tier': '5', 'kind': 'cup', 'format': 'knockout', 'expected_count': None, 'rank': 83},
            {'id': 92, 'name': 'Taça de Portugal', 'country': 'Portugal', 'tier': '5', 'kind': 'cup', 'format': 'knockout', 'expected_count': None, 'rank': 84},
            {'id': 93, 'name': 'Taça da Liga', 'country': 'Portugal', 'tier': '5', 'kind': 'cup', 'format': 'knockout', 'expected_count': None, 'rank': 85},
            {'id': 110, 'name': 'Coupe de Turquie', 'country': 'Turkey', 'tier': '5', 'kind': 'cup', 'format': 'knockout', 'expected_count': None, 'rank': 86},
            {'id': 46, 'name': 'Puchar Polski', 'country': 'Poland', 'tier': '5', 'kind': 'cup', 'format': 'knockout', 'expected_count': None, 'rank': 87},
            {'id': 56, 'name': 'Suomen Cup', 'country': 'Finland', 'tier': '5', 'kind': 'cup', 'format': 'knockout', 'expected_count': None, 'rank': 88},
            {'id': 35, 'name': 'Copa do Brasil', 'country': 'Brazil', 'tier': '5', 'kind': 'cup', 'format': 'knockout', 'expected_count': None, 'rank': 89},
            {'id': 81, 'name': 'Copa Colombia', 'country': 'Colombia', 'tier': '5', 'kind': 'cup', 'format': 'knockout', 'expected_count': None, 'rank': 90},
            {'id': 51, 'name': "Coupe de l'Empereur", 'country': 'Japan', 'tier': '5', 'kind': 'cup', 'format': 'knockout', 'expected_count': None, 'rank': 91},
            {'id': 48, 'name': 'Coupe de Tunisie', 'country': 'Tunisia', 'tier': '5', 'kind': 'cup', 'format': 'knockout', 'expected_count': None, 'rank': 92},
            {'id': 36, 'name': 'Liga F', 'country': 'Spain', 'tier': '6', 'kind': 'women', 'format': 'round_robin', 'expected_count': 16, 'rank': 93},
            {'id': 72, 'name': 'NWSL', 'country': 'USA', 'tier': '6', 'kind': 'women', 'format': 'round_robin', 'expected_count': 16, 'rank': 94},
            {'id': 29, 'name': 'Ligue des champions CAF', 'country': None, 'tier': '4', 'kind': 'world', 'format': 'groups', 'expected_count': None, 'rank': 95},
            {'id': 79, 'name': 'Matchs amicaux de clubs', 'country': None, 'tier': '4', 'kind': 'world', 'format': 'knockout', 'expected_count': None, 'rank': 96},
            {'id': 30, 'name': "Coupe d'Afrique des nations", 'country': None, 'tier': 'selection', 'kind': 'selection', 'format': 'groups', 'expected_count': None, 'rank': 97},
            {'id': 68, 'name': "Coupe d'Asie", 'country': None, 'tier': 'selection', 'kind': 'selection', 'format': 'groups', 'expected_count': None, 'rank': 98},
            {'id': 69, 'name': 'Gold Cup', 'country': None, 'tier': 'selection', 'kind': 'selection', 'format': 'groups', 'expected_count': None, 'rank': 99},
            {'id': 60, 'name': 'Qualifs CM — CAF', 'country': None, 'tier': 'selection', 'kind': 'selection', 'format': 'groups', 'expected_count': None, 'rank': 100},
            {'id': 61, 'name': 'Qualifs CM — AFC', 'country': None, 'tier': 'selection', 'kind': 'selection', 'format': 'groups', 'expected_count': None, 'rank': 101},
            {'id': 71, 'name': 'Euro U19', 'country': None, 'tier': 'selection', 'kind': 'selection', 'format': 'groups', 'expected_count': None, 'rank': 102},
            {'id': 31, 'name': 'Matchs amicaux internationaux', 'country': None, 'tier': 'selection', 'kind': 'selection', 'format': 'knockout', 'expected_count': None, 'rank': 103},
        ],
    )


def downgrade() -> None:
    op.execute(f"DELETE FROM leagues WHERE id IN ({', '.join(str(i) for i in _IDS)})")
