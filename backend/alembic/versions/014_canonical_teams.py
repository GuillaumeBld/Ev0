"""canonical_teams table + FK on players and fixtures

Revision ID: 014
Revises: 013
Create Date: 2026-03-28
"""
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import ARRAY

from alembic import op

revision = "014"
down_revision = "013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── 1. canonical_teams ────────────────────────────────────────
    op.create_table(
        "canonical_teams",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name_fr", sa.String(200), nullable=False, unique=True),
        sa.Column("aliases", ARRAY(sa.Text()), nullable=False, server_default="{}"),
    )

    # ── 2. players.canonical_team_id ──────────────────────────────
    op.add_column(
        "players",
        sa.Column(
            "canonical_team_id",
            sa.Integer(),
            sa.ForeignKey("canonical_teams.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.create_index("ix_players_canonical_team_id", "players", ["canonical_team_id"])

    # ── 3. fixtures canonical team FKs ────────────────────────────
    op.add_column(
        "fixtures",
        sa.Column(
            "home_canonical_team_id",
            sa.Integer(),
            sa.ForeignKey("canonical_teams.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.add_column(
        "fixtures",
        sa.Column(
            "away_canonical_team_id",
            sa.Integer(),
            sa.ForeignKey("canonical_teams.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.create_index("ix_fixtures_home_canonical_team_id", "fixtures", ["home_canonical_team_id"])
    op.create_index("ix_fixtures_away_canonical_team_id", "fixtures", ["away_canonical_team_id"])


def downgrade() -> None:
    op.drop_index("ix_fixtures_away_canonical_team_id", "fixtures")
    op.drop_index("ix_fixtures_home_canonical_team_id", "fixtures")
    op.drop_column("fixtures", "away_canonical_team_id")
    op.drop_column("fixtures", "home_canonical_team_id")
    op.drop_index("ix_players_canonical_team_id", "players")
    op.drop_column("players", "canonical_team_id")
    op.drop_table("canonical_teams")
