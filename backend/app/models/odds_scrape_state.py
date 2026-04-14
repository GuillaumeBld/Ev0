"""Tracks last/next scrape timestamps per fixture for the OddsScheduler."""
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class OddsScrapeState(Base):
    """One row per fixture — when it was last scraped and when next scrape is due."""

    __tablename__ = "odds_scrape_state"

    fixture_id: Mapped[int] = mapped_column(
        ForeignKey("fixtures.id"), primary_key=True
    )
    last_scraped_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    next_scrape_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    betclic_ok: Mapped[bool] = mapped_column(Boolean, default=False)
    unibet_ok: Mapped[bool] = mapped_column(Boolean, default=False)
