"""add_market_type_to_recommendations

Revision ID: 369a4a81793a
Revises: 041
Create Date: 2026-06-13 03:11:12.719198

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '369a4a81793a'
down_revision: Union[str, None] = '041'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("CREATE TYPE IF NOT EXISTS markettype AS ENUM ('standard', 'supersub')")
    op.add_column(
        'recommendations',
        sa.Column(
            'market_type',
            sa.Enum('standard', 'supersub', name='markettype', create_type=False),
            nullable=False,
            server_default='standard',
        )
    )
    # Ajouter bet_type
    op.add_column(
        'recommendations',
        sa.Column('bet_type', sa.String(20), nullable=False, server_default='goal')
    )
    # Remplacer la contrainte unique (drop ancienne si elle existe, créer nouvelle)
    try:
        op.drop_constraint('uq_recommendation_fixture_player_market', 'recommendations', type_='unique')
    except Exception:
        pass
    op.create_unique_constraint(
        'uq_recommendation_fixture_player_market_bet',
        'recommendations',
        ['fixture_id', 'player_name', 'market_type', 'bet_type']
    )


def downgrade() -> None:
    op.drop_constraint('uq_recommendation_fixture_player_market_bet', 'recommendations', type_='unique')
    op.create_unique_constraint(
        'uq_recommendation_fixture_player_market',
        'recommendations',
        ['fixture_id', 'player_name', 'market_type']
    )
    op.drop_column('recommendations', 'bet_type')
    op.drop_column('recommendations', 'market_type')
    op.execute("DROP TYPE IF EXISTS markettype")
