"""Initial schema

Revision ID: 001
Revises: 
Create Date: 2024-01-31

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = '001'
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Fixtures table
    op.create_table(
        'fixtures',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('external_id', sa.String(100), nullable=False),
        sa.Column('league', sa.String(50), nullable=False),
        sa.Column('season', sa.String(10), nullable=False),
        sa.Column('matchweek', sa.Integer(), nullable=True),
        sa.Column('home_team', sa.String(100), nullable=False),
        sa.Column('away_team', sa.String(100), nullable=False),
        sa.Column('home_team_id', sa.String(100), nullable=True),
        sa.Column('away_team_id', sa.String(100), nullable=True),
        sa.Column('kickoff_utc', sa.DateTime(timezone=True), nullable=False),
        sa.Column('status', sa.String(20), nullable=False, server_default='scheduled'),
        sa.Column('home_score', sa.Integer(), nullable=True),
        sa.Column('away_score', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_fixtures_external_id', 'fixtures', ['external_id'], unique=True)
    op.create_index('ix_fixtures_league', 'fixtures', ['league'])
    op.create_index('ix_fixtures_kickoff_utc', 'fixtures', ['kickoff_utc'])

    # Players table
    op.create_table(
        'players',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('external_id', sa.String(100), nullable=False),
        sa.Column('name', sa.String(200), nullable=False),
        sa.Column('normalized_name', sa.String(200), nullable=False),
        sa.Column('team', sa.String(100), nullable=True),
        sa.Column('team_id', sa.String(100), nullable=True),
        sa.Column('position', sa.String(50), nullable=True),
        sa.Column('nationality', sa.String(100), nullable=True),
        sa.Column('birth_year', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_players_external_id', 'players', ['external_id'], unique=True)
    op.create_index('ix_players_name', 'players', ['name'])
    op.create_index('ix_players_normalized_name', 'players', ['normalized_name'])

    # Player stats table
    op.create_table(
        'player_stats',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('player_id', sa.Integer(), nullable=False),
        sa.Column('as_of_utc', sa.DateTime(timezone=True), nullable=False),
        sa.Column('league', sa.String(50), nullable=False),
        sa.Column('season', sa.String(10), nullable=False),
        sa.Column('matches_played', sa.Integer(), server_default='0', nullable=False),
        sa.Column('minutes_played', sa.Integer(), server_default='0', nullable=False),
        sa.Column('starts', sa.Integer(), server_default='0', nullable=False),
        sa.Column('goals', sa.Integer(), server_default='0', nullable=False),
        sa.Column('npxg', sa.Float(), server_default='0.0', nullable=False),
        sa.Column('xg', sa.Float(), server_default='0.0', nullable=False),
        sa.Column('shots', sa.Integer(), server_default='0', nullable=False),
        sa.Column('shots_on_target', sa.Integer(), server_default='0', nullable=False),
        sa.Column('assists', sa.Integer(), server_default='0', nullable=False),
        sa.Column('xa', sa.Float(), server_default='0.0', nullable=False),
        sa.Column('key_passes', sa.Integer(), server_default='0', nullable=False),
        sa.Column('sca', sa.Integer(), server_default='0', nullable=False),
        sa.Column('passes_completed', sa.Integer(), server_default='0', nullable=False),
        sa.Column('passes_into_penalty_area', sa.Integer(), server_default='0', nullable=False),
        sa.Column('progressive_passes', sa.Integer(), server_default='0', nullable=False),
        sa.Column('crosses', sa.Integer(), server_default='0', nullable=False),
        sa.Column('xg_per_90', sa.Float(), nullable=True),
        sa.Column('xa_per_90', sa.Float(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(['player_id'], ['players.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('player_id', 'as_of_utc', 'league', 'season', name='uq_player_stats_snapshot'),
    )
    op.create_index('ix_player_stats_player_id', 'player_stats', ['player_id'])
    op.create_index('ix_player_stats_as_of_utc', 'player_stats', ['as_of_utc'])

    # Odds snapshots table
    op.create_table(
        'odds_snapshots',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('fixture_id', sa.Integer(), nullable=False),
        sa.Column('player_id', sa.Integer(), nullable=True),
        sa.Column('player_name', sa.String(200), nullable=False),
        sa.Column('market_type', sa.String(50), nullable=False),
        sa.Column('bookmaker', sa.String(50), nullable=False),
        sa.Column('odds', sa.Float(), nullable=False),
        sa.Column('implied_probability', sa.Float(), nullable=False),
        sa.Column('snapshot_utc', sa.DateTime(timezone=True), nullable=False),
        sa.Column('raw_data', postgresql.JSON(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(['fixture_id'], ['fixtures.id']),
        sa.ForeignKeyConstraint(['player_id'], ['players.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('fixture_id', 'player_name', 'market_type', 'bookmaker', 'snapshot_utc', name='uq_odds_snapshot'),
    )
    op.create_index('ix_odds_snapshots_fixture_id', 'odds_snapshots', ['fixture_id'])
    op.create_index('ix_odds_snapshots_market_type', 'odds_snapshots', ['market_type'])
    op.create_index('ix_odds_snapshots_bookmaker', 'odds_snapshots', ['bookmaker'])
    op.create_index('ix_odds_snapshots_snapshot_utc', 'odds_snapshots', ['snapshot_utc'])

    # Recommendations table
    op.create_table(
        'recommendations',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('fixture_id', sa.Integer(), nullable=False),
        sa.Column('player_id', sa.Integer(), nullable=True),
        sa.Column('player_name', sa.String(200), nullable=False),
        sa.Column('market_type', sa.String(50), nullable=False),
        sa.Column('lambda_intensity', sa.Float(), nullable=False),
        sa.Column('fair_probability', sa.Float(), nullable=False),
        sa.Column('fair_odds', sa.Float(), nullable=False),
        sa.Column('best_bookmaker', sa.String(50), nullable=False),
        sa.Column('best_odds', sa.Float(), nullable=False),
        sa.Column('edge', sa.Float(), nullable=False),
        sa.Column('classification', sa.String(20), nullable=False),
        sa.Column('confidence', sa.Float(), nullable=False),
        sa.Column('explanation', postgresql.JSON(), nullable=False),
        sa.Column('status', sa.String(20), server_default='pending', nullable=False),
        sa.Column('operator_notes', sa.Text(), nullable=True),
        sa.Column('result', sa.String(20), nullable=True),
        sa.Column('pnl', sa.Float(), nullable=True),
        sa.Column('generated_utc', sa.DateTime(timezone=True), nullable=False),
        sa.Column('decided_utc', sa.DateTime(timezone=True), nullable=True),
        sa.Column('settled_utc', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(['fixture_id'], ['fixtures.id']),
        sa.ForeignKeyConstraint(['player_id'], ['players.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_recommendations_fixture_id', 'recommendations', ['fixture_id'])
    op.create_index('ix_recommendations_edge', 'recommendations', ['edge'])
    op.create_index('ix_recommendations_generated_utc', 'recommendations', ['generated_utc'])


def downgrade() -> None:
    op.drop_table('recommendations')
    op.drop_table('odds_snapshots')
    op.drop_table('player_stats')
    op.drop_table('players')
    op.drop_table('fixtures')
