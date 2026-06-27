"""WC2026 per-nation tournament advancement probabilities."""
from datetime import datetime

from sqlalchemy import DateTime, Float, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class WC2026TeamAdvancement(Base):
    __tablename__ = "wc2026_team_advancement"

    id: Mapped[int] = mapped_column(primary_key=True)
    nation: Mapped[str] = mapped_column(String(60), nullable=False, unique=True)
    elo: Mapped[float] = mapped_column(Float, nullable=False)
    p_r32: Mapped[float] = mapped_column(Float, nullable=False)
    p_r16: Mapped[float] = mapped_column(Float, nullable=False)
    p_qf: Mapped[float] = mapped_column(Float, nullable=False)
    p_sf: Mapped[float] = mapped_column(Float, nullable=False)
    p_finalist: Mapped[float] = mapped_column(Float, nullable=False)
    p_winner: Mapped[float] = mapped_column(Float, nullable=False)
    e_games: Mapped[float] = mapped_column(Float, nullable=False)
    n_sim: Mapped[int] = mapped_column(Integer, nullable=False)
    computed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
