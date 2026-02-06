"""Odds snapshot model."""

from datetime import datetime

from sqlalchemy import String, Float, DateTime, ForeignKey, UniqueConstraint, JSON
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin


class OddsSnapshot(Base, TimestampMixin):
    """A snapshot of bookmaker odds for a player prop market."""
    
    __tablename__ = "odds_snapshots"
    
    id: Mapped[int] = mapped_column(primary_key=True)
    
    # Match reference
    fixture_id: Mapped[int] = mapped_column(ForeignKey("fixtures.id"), index=True)
    
    # Player reference (may be soft link if player not in DB)
    player_id: Mapped[int | None] = mapped_column(ForeignKey("players.id"), nullable=True)
    player_name: Mapped[str] = mapped_column(String(200))  # Store name for matching
    
    # Market info
    market_type: Mapped[str] = mapped_column(String(50), index=True)  # goalscorer, assist
    bookmaker: Mapped[str] = mapped_column(String(50), index=True)  # betclic, pmu, unibet
    
    # Odds
    odds: Mapped[float] = mapped_column(Float)
    implied_probability: Mapped[float] = mapped_column(Float)  # 1/odds
    
    # Snapshot timing
    snapshot_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    
    # Raw response (for debugging)
    raw_data: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    
    # Relationship
    fixture = relationship("Fixture", back_populates="odds_snapshots")
    
    __table_args__ = (
        UniqueConstraint(
            "fixture_id", "player_name", "market_type", "bookmaker", "snapshot_utc",
            name="uq_odds_snapshot"
        ),
    )
    
    def __repr__(self) -> str:
        return f"<OddsSnapshot {self.player_name} {self.market_type} @{self.bookmaker} = {self.odds}>"
