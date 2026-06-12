"""WC2026 per-player tournament pricing results."""
from datetime import datetime

from sqlalchemy import DateTime, Float, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class WC2026PlayerPricing(Base):
    __tablename__ = "wc2026_player_pricing"

    id: Mapped[int] = mapped_column(primary_key=True)
    nation: Mapped[str] = mapped_column(String(60), nullable=False, index=True)
    player_name: Mapped[str] = mapped_column(String(100), nullable=False)
    position: Mapped[str | None] = mapped_column(String(10), nullable=True)

    expected_games: Mapped[float | None] = mapped_column(Float, nullable=True)
    lambda_goals: Mapped[float] = mapped_column(Float, nullable=False)
    lambda_assists: Mapped[float] = mapped_column(Float, nullable=False)

    # Cuts — goals
    p_1g: Mapped[float | None] = mapped_column(Float, nullable=True)
    p_2g: Mapped[float | None] = mapped_column(Float, nullable=True)
    p_3g: Mapped[float | None] = mapped_column(Float, nullable=True)
    p_4g: Mapped[float | None] = mapped_column(Float, nullable=True)
    fair_1g: Mapped[float | None] = mapped_column(Float, nullable=True)
    fair_2g: Mapped[float | None] = mapped_column(Float, nullable=True)
    fair_3g: Mapped[float | None] = mapped_column(Float, nullable=True)
    fair_4g: Mapped[float | None] = mapped_column(Float, nullable=True)

    # Cuts — assists
    p_1a: Mapped[float | None] = mapped_column(Float, nullable=True)
    p_2a: Mapped[float | None] = mapped_column(Float, nullable=True)
    p_3a: Mapped[float | None] = mapped_column(Float, nullable=True)
    fair_1a: Mapped[float | None] = mapped_column(Float, nullable=True)
    fair_2a: Mapped[float | None] = mapped_column(Float, nullable=True)
    fair_3a: Mapped[float | None] = mapped_column(Float, nullable=True)

    # Outrights
    p_top_scorer: Mapped[float | None] = mapped_column(Float, nullable=True)
    p_top_assister: Mapped[float | None] = mapped_column(Float, nullable=True)
    fair_top_scorer: Mapped[float | None] = mapped_column(Float, nullable=True)
    fair_top_assister: Mapped[float | None] = mapped_column(Float, nullable=True)

    computed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
