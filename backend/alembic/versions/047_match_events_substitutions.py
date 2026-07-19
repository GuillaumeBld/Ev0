"""match_events.related_player_name : sortant d'une substitution (settlement avec-sub).

Revision ID: 047
Revises: 046
Create Date: 2026-07-19
"""
import sqlalchemy as sa

from alembic import op

revision = "047"
down_revision = "046"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "match_events",
        sa.Column("related_player_name", sa.String(200), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("match_events", "related_player_name")
