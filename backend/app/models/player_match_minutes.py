"""PlayerMatchMinutes model — stores per-player minutes played per fixture."""

from sqlalchemy import ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class PlayerMatchMinutes(Base, TimestampMixin):
    """Minutes played by a player in a specific fixture (from Understat rostersData)."""

    __tablename__ = "player_match_minutes"

    id: Mapped[int] = mapped_column(primary_key=True)
    fixture_id: Mapped[int] = mapped_column(ForeignKey("fixtures.id"), index=True)
    player_name: Mapped[str] = mapped_column(String(200))
    minutes_played: Mapped[int] = mapped_column(Integer)

    __table_args__ = (
        UniqueConstraint("fixture_id", "player_name", name="uq_player_match_minutes"),
    )

    def __repr__(self) -> str:
        return f"<PlayerMatchMinutes {self.player_name} {self.minutes_played}min fixture={self.fixture_id}>"
