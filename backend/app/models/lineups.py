"""TeamLineup et TeamLineupPlayer — compositions d'équipe par match."""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin


class TeamLineup(Base, TimestampMixin):
    """Une composition par fixture × équipe × type."""

    __tablename__ = "team_lineups"

    id: Mapped[int] = mapped_column(primary_key=True)
    fixture_id: Mapped[int] = mapped_column(ForeignKey("fixtures.id"), index=True)
    team: Mapped[str] = mapped_column(String(200), index=True)
    # "official" | "probable_manual" | "last_known"
    lineup_type: Mapped[str] = mapped_column(String(20))
    source: Mapped[str] = mapped_column(String(50), default="manual")
    created_by: Mapped[str] = mapped_column(String(100), default="system")

    # Ce que Bzzoiro declare : "confirmed" pour une compo officielle,
    # "predicted" pour une compo probable publiee un a deux jours avant.
    # Nullable : une compo manuelle n'a pas de statut Bzzoiro.
    lineup_status: Mapped[str | None] = mapped_column(String(20), nullable=True)
    # Heure de publication annoncee par Bzzoiro (updated_at).
    published_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    players: Mapped[list[TeamLineupPlayer]] = relationship(
        back_populates="lineup", cascade="all, delete-orphan"
    )

    __table_args__ = (
        UniqueConstraint("fixture_id", "team", "lineup_type", name="uq_team_lineup"),
    )


class TeamLineupPlayer(Base, TimestampMixin):
    """Un joueur dans une composition."""

    __tablename__ = "team_lineup_players"

    id: Mapped[int] = mapped_column(primary_key=True)
    lineup_id: Mapped[int] = mapped_column(ForeignKey("team_lineups.id"), index=True)
    player_name: Mapped[str] = mapped_column(String(200))
    position: Mapped[str] = mapped_column(String(10))  # GK | DEF | MID | FWD
    is_starter: Mapped[bool] = mapped_column(Boolean, default=True)
    jersey_number: Mapped[int | None] = mapped_column(Integer, nullable=True)

    lineup: Mapped[TeamLineup] = relationship(back_populates="players")

    def __init__(self, **kwargs: object) -> None:
        kwargs.setdefault("is_starter", True)
        super().__init__(**kwargs)
