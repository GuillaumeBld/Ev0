"""Add autopilot_decisions table

Revision ID: 008
Revises: 007
Create Date: 2026-03-01

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "008"
down_revision: str | None = "007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "autopilot_decisions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("recommendation_id", sa.Integer(), sa.ForeignKey("recommendations.id", ondelete="SET NULL"), nullable=True),
        sa.Column("features_json", sa.Text(), nullable=False),
        sa.Column("action_idx", sa.Integer(), nullable=False),
        sa.Column("kelly_fraction", sa.Float(), nullable=False),
        sa.Column("stake", sa.Float(), nullable=False),
        sa.Column("best_odds", sa.Float(), nullable=False, server_default="1.0"),
        sa.Column("result", sa.String(20), nullable=True),
        sa.Column("pnl", sa.Float(), nullable=True),
        sa.Column("reward", sa.Float(), nullable=True),
        sa.Column("trained_on", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("mode", sa.String(20), nullable=False, server_default="paper"),
        sa.Column("player_name", sa.String(200), nullable=False, server_default=""),
        sa.Column("fixture_name", sa.String(300), nullable=False, server_default=""),
        sa.Column("market_type", sa.String(50), nullable=False, server_default=""),
        sa.Column("league", sa.String(100), nullable=False, server_default=""),
        sa.Column("settled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_utc", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_autopilot_decisions_recommendation_id", "autopilot_decisions", ["recommendation_id"])
    op.create_index("ix_autopilot_decisions_created_utc", "autopilot_decisions", ["created_utc"])
    op.create_index("ix_autopilot_unsettled", "autopilot_decisions", ["result", "mode"])


def downgrade() -> None:
    op.drop_index("ix_autopilot_unsettled")
    op.drop_index("ix_autopilot_decisions_created_utc")
    op.drop_index("ix_autopilot_decisions_recommendation_id")
    op.drop_table("autopilot_decisions")
