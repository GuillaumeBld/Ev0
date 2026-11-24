"""Player-level bookmaker odds snapshot (goalscorer / assist)."""
from datetime import datetime

from sqlalchemy import DateTime, Float, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class PlayerOddsSnapshot(Base):
    """Latest bookmaker odds for a player prop market."""

    __tablename__ = "player_odds_snapshots"

    id: Mapped[int] = mapped_column(primary_key=True)
    fixture_id: Mapped[int] = mapped_column(ForeignKey("fixtures.id"), index=True)
    bookmaker: Mapped[str] = mapped_column(String(30))
    market_type: Mapped[str] = mapped_column(String(20))   # goalscorer | assist
    player_name: Mapped[str] = mapped_column(String(200))
    # bzz_players.api_id : résolu contre l'effectif Atlas des deux équipes du
    # match (player_identity.resolve_odds_player) ; None = non relié
    player_api_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    odds: Mapped[float] = mapped_column(Float)
    # "avec_sub" (le pari couvre le remplaçant) | "sans_sub" (second recours)
    # | "inconnu" (ligne écrite avant le 24/11/2026, variante non notée).
    # Une seule cote par joueur x book x marché : la variante dit laquelle.
    variant: Mapped[str] = mapped_column(
        String(10), nullable=False, default="sans_sub", server_default="inconnu"
    )
    scraped_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        UniqueConstraint(
            "fixture_id", "bookmaker", "market_type", "player_name",
            name="uq_player_odds",
        ),
    )
