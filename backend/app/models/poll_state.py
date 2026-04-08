"""OddsPortal per-fixture polling state."""

from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class OddsPortalPollState(Base, TimestampMixin):
    __tablename__ = "oddsportal_poll_state"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    fixture_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("fixtures.id"), nullable=False, unique=True, index=True
    )
    oddsportal_url: Mapped[str] = mapped_column(String(500), nullable=False)
    betclic_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    unibet_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    next_due_at_utc: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    last_scraped_at_utc: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_success_at_utc: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    error_streak: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    stopped: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    stopped_reason: Mapped[str | None] = mapped_column(String(50), nullable=True)
