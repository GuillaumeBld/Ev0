"""Stats de sélection nationale, isolées de bzz_player_season_stats.

Table dédiée pour que le complément Transfermarkt (Temps 2) soit ajouté ou
retiré par `source` sans toucher aux données de club. L'équipe nationale
(`national_team_api_id`) est dérivée de la nationalité du joueur (espace A :
même identifiant que canonical_teams.bzz_team_id), pas du team_api_id des
stats de match (vide à 59 % sur la Coupe du monde).
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import Float, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class PlayerSelectionSeasonStat(Base, TimestampMixin):
    __tablename__ = "player_selection_season_stats"
    __table_args__ = (
        UniqueConstraint(
            "player_api_id", "national_team_api_id", "league_api_id", "season", "source",
            name="uq_selection_player_team_league_season_source",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    player_api_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    national_team_api_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    league_api_id: Mapped[int] = mapped_column(Integer, nullable=False)
    season: Mapped[str] = mapped_column(String(10), nullable=False)
    source: Mapped[str] = mapped_column(String(16), nullable=False, default="bzzoiro")
    as_of_utc: Mapped[datetime | None] = mapped_column(nullable=True)

    # --- Mêmes colonnes de stats que BzzPlayerSeasonStat (voir bzzoiro.py) ---
    matches_played: Mapped[int | None] = mapped_column(Integer)
    minutes_played: Mapped[int | None] = mapped_column(Integer)
    starts: Mapped[int | None] = mapped_column(Integer)
    goals: Mapped[int | None] = mapped_column(Integer)
    goal_assist: Mapped[int | None] = mapped_column(Integer)
    total_shots: Mapped[int | None] = mapped_column(Integer)
    shots_on_target: Mapped[int | None] = mapped_column(Integer)
    key_pass: Mapped[int | None] = mapped_column(Integer)
    total_cross: Mapped[int | None] = mapped_column(Integer)
    accurate_cross: Mapped[int | None] = mapped_column(Integer)
    total_pass: Mapped[int | None] = mapped_column(Integer)
    accurate_pass: Mapped[int | None] = mapped_column(Integer)
    total_long_balls: Mapped[int | None] = mapped_column(Integer)
    accurate_long_balls: Mapped[int | None] = mapped_column(Integer)
    duel_won: Mapped[int | None] = mapped_column(Integer)
    duel_lost: Mapped[int | None] = mapped_column(Integer)
    aerial_won: Mapped[int | None] = mapped_column(Integer)
    aerial_lost: Mapped[int | None] = mapped_column(Integer)
    total_tackle: Mapped[int | None] = mapped_column(Integer)
    won_tackle: Mapped[int | None] = mapped_column(Integer)
    interception: Mapped[int | None] = mapped_column(Integer)
    ball_recovery: Mapped[int | None] = mapped_column(Integer)
    yellow_card: Mapped[int | None] = mapped_column(Integer)
    red_card: Mapped[int | None] = mapped_column(Integer)
    saves: Mapped[int | None] = mapped_column(Integer)
    expected_goals: Mapped[float | None] = mapped_column(Float)
    expected_assists: Mapped[float | None] = mapped_column(Float)
    xg_per_90: Mapped[float | None] = mapped_column(Float)
    xa_per_90: Mapped[float | None] = mapped_column(Float)
    shots_per_90: Mapped[float | None] = mapped_column(Float)
    shots_on_target_per_90: Mapped[float | None] = mapped_column(Float)
    key_pass_per_90: Mapped[float | None] = mapped_column(Float)
    accurate_cross_per_90: Mapped[float | None] = mapped_column(Float)
    recoveries_per_90: Mapped[float | None] = mapped_column(Float)
    tackles_per_90: Mapped[float | None] = mapped_column(Float)
    interceptions_per_90: Mapped[float | None] = mapped_column(Float)
    shot_accuracy: Mapped[float | None] = mapped_column(Float)
    xg_per_shot: Mapped[float | None] = mapped_column(Float)
    finishing_delta: Mapped[float | None] = mapped_column(Float)
    xa_delta: Mapped[float | None] = mapped_column(Float)
    pass_completion: Mapped[float | None] = mapped_column(Float)
    long_ball_accuracy: Mapped[float | None] = mapped_column(Float)
    cross_accuracy: Mapped[float | None] = mapped_column(Float)
    duel_win_rate: Mapped[float | None] = mapped_column(Float)
    aerial_win_rate: Mapped[float | None] = mapped_column(Float)
    tackle_success_rate: Mapped[float | None] = mapped_column(Float)
    avg_rating: Mapped[float | None] = mapped_column(Float)
    avg_minutes_per_match: Mapped[float | None] = mapped_column(Float)
    starts_pct: Mapped[float | None] = mapped_column(Float)
    form_xg_5: Mapped[float | None] = mapped_column(Float)
    form_rating_5: Mapped[float | None] = mapped_column(Float)
    form_goals_5: Mapped[int | None] = mapped_column(Integer)
    form_assists_5: Mapped[int | None] = mapped_column(Integer)
    rating_trend: Mapped[float | None] = mapped_column(Float)
