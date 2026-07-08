"""wc2026_player_pricing: marchés plus décisif (G+A) et top 3.

Revision ID: 044
Revises: 043_wc2026_ko_predictions
Create Date: 2026-07-08
"""
import sqlalchemy as sa
from alembic import op

revision = "044"
down_revision = "043_wc2026_ko_predictions"
branch_labels = None
depends_on = None

_COLUMNS = [
    "p_most_decisive",
    "fair_most_decisive",
    "p_top3_decisive",
    "fair_top3_decisive",
    "p_top3_scorer",
    "fair_top3_scorer",
    "p_top3_assister",
    "fair_top3_assister",
]


def upgrade() -> None:
    for col in _COLUMNS:
        op.add_column(
            "wc2026_player_pricing",
            sa.Column(col, sa.Float(), nullable=True),
        )


def downgrade() -> None:
    for col in reversed(_COLUMNS):
        op.drop_column("wc2026_player_pricing", col)
