"""Expansion ~57-competitions (Task 1): canonical_teams.country, leagues
table seeded from LEAGUE_REGISTRY, data_coverage_runs table.

canonical_teams.country is nullable: existing club rows predate this column
and are not retro-filled here — that's a later data task, not this
migration's job. `leagues` mirrors LEAGUE_REGISTRY verbatim as it stood on
this migration's Create Date (seeded below as a frozen literal, one row
per registry entry, keyed by the Bzzoiro internal id — not imported from
app.ingestion.bzzoiro.league_registry, since a migration is an immutable
record and must not re-run with whatever the live registry says later).
`data_coverage_runs` tracks per-league completeness checks (clubs/joueurs),
one row per run — see app/models/data_coverage.py for why there's no FK to
leagues.id.

Revision ID: 060
Revises: 059
Create Date: 2026-11-03
"""
from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "060"
down_revision: str | None = "059"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "canonical_teams",
        sa.Column("country", sa.String(length=100), nullable=True),
    )

    leagues_table = op.create_table(
        "leagues",
        sa.Column("id", sa.Integer(), nullable=False, autoincrement=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("country", sa.String(length=100), nullable=True),
        sa.Column("tier", sa.String(length=20), nullable=False),
        sa.Column("kind", sa.String(length=20), nullable=False),
        sa.Column("format", sa.String(length=20), nullable=False),
        sa.Column("expected_count", sa.Integer(), nullable=True),
        sa.Column("rank", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "data_coverage_runs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("league_api_id", sa.Integer(), nullable=False),
        sa.Column("clubs_total", sa.Integer(), nullable=True),
        sa.Column("clubs_ok", sa.Integer(), nullable=True),
        sa.Column("clubs_incomplets", sa.Integer(), nullable=True),
        sa.Column("joueurs_sans_stats", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(length=10), nullable=True),
        sa.Column("detail", postgresql.JSONB(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_data_coverage_runs_league_api_id",
        "data_coverage_runs",
        ["league_api_id"],
    )

    # Seed leagues — snapshot of LEAGUE_REGISTRY as it stood when this
    # migration was authored. A migration is an immutable historical
    # record: it must NOT import app.ingestion.bzzoiro.league_registry
    # and call seed_rows() at run time, because the registry's
    # expected_count values are expected to be corrected later as real
    # club counts are confirmed. Re-running this migration from scratch
    # on a fresh environment (CI, new dev machine, disaster recovery)
    # after such a correction would otherwise seed the *current* registry
    # values instead of the values frozen at the time 060 was written,
    # silently diverging from environments that ran 060 earlier. The 57
    # rows below are a verbatim, frozen copy — see
    # app/ingestion/bzzoiro/league_registry.py for the live registry that
    # the rest of the application code continues to read from.
    op.bulk_insert(
        leagues_table,
        [
            {"id": 1, "name": "Premier League", "country": "England", "tier": "1", "kind": "domestic_1", "format": "round_robin", "expected_count": 20, "rank": 1},
            {"id": 3, "name": "La Liga", "country": "Spain", "tier": "1", "kind": "domestic_1", "format": "round_robin", "expected_count": 20, "rank": 2},
            {"id": 4, "name": "Serie A", "country": "Italy", "tier": "1", "kind": "domestic_1", "format": "round_robin", "expected_count": 20, "rank": 3},
            {"id": 5, "name": "Bundesliga", "country": "Germany", "tier": "1", "kind": "domestic_1", "format": "round_robin", "expected_count": 18, "rank": 4},
            {"id": 6, "name": "Ligue 1", "country": "France", "tier": "1", "kind": "domestic_1", "format": "round_robin", "expected_count": 18, "rank": 5},
            {"id": 2, "name": "Liga Portugal", "country": "Portugal", "tier": "1", "kind": "domestic_1", "format": "round_robin", "expected_count": 18, "rank": 6},
            {"id": 10, "name": "Eredivisie", "country": "Netherlands", "tier": "1", "kind": "domestic_1", "format": "round_robin", "expected_count": 18, "rank": 7},
            {"id": 11, "name": "Süper Lig", "country": "Turkey", "tier": "1", "kind": "domestic_1", "format": "round_robin", "expected_count": 18, "rank": 8},
            {"id": 14, "name": "Pro League", "country": "Belgium", "tier": "1", "kind": "domestic_1", "format": "round_robin", "expected_count": 16, "rank": 9},
            {"id": 15, "name": "Super League", "country": "Switzerland", "tier": "1", "kind": "domestic_1", "format": "round_robin", "expected_count": 12, "rank": 10},
            {"id": 13, "name": "Scottish Premiership", "country": "Scotland", "tier": "1", "kind": "domestic_1", "format": "round_robin", "expected_count": 12, "rank": 11},
            {"id": 24, "name": "Super League Greece", "country": "Greece", "tier": "1", "kind": "domestic_1", "format": "round_robin", "expected_count": 14, "rank": 12},
            {"id": 25, "name": "Ekstraklasa", "country": "Poland", "tier": "1", "kind": "domestic_1", "format": "round_robin", "expected_count": 18, "rank": 13},
            {"id": 23, "name": "Superliga", "country": "Romania", "tier": "1", "kind": "domestic_1", "format": "round_robin", "expected_count": 16, "rank": 14},
            {"id": 54, "name": "Eliteserien", "country": "Norway", "tier": "1", "kind": "domestic_1", "format": "round_robin", "expected_count": 16, "rank": 15},
            {"id": 26, "name": "Allsvenskan", "country": "Sweden", "tier": "1", "kind": "domestic_1", "format": "round_robin", "expected_count": 16, "rank": 16},
            {"id": 84, "name": "Superliga", "country": "Denmark", "tier": "1", "kind": "domestic_1", "format": "round_robin", "expected_count": 12, "rank": 17},
            {"id": 99, "name": "Czech First League", "country": "Czechia", "tier": "1", "kind": "domestic_1", "format": "round_robin", "expected_count": 16, "rank": 18},
            {"id": 22, "name": "Parva Liga", "country": "Bulgaria", "tier": "1", "kind": "domestic_1", "format": "round_robin", "expected_count": 16, "rank": 19},
            {"id": 55, "name": "Veikkausliiga", "country": "Finland", "tier": "1", "kind": "domestic_1", "format": "round_robin", "expected_count": 12, "rank": 20},
            {"id": 7, "name": "Champions League", "country": None, "tier": "2", "kind": "uefa_club", "format": "groups", "expected_count": None, "rank": 21},
            {"id": 8, "name": "Europa League", "country": None, "tier": "2", "kind": "uefa_club", "format": "groups", "expected_count": None, "rank": 22},
            {"id": 83, "name": "Conference League", "country": None, "tier": "2", "kind": "uefa_club", "format": "groups", "expected_count": None, "rank": 23},
            {"id": 90, "name": "UEFA Super Cup", "country": None, "tier": "2", "kind": "uefa_club", "format": "knockout", "expected_count": None, "rank": 24},
            {"id": 12, "name": "Championship", "country": "England", "tier": "3", "kind": "domestic_2", "format": "round_robin", "expected_count": 24, "rank": 25},
            {"id": 38, "name": "Segunda División", "country": "Spain", "tier": "3", "kind": "domestic_2", "format": "round_robin", "expected_count": 22, "rank": 26},
            {"id": 100, "name": "Serie B", "country": "Italy", "tier": "3", "kind": "domestic_2", "format": "round_robin", "expected_count": 20, "rank": 27},
            {"id": 94, "name": "2. Bundesliga", "country": "Germany", "tier": "3", "kind": "domestic_2", "format": "round_robin", "expected_count": 18, "rank": 28},
            {"id": 89, "name": "Ligue 2", "country": "France", "tier": "3", "kind": "domestic_2", "format": "round_robin", "expected_count": 18, "rank": 29},
            {"id": 88, "name": "Liga Portugal 2", "country": "Portugal", "tier": "3", "kind": "domestic_2", "format": "round_robin", "expected_count": 18, "rank": 30},
            {"id": 101, "name": "Serie C Girone A", "country": "Italy", "tier": "3", "kind": "domestic_2", "format": "round_robin", "expected_count": 20, "rank": 31},
            {"id": 102, "name": "Serie C Girone B", "country": "Italy", "tier": "3", "kind": "domestic_2", "format": "round_robin", "expected_count": 20, "rank": 32},
            {"id": 103, "name": "Serie C Girone C", "country": "Italy", "tier": "3", "kind": "domestic_2", "format": "round_robin", "expected_count": 20, "rank": 33},
            {"id": 9, "name": "Brasileirão Série A", "country": "Brazil", "tier": "4", "kind": "world", "format": "round_robin", "expected_count": 20, "rank": 34},
            {"id": 17, "name": "Saudi Pro League", "country": "Saudi Arabia", "tier": "4", "kind": "world", "format": "round_robin", "expected_count": 18, "rank": 35},
            {"id": 18, "name": "Major League Soccer", "country": "USA", "tier": "4", "kind": "world", "format": "groups", "expected_count": None, "rank": 36},
            {"id": 19, "name": "Liga MX Apertura", "country": "Mexico", "tier": "4", "kind": "world", "format": "round_robin", "expected_count": 18, "rank": 37},
            {"id": 20, "name": "Liga MX Clausura", "country": "Mexico", "tier": "4", "kind": "world", "format": "round_robin", "expected_count": 18, "rank": 38},
            {"id": 49, "name": "J1 League", "country": "Japan", "tier": "4", "kind": "world", "format": "round_robin", "expected_count": 20, "rank": 39},
            {"id": 50, "name": "K League 1", "country": "South Korea", "tier": "4", "kind": "world", "format": "round_robin", "expected_count": 12, "rank": 40},
            {"id": 52, "name": "Chinese Super League", "country": "China", "tier": "4", "kind": "world", "format": "round_robin", "expected_count": 16, "rank": 41},
            {"id": 53, "name": "Botola Pro", "country": "Morocco", "tier": "4", "kind": "world", "format": "round_robin", "expected_count": 16, "rank": 42},
            {"id": 47, "name": "Ligue 1 Tunisie", "country": "Tunisia", "tier": "4", "kind": "world", "format": "round_robin", "expected_count": 16, "rank": 43},
            {"id": 80, "name": "Primera A", "country": "Colombia", "tier": "4", "kind": "world", "format": "groups", "expected_count": None, "rank": 44},
            {"id": 85, "name": "Liga Profesional", "country": "Argentina", "tier": "4", "kind": "world", "format": "groups", "expected_count": None, "rank": 45},
            {"id": 32, "name": "Copa Libertadores", "country": None, "tier": "4", "kind": "world", "format": "groups", "expected_count": None, "rank": 46},
            {"id": 33, "name": "Copa Sudamericana", "country": None, "tier": "4", "kind": "world", "format": "groups", "expected_count": None, "rank": 47},
            {"id": 104, "name": "A-League Men", "country": "Australia", "tier": "4", "kind": "world", "format": "round_robin", "expected_count": 13, "rank": 48},
            {"id": 27, "name": "Coupe du monde 2026", "country": None, "tier": "selection", "kind": "selection", "format": "groups", "expected_count": None, "rank": 49},
            {"id": 58, "name": "Qualifs CM — UEFA", "country": None, "tier": "selection", "kind": "selection", "format": "groups", "expected_count": None, "rank": 50},
            {"id": 59, "name": "Qualifs CM — CONMEBOL", "country": None, "tier": "selection", "kind": "selection", "format": "groups", "expected_count": None, "rank": 51},
            {"id": 62, "name": "Qualifs CM — CONCACAF", "country": None, "tier": "selection", "kind": "selection", "format": "groups", "expected_count": None, "rank": 52},
            {"id": 63, "name": "Qualifs CM — OFC", "country": None, "tier": "selection", "kind": "selection", "format": "groups", "expected_count": None, "rank": 53},
            {"id": 64, "name": "Nations League — UEFA", "country": None, "tier": "selection", "kind": "selection", "format": "groups", "expected_count": None, "rank": 54},
            {"id": 65, "name": "Nations League — CONCACAF", "country": None, "tier": "selection", "kind": "selection", "format": "groups", "expected_count": None, "rank": 55},
            {"id": 66, "name": "Euro 2024", "country": None, "tier": "selection", "kind": "selection", "format": "groups", "expected_count": None, "rank": 56},
            {"id": 67, "name": "Copa América 2024", "country": None, "tier": "selection", "kind": "selection", "format": "groups", "expected_count": None, "rank": 57},
        ],
    )


def downgrade() -> None:
    op.drop_index("ix_data_coverage_runs_league_api_id", table_name="data_coverage_runs")
    op.drop_table("data_coverage_runs")
    op.drop_table("leagues")
    op.drop_column("canonical_teams", "country")
