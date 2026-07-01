from __future__ import annotations
from datetime import datetime
from sqlalchemy import Integer, Float, String, DateTime, func
from sqlalchemy.orm import Mapped, mapped_column
from app.db import Base


class WC2026KOPrediction(Base):
    __tablename__ = "wc2026_ko_predictions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    fixture_id: Mapped[int] = mapped_column(Integer, nullable=False, unique=True)
    home_team: Mapped[str] = mapped_column(String, nullable=False)
    away_team: Mapped[str] = mapped_column(String, nullable=False)
    matchweek: Mapped[int | None] = mapped_column(Integer, nullable=True)
    kickoff_utc: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    elo_home: Mapped[float | None] = mapped_column(Float, nullable=True)
    elo_away: Mapped[float | None] = mapped_column(Float, nullable=True)
    elo_prob_home: Mapped[float | None] = mapped_column(Float, nullable=True)

    pin_odds_home: Mapped[float | None] = mapped_column(Float, nullable=True)
    pin_odds_draw: Mapped[float | None] = mapped_column(Float, nullable=True)
    pin_odds_away: Mapped[float | None] = mapped_column(Float, nullable=True)
    pin_prob_home: Mapped[float | None] = mapped_column(Float, nullable=True)

    fr_odds_home: Mapped[float | None] = mapped_column(Float, nullable=True)
    fr_odds_away: Mapped[float | None] = mapped_column(Float, nullable=True)

    home_score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    away_score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    winner: Mapped[str | None] = mapped_column(String, nullable=True)

    snapshot_utc: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    result_recorded_utc: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
