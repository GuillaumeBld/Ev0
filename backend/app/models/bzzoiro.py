"""Bzzoiro Sports Data API models."""

from datetime import date, datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class BzzLeague(Base, TimestampMixin):
    """A football league/competition from Bzzoiro."""

    __tablename__ = "bzz_leagues"

    id: Mapped[int] = mapped_column(primary_key=True)
    api_id: Mapped[int] = mapped_column(Integer, unique=True, index=True, nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    country: Mapped[str | None] = mapped_column(String(100), nullable=True)
    season_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class BzzTeam(Base, TimestampMixin):
    """A football team from Bzzoiro."""

    __tablename__ = "bzz_teams"

    id: Mapped[int] = mapped_column(primary_key=True)
    api_id: Mapped[int] = mapped_column(Integer, unique=True, index=True, nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    short_name: Mapped[str | None] = mapped_column(String(50), nullable=True)
    country: Mapped[str | None] = mapped_column(String(100), nullable=True)
    synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class BzzPlayer(Base, TimestampMixin):
    """A football player from Bzzoiro."""

    __tablename__ = "bzz_players"

    id: Mapped[int] = mapped_column(primary_key=True)
    api_id: Mapped[int] = mapped_column(Integer, unique=True, index=True, nullable=False)
    internal_id: Mapped[int | None] = mapped_column(Integer, unique=True, index=True, nullable=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    short_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    nationality: Mapped[str | None] = mapped_column(String(100), nullable=True)
    date_of_birth: Mapped[date | None] = mapped_column(nullable=True)
    height: Mapped[int | None] = mapped_column(Integer, nullable=True)  # cm
    jersey_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    position: Mapped[str | None] = mapped_column(String(1), nullable=True)  # G/D/M/F
    market_value: Mapped[int | None] = mapped_column(BigInteger, nullable=True)  # EUR
    current_team_api_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("bzz_teams.api_id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    national_team_api_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("bzz_teams.api_id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class BzzEvent(Base, TimestampMixin):
    """A football match event from Bzzoiro."""

    __tablename__ = "bzz_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    api_id: Mapped[int] = mapped_column(Integer, unique=True, index=True, nullable=False)
    league_api_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("bzz_leagues.api_id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    home_team_api_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("bzz_teams.api_id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    away_team_api_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("bzz_teams.api_id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    event_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    status: Mapped[str | None] = mapped_column(String(50), nullable=True)
    period: Mapped[str | None] = mapped_column(String(20), nullable=True)
    current_minute: Mapped[int | None] = mapped_column(Integer, nullable=True)
    round_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    home_score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    away_score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    home_score_ht: Mapped[int | None] = mapped_column(Integer, nullable=True)
    away_score_ht: Mapped[int | None] = mapped_column(Integer, nullable=True)
    home_xg: Mapped[float | None] = mapped_column(Float, nullable=True)
    away_xg: Mapped[float | None] = mapped_column(Float, nullable=True)

    # JSONB blobs
    shotmap: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    incidents: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    momentum: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    average_positions: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    lineups: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    odds_1x2: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    odds_over_under: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    odds_btts: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class BzzPlayerMatchStat(Base, TimestampMixin):
    """Per-match player statistics from Bzzoiro."""

    __tablename__ = "bzz_player_match_stats"

    __table_args__ = (
        UniqueConstraint("player_api_id", "event_api_id", name="uq_bzz_player_match"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    player_api_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("bzz_players.api_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    event_api_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("bzz_events.api_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    team_api_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("bzz_teams.api_id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    is_home: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    minutes_played: Mapped[int | None] = mapped_column(Integer, nullable=True)
    rating: Mapped[float | None] = mapped_column(Float, nullable=True)
    touches: Mapped[int | None] = mapped_column(Integer, nullable=True)
    goals: Mapped[int | None] = mapped_column(Integer, nullable=True)
    goal_assist: Mapped[int | None] = mapped_column(Integer, nullable=True)
    expected_goals: Mapped[float | None] = mapped_column(Float, nullable=True)
    expected_assists: Mapped[float | None] = mapped_column(Float, nullable=True)
    total_shots: Mapped[int | None] = mapped_column(Integer, nullable=True)
    shots_on_target: Mapped[int | None] = mapped_column(Integer, nullable=True)
    total_pass: Mapped[int | None] = mapped_column(Integer, nullable=True)
    accurate_pass: Mapped[int | None] = mapped_column(Integer, nullable=True)
    key_pass: Mapped[int | None] = mapped_column(Integer, nullable=True)
    total_long_balls: Mapped[int | None] = mapped_column(Integer, nullable=True)
    accurate_long_balls: Mapped[int | None] = mapped_column(Integer, nullable=True)
    total_cross: Mapped[int | None] = mapped_column(Integer, nullable=True)
    accurate_cross: Mapped[int | None] = mapped_column(Integer, nullable=True)
    duel_won: Mapped[int | None] = mapped_column(Integer, nullable=True)
    duel_lost: Mapped[int | None] = mapped_column(Integer, nullable=True)
    aerial_won: Mapped[int | None] = mapped_column(Integer, nullable=True)
    aerial_lost: Mapped[int | None] = mapped_column(Integer, nullable=True)
    total_tackle: Mapped[int | None] = mapped_column(Integer, nullable=True)
    won_tackle: Mapped[int | None] = mapped_column(Integer, nullable=True)
    total_clearance: Mapped[int | None] = mapped_column(Integer, nullable=True)
    interception: Mapped[int | None] = mapped_column(Integer, nullable=True)
    ball_recovery: Mapped[int | None] = mapped_column(Integer, nullable=True)
    yellow_card: Mapped[int | None] = mapped_column(Integer, nullable=True)
    red_card: Mapped[int | None] = mapped_column(Integer, nullable=True)
    fouls: Mapped[int | None] = mapped_column(Integer, nullable=True)
    was_fouled: Mapped[int | None] = mapped_column(Integer, nullable=True)
    dispossessed: Mapped[int | None] = mapped_column(Integer, nullable=True)
    possession_lost: Mapped[int | None] = mapped_column(Integer, nullable=True)
    saves: Mapped[int | None] = mapped_column(Integer, nullable=True)
    goals_conceded: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # Derived fields
    shot_accuracy: Mapped[float | None] = mapped_column(Float, nullable=True)
    xg_per_shot: Mapped[float | None] = mapped_column(Float, nullable=True)
    finishing_delta: Mapped[float | None] = mapped_column(Float, nullable=True)
    xa_delta: Mapped[float | None] = mapped_column(Float, nullable=True)
    pass_completion: Mapped[float | None] = mapped_column(Float, nullable=True)
    long_ball_accuracy: Mapped[float | None] = mapped_column(Float, nullable=True)
    cross_accuracy: Mapped[float | None] = mapped_column(Float, nullable=True)
    duel_win_rate: Mapped[float | None] = mapped_column(Float, nullable=True)
    aerial_win_rate: Mapped[float | None] = mapped_column(Float, nullable=True)
    tackle_success_rate: Mapped[float | None] = mapped_column(Float, nullable=True)


class BzzPlayerSeasonStat(Base, TimestampMixin):
    """Aggregated season statistics for a player from Bzzoiro."""

    __tablename__ = "bzz_player_season_stats"

    __table_args__ = (
        UniqueConstraint("player_api_id", "league_api_id", "season", name="uq_bzz_player_season"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    player_api_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("bzz_players.api_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    league_api_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("bzz_leagues.api_id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    season: Mapped[str] = mapped_column(String(10), nullable=False)
    as_of_utc: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Totals (Integer)
    matches_played: Mapped[int | None] = mapped_column(Integer, nullable=True)
    minutes_played: Mapped[int | None] = mapped_column(Integer, nullable=True)
    starts: Mapped[int | None] = mapped_column(Integer, nullable=True)
    goals: Mapped[int | None] = mapped_column(Integer, nullable=True)
    goal_assist: Mapped[int | None] = mapped_column(Integer, nullable=True)
    total_shots: Mapped[int | None] = mapped_column(Integer, nullable=True)
    shots_on_target: Mapped[int | None] = mapped_column(Integer, nullable=True)
    key_pass: Mapped[int | None] = mapped_column(Integer, nullable=True)
    total_cross: Mapped[int | None] = mapped_column(Integer, nullable=True)
    accurate_cross: Mapped[int | None] = mapped_column(Integer, nullable=True)
    total_pass: Mapped[int | None] = mapped_column(Integer, nullable=True)
    accurate_pass: Mapped[int | None] = mapped_column(Integer, nullable=True)
    total_long_balls: Mapped[int | None] = mapped_column(Integer, nullable=True)
    accurate_long_balls: Mapped[int | None] = mapped_column(Integer, nullable=True)
    duel_won: Mapped[int | None] = mapped_column(Integer, nullable=True)
    duel_lost: Mapped[int | None] = mapped_column(Integer, nullable=True)
    aerial_won: Mapped[int | None] = mapped_column(Integer, nullable=True)
    aerial_lost: Mapped[int | None] = mapped_column(Integer, nullable=True)
    total_tackle: Mapped[int | None] = mapped_column(Integer, nullable=True)
    won_tackle: Mapped[int | None] = mapped_column(Integer, nullable=True)
    interception: Mapped[int | None] = mapped_column(Integer, nullable=True)
    ball_recovery: Mapped[int | None] = mapped_column(Integer, nullable=True)
    yellow_card: Mapped[int | None] = mapped_column(Integer, nullable=True)
    red_card: Mapped[int | None] = mapped_column(Integer, nullable=True)
    saves: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # Totals (Float)
    expected_goals: Mapped[float | None] = mapped_column(Float, nullable=True)
    expected_assists: Mapped[float | None] = mapped_column(Float, nullable=True)

    # Per-90 (Float nullable)
    xg_per_90: Mapped[float | None] = mapped_column(Float, nullable=True)
    xa_per_90: Mapped[float | None] = mapped_column(Float, nullable=True)
    shots_per_90: Mapped[float | None] = mapped_column(Float, nullable=True)
    shots_on_target_per_90: Mapped[float | None] = mapped_column(Float, nullable=True)
    key_pass_per_90: Mapped[float | None] = mapped_column(Float, nullable=True)
    accurate_cross_per_90: Mapped[float | None] = mapped_column(Float, nullable=True)
    recoveries_per_90: Mapped[float | None] = mapped_column(Float, nullable=True)
    tackles_per_90: Mapped[float | None] = mapped_column(Float, nullable=True)
    interceptions_per_90: Mapped[float | None] = mapped_column(Float, nullable=True)

    # Efficiency (Float nullable)
    shot_accuracy: Mapped[float | None] = mapped_column(Float, nullable=True)
    xg_per_shot: Mapped[float | None] = mapped_column(Float, nullable=True)
    finishing_delta: Mapped[float | None] = mapped_column(Float, nullable=True)
    xa_delta: Mapped[float | None] = mapped_column(Float, nullable=True)
    pass_completion: Mapped[float | None] = mapped_column(Float, nullable=True)
    long_ball_accuracy: Mapped[float | None] = mapped_column(Float, nullable=True)
    cross_accuracy: Mapped[float | None] = mapped_column(Float, nullable=True)
    duel_win_rate: Mapped[float | None] = mapped_column(Float, nullable=True)
    aerial_win_rate: Mapped[float | None] = mapped_column(Float, nullable=True)
    tackle_success_rate: Mapped[float | None] = mapped_column(Float, nullable=True)
    avg_rating: Mapped[float | None] = mapped_column(Float, nullable=True)

    # Profile (Float nullable)
    avg_minutes_per_match: Mapped[float | None] = mapped_column(Float, nullable=True)
    starts_pct: Mapped[float | None] = mapped_column(Float, nullable=True)

    # Form (nullable)
    form_xg_5: Mapped[float | None] = mapped_column(Float, nullable=True)
    form_rating_5: Mapped[float | None] = mapped_column(Float, nullable=True)
    form_goals_5: Mapped[int | None] = mapped_column(Integer, nullable=True)
    form_assists_5: Mapped[int | None] = mapped_column(Integer, nullable=True)
    rating_trend: Mapped[float | None] = mapped_column(Float, nullable=True)


class BzzPrediction(Base, TimestampMixin):
    """Match prediction data from Bzzoiro."""

    __tablename__ = "bzz_predictions"

    id: Mapped[int] = mapped_column(primary_key=True)
    event_api_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("bzz_events.api_id", ondelete="CASCADE"),
        unique=True,
        nullable=False,
        index=True,
    )
    created_at_bzz: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    prob_home_win: Mapped[float | None] = mapped_column(Float, nullable=True)
    prob_draw: Mapped[float | None] = mapped_column(Float, nullable=True)
    prob_away_win: Mapped[float | None] = mapped_column(Float, nullable=True)
    predicted_result: Mapped[str | None] = mapped_column(String(1), nullable=True)
    expected_home_goals: Mapped[float | None] = mapped_column(Float, nullable=True)
    expected_away_goals: Mapped[float | None] = mapped_column(Float, nullable=True)
    prob_over_15: Mapped[float | None] = mapped_column(Float, nullable=True)
    prob_over_25: Mapped[float | None] = mapped_column(Float, nullable=True)
    prob_over_35: Mapped[float | None] = mapped_column(Float, nullable=True)
    prob_btts_yes: Mapped[float | None] = mapped_column(Float, nullable=True)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    model_version: Mapped[str | None] = mapped_column(String(50), nullable=True)
    most_likely_score: Mapped[str | None] = mapped_column(String(10), nullable=True)
    favorite: Mapped[str | None] = mapped_column(String(1), nullable=True)
    favorite_prob: Mapped[float | None] = mapped_column(Float, nullable=True)

    # Booleans (nullable)
    favorite_recommend: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    over_15_recommend: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    over_25_recommend: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    over_35_recommend: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    btts_recommend: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    winner_recommend: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
