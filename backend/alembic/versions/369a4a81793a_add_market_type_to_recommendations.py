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
    op.execute("""
        DO $$
        BEGIN
            -- 1. Créer le type ENUM si absent
            IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'markettype') THEN
                CREATE TYPE markettype AS ENUM ('standard', 'supersub');
            END IF;

            -- 2. Ajouter market_type si absent
            IF NOT EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_name = 'recommendations' AND column_name = 'market_type'
            ) THEN
                ALTER TABLE recommendations
                    ADD COLUMN market_type markettype NOT NULL DEFAULT 'standard';
            END IF;

            -- 3. Ajouter bet_type si absent
            IF NOT EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_name = 'recommendations' AND column_name = 'bet_type'
            ) THEN
                ALTER TABLE recommendations
                    ADD COLUMN bet_type VARCHAR(20) NOT NULL DEFAULT 'goal';
            END IF;

            -- 4. Supprimer l'ancienne contrainte unique si elle existe
            IF EXISTS (
                SELECT 1 FROM information_schema.table_constraints
                WHERE table_name = 'recommendations'
                  AND constraint_name = 'uq_recommendation_fixture_player_market'
                  AND constraint_type = 'UNIQUE'
            ) THEN
                ALTER TABLE recommendations
                    DROP CONSTRAINT uq_recommendation_fixture_player_market;
            END IF;

            -- 5. Créer la nouvelle contrainte unique si elle n'existe pas
            IF NOT EXISTS (
                SELECT 1 FROM information_schema.table_constraints
                WHERE table_name = 'recommendations'
                  AND constraint_name = 'uq_recommendation_fixture_player_market_bet'
                  AND constraint_type = 'UNIQUE'
            ) THEN
                ALTER TABLE recommendations
                    ADD CONSTRAINT uq_recommendation_fixture_player_market_bet
                    UNIQUE (fixture_id, player_name, market_type, bet_type);
            END IF;
        END
        $$;
    """)


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
