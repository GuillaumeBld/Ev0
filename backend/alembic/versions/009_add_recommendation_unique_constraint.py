"""Add unique constraint on recommendations(fixture_id, player_name, market_type)

Revision ID: 009
Revises: 008
Create Date: 2026-03-06

Prevents duplicate recommendation rows when GET /recommendations is called
concurrently by multiple clients for the same fixture/player/market combination.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "009"
down_revision: str | None = "008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Remove any existing duplicates before adding the constraint.
    # Keep the row with the highest id (most recently inserted).
    op.execute("""
        DELETE FROM recommendations
        WHERE id NOT IN (
            SELECT MAX(id)
            FROM recommendations
            GROUP BY fixture_id, player_name, market_type
        )
    """)

    op.create_unique_constraint(
        "uq_recommendation_fixture_player_market",
        "recommendations",
        ["fixture_id", "player_name", "market_type"],
    )


def downgrade() -> None:
    op.drop_constraint(
        "uq_recommendation_fixture_player_market",
        "recommendations",
        type_="unique",
    )
