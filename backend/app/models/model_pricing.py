"""Snapshots de pricing par modèle — registre Alpha/Beta (spec 2026-07-18, §3.1)."""

from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class ModelPricingSnapshot(Base, TimestampMixin):
    """Prix pré-match d'un (match, joueur, marché) pour un modèle donné.

    Une ligne par (fixture, joueur, marché, modèle), upsertée jusqu'au coup
    d'envoi puis figée (frozen=True). Seules les lignes figées sont admissibles
    pour comparer les modèles — rien n'est recalculé a posteriori.
    """

    __tablename__ = "model_pricing_snapshots"
    __table_args__ = (
        UniqueConstraint(
            "fixture_id", "player_api_id", "market", "model_name",
            name="uq_model_pricing_snapshot",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    model_name: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    fixture_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("fixtures.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    player_api_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    player_name: Mapped[str] = mapped_column(String(200), nullable=False)
    market: Mapped[str] = mapped_column(String(30), nullable=False)
    probability: Mapped[float] = mapped_column(Float, nullable=False)
    fair_odds: Mapped[float] = mapped_column(Float, nullable=False)
    as_of_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    frozen: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
