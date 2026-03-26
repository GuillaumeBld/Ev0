"""add lineup tables and player is_striker

Revision ID: 013
Revises: 012
Create Date: 2026-03-26
"""
import sqlalchemy as sa

from alembic import op

revision = "013"
down_revision = "012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Champ is_striker sur players
    op.add_column(
        "players",
        sa.Column("is_striker", sa.Boolean(), nullable=False, server_default="false"),
    )

    # Table team_lineups
    op.create_table(
        "team_lineups",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("fixture_id", sa.Integer(), sa.ForeignKey("fixtures.id"), nullable=False),
        sa.Column("team", sa.String(200), nullable=False),
        sa.Column("lineup_type", sa.String(20), nullable=False),
        sa.Column("source", sa.String(50), nullable=False, server_default="manual"),
        sa.Column("created_by", sa.String(100), nullable=False, server_default="system"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("fixture_id", "team", "lineup_type", name="uq_team_lineup"),
    )
    op.create_index("ix_team_lineups_fixture_id", "team_lineups", ["fixture_id"])
    op.create_index("ix_team_lineups_team", "team_lineups", ["team"])

    # Table team_lineup_players
    op.create_table(
        "team_lineup_players",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("lineup_id", sa.Integer(), sa.ForeignKey("team_lineups.id"), nullable=False),
        sa.Column("player_name", sa.String(200), nullable=False),
        sa.Column("position", sa.String(10), nullable=False),
        sa.Column("is_starter", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("jersey_number", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_team_lineup_players_lineup_id", "team_lineup_players", ["lineup_id"])


def downgrade() -> None:
    op.drop_table("team_lineup_players")
    op.drop_table("team_lineups")
    op.drop_column("players", "is_striker")
