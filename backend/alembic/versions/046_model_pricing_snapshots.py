"""model_pricing_snapshots : registre Alpha/Beta, prix pré-match figés au coup d'envoi.

Revision ID: 046
Revises: 045
Create Date: 2026-07-18
"""
import sqlalchemy as sa

from alembic import op

revision = "046"
down_revision = "045"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "model_pricing_snapshots",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("model_name", sa.String(20), nullable=False, index=True),
        sa.Column(
            "fixture_id",
            sa.Integer(),
            sa.ForeignKey("fixtures.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("player_api_id", sa.Integer(), nullable=False, index=True),
        sa.Column("player_name", sa.String(200), nullable=False),
        sa.Column("market", sa.String(30), nullable=False),
        sa.Column("probability", sa.Float(), nullable=False),
        sa.Column("fair_odds", sa.Float(), nullable=False),
        sa.Column("as_of_utc", sa.DateTime(timezone=True), nullable=False),
        sa.Column("frozen", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint(
            "fixture_id", "player_api_id", "market", "model_name",
            name="uq_model_pricing_snapshot",
        ),
    )


def downgrade() -> None:
    op.drop_table("model_pricing_snapshots")
