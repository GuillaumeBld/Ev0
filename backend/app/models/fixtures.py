"""Fixture model."""

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import String, Integer, DateTime, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin

if TYPE_CHECKING:
    from app.models.odds import OddsSnapshot


class Fixture(Base, TimestampMixin):
    """A football match fixture."""
    
    __tablename__ = "fixtures"
    
    id: Mapped[int] = mapped_column(primary_key=True)
    external_id: Mapped[str] = mapped_column(String(100), unique=True, index=True)
    
    # Match info
    league: Mapped[str] = mapped_column(String(50), index=True)  # ligue1, premier_league
    season: Mapped[str] = mapped_column(String(10))  # 2024-25
    matchweek: Mapped[int | None] = mapped_column(Integer, nullable=True)
    
    # Teams
    home_team: Mapped[str] = mapped_column(String(100))
    away_team: Mapped[str] = mapped_column(String(100))
    home_team_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    away_team_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    
    # Timing
    kickoff_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    
    # Status
    status: Mapped[str] = mapped_column(String(20), default="scheduled")  # scheduled, live, finished, postponed
    
    # Results (filled after match)
    home_score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    away_score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    
    # Relationships
    odds_snapshots: Mapped[list["OddsSnapshot"]] = relationship(back_populates="fixture")
    
    def __repr__(self) -> str:
        return f"<Fixture {self.home_team} vs {self.away_team} ({self.kickoff_utc})>"
