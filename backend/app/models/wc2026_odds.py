"""WC2026 outright odds model."""
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class WC2026OutrightOdd(Base):
    """Outright odds for WC 2026 (winner, top4, top_scorer, etc.)."""

    __tablename__ = "wc2026_outright_odds"
    __table_args__ = (
        UniqueConstraint(
            "nation", "player_name", "market_type", "bookmaker",
            name="uq_wc2026_outright",
            postgresql_nulls_not_distinct=True,
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    nation: Mapped[str | None] = mapped_column(String(60), nullable=True, index=True)
    player_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    market_type: Mapped[str] = mapped_column(String(30), nullable=False, index=True)
    bookmaker: Mapped[str] = mapped_column(String(20), nullable=False)
    odds: Mapped[float] = mapped_column(Float, nullable=False)
    scraped_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean, server_default="TRUE", nullable=False
    )
    first_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    odds_changed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    republished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
