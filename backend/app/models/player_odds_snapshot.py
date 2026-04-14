"""Player-level bookmaker odds snapshot (goalscorer / assist)."""
from datetime import datetime

from sqlalchemy import DateTime, Float, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class PlayerOddsSnapshot(Base):
    """Latest bookmaker odds for a player prop market."""

    __tablename__ = "player_odds_snapshots"

    id: Mapped[int] = mapped_column(primary_key=True)
    fixture_id: Mapped[int] = mapped_column(ForeignKey("fixtures.id"), index=True)
    bookmaker: Mapped[str] = mapped_column(String(30))
    market_type: Mapped[str] = mapped_column(String(20))   # goalscorer | assist
    player_name: Mapped[str] = mapped_column(String(200))
    odds: Mapped[float] = mapped_column(Float)
    scraped_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        UniqueConstraint(
            "fixture_id", "bookmaker", "market_type", "player_name",
            name="uq_player_odds",
        ),
    )
