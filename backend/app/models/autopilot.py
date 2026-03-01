"""AutopilotDecision model — stores the RL agent's decisions and outcomes."""

from datetime import datetime

from sqlalchemy import DateTime, Float, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class AutopilotDecision(Base, TimestampMixin):
    """A single decision made by the RL autopilot agent."""

    __tablename__ = "autopilot_decisions"

    id: Mapped[int] = mapped_column(primary_key=True)

    # Linked recommendation (nullable: backtest pre-train records have no real rec)
    recommendation_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("recommendations.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    # State snapshot at decision time
    features_json: Mapped[str] = mapped_column(Text)  # JSON array of 10 floats

    # Action taken
    action_idx: Mapped[int] = mapped_column(Integer)  # 0=skip, 1=half_kelly, 2=kelly, 3=aggressive
    kelly_fraction: Mapped[float] = mapped_column(Float)  # actual fraction applied (0.0–1.5)
    stake: Mapped[float] = mapped_column(Float)  # computed stake in EUR (0 if skip)
    best_odds: Mapped[float] = mapped_column(Float, default=1.0)

    # Outcome (filled after match settlement)
    result: Mapped[str | None] = mapped_column(String(20), nullable=True)  # won, lost, void
    pnl: Mapped[float | None] = mapped_column(Float, nullable=True)
    reward: Mapped[float | None] = mapped_column(Float, nullable=True)  # pnl / bankroll at decision
    trained_on: Mapped[bool] = mapped_column(default=False)  # has this decision been used for fine-tuning?

    # Metadata
    mode: Mapped[str] = mapped_column(String(20), default="paper")  # paper | live
    player_name: Mapped[str] = mapped_column(String(200), default="")
    fixture_name: Mapped[str] = mapped_column(String(300), default="")
    market_type: Mapped[str] = mapped_column(String(50), default="")
    league: Mapped[str] = mapped_column(String(100), default="")
    settled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_utc: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), index=True
    )

    __table_args__ = (
        Index("ix_autopilot_unsettled", "result", "mode"),
    )

    def __repr__(self) -> str:
        return (
            f"<AutopilotDecision {self.player_name} action={self.action_idx} "
            f"stake={self.stake:.2f} result={self.result}>"
        )
