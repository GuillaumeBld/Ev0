"""Add bankroll_entries table

Revision ID: 004
Revises: 003
Create Date: 2026-02-22

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = '004'
down_revision: Union[str, None] = '003'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'bankroll_entries',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('entry_type', sa.String(20), nullable=False),
        sa.Column('amount', sa.Float(), nullable=False),
        sa.Column('balance_after', sa.Float(), nullable=False),
        sa.Column('recommendation_id', sa.Integer(), nullable=True),
        sa.Column('stake', sa.Float(), nullable=True),
        sa.Column('notes', sa.Text(), nullable=True),
        sa.Column('transacted_utc', sa.DateTime(timezone=True), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(['recommendation_id'], ['recommendations.id']),
    )
    op.create_index('ix_bankroll_entries_entry_type', 'bankroll_entries', ['entry_type'])
    op.create_index('ix_bankroll_entries_recommendation_id', 'bankroll_entries', ['recommendation_id'])
    op.create_index('ix_bankroll_entries_transacted_utc', 'bankroll_entries', ['transacted_utc'])


def downgrade() -> None:
    op.drop_index('ix_bankroll_entries_transacted_utc')
    op.drop_index('ix_bankroll_entries_recommendation_id')
    op.drop_index('ix_bankroll_entries_entry_type')
    op.drop_table('bankroll_entries')
