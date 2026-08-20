"""Market-implied team xG estimates (append-only time series)."""

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


# Append-only time series: inherits Base only (no updated_at needed).
class TeamXgEstimate(Base):
    __tablename__ = "team_xg_estimates"

    id: Mapped[int] = mapped_column(primary_key=True)
    fixture_id: Mapped[int] = mapped_column(
        ForeignKey("fixtures.id"), nullable=False, index=True
    )
    # "opening" = premiere estimation publiee, "closing" = derniere avant le
    # coup d'envoi. Une ligne de chaque par match, archivee definitivement.
    phase: Mapped[str] = mapped_column(String(10), nullable=False, server_default="closing")
    as_of_utc: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    lambda_home: Mapped[float] = mapped_column(Float, nullable=False)
    lambda_away: Mapped[float] = mapped_column(Float, nullable=False)
    fit_residual: Mapped[float] = mapped_column(Float, nullable=False)
    flagged: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    data_source: Mapped[str] = mapped_column(String(20), nullable=False)
    fallback_used: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    input_snapshot_ids: Mapped[list[int] | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default="now()", nullable=False
    )

    __table_args__ = (
        UniqueConstraint("fixture_id", "phase", name="uq_team_xg_estimates_fixture_phase"),
    )
