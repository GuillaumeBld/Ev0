"""Match-level odds snapshot model (h2h / totals / btts)."""

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin


class MatchOddsSnapshot(Base, TimestampMixin):
    """A snapshot of match-level bookmaker odds (h2h, totals, btts)."""

    __tablename__ = "match_odds_snapshots"

    id: Mapped[int] = mapped_column(primary_key=True)
    fixture_id: Mapped[int] = mapped_column(ForeignKey("fixtures.id"), index=True)
    bookmaker: Mapped[str] = mapped_column(String(50), index=True)
    # 'h2h' | 'totals' | 'btts'
    market_type: Mapped[str] = mapped_column(String(50), index=True)
    # 'home' | 'draw' | 'away' | 'over_2.5' | 'under_2.5' | 'yes' | 'no'
    outcome: Mapped[str] = mapped_column(String(50))
    odds: Mapped[float] = mapped_column(Float)
    snapshot_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    source: Mapped[str | None] = mapped_column(String(20), nullable=True)
    source_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    parse_version: Mapped[str | None] = mapped_column(String(20), nullable=True)
    fallback_used: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")

    fixture = relationship("Fixture")

    __table_args__ = (
        UniqueConstraint(
            "fixture_id",
            "bookmaker",
            "market_type",
            "outcome",
            "snapshot_utc",
            name="uq_match_odds_snapshot",
        ),
    )

    def __repr__(self) -> str:
        return (
            f"<MatchOddsSnapshot fixture={self.fixture_id} "
            f"{self.bookmaker} {self.market_type}/{self.outcome} @{self.odds}>"
        )
