"""WC2026 expected lineup models."""

from __future__ import annotations

from sqlalchemy import Boolean, ForeignKey, Index, SmallInteger, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin


class WC2026ExpectedLineup(Base, TimestampMixin):
    """One expected lineup per (nation, context)."""

    __tablename__ = "wc2026_expected_lineups"
    __table_args__ = (
        UniqueConstraint("nation", "context", name="uq_wc2026_lineup_nation_context"),
        Index("ix_wc2026_lineup_nation", "nation"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    nation: Mapped[str] = mapped_column(String(60), nullable=False)
    # "default" | "matchday_1" | "matchday_2" | "matchday_3" | "r16" | "qf" | "sf" | "final"
    context: Mapped[str] = mapped_column(String(20), nullable=False)
    formation: Mapped[str] = mapped_column(String(10), nullable=False)
    source: Mapped[str] = mapped_column(String(20), nullable=False, default="manual")

    players: Mapped[list[WC2026ExpectedLineupPlayer]] = relationship(
        back_populates="lineup", cascade="all, delete-orphan"
    )


class WC2026ExpectedLineupPlayer(Base):
    """One player slot in an expected lineup."""

    __tablename__ = "wc2026_expected_lineup_players"
    __table_args__ = (
        UniqueConstraint("lineup_id", "player_name", name="uq_wc2026_lineup_player"),
        Index("ix_wc2026_lineup_players_lineup", "lineup_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    lineup_id: Mapped[int] = mapped_column(ForeignKey("wc2026_expected_lineups.id", ondelete="CASCADE"), index=True)
    player_name: Mapped[str] = mapped_column(String(100), nullable=False)
    position: Mapped[str] = mapped_column(String(4), nullable=False)  # GK / DEF / MID / FWD
    # 0=GK row, 1=first outfield line (DEF), 2=next line, etc.
    line_index: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    # left-to-right order within a line
    slot_index: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    is_starter: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    # "starter" | "sub_planned" | "sub_tactical" | "reserve"
    role: Mapped[str] = mapped_column(String(20), nullable=False, default="starter")
    expected_minutes: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=85)

    lineup: Mapped[WC2026ExpectedLineup] = relationship(back_populates="players")
