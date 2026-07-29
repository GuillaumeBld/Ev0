"""Player career history (per season / competition), sourced from Transfermarkt."""

from sqlalchemy import ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class PlayerCareerSeason(Base, TimestampMixin):
    """One (player, season, competition) row of career stats."""

    __tablename__ = "player_career_seasons"

    __table_args__ = (
        UniqueConstraint(
            "player_api_id", "season", "competition_code", name="uq_player_career_season"
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)

    player_api_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("bzz_players.api_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    tm_id: Mapped[int | None] = mapped_column(Integer, nullable=True)

    season: Mapped[str] = mapped_column(String(10), nullable=False)
    season_start_year: Mapped[int | None] = mapped_column(Integer, nullable=True)
    competition_code: Mapped[str | None] = mapped_column(String(20), nullable=True)
    competition: Mapped[str | None] = mapped_column(String(120), nullable=True)

    appearances: Mapped[int] = mapped_column(Integer, nullable=False)
    goals: Mapped[int] = mapped_column(Integer, nullable=False)
    assists: Mapped[int] = mapped_column(Integer, nullable=False)
    minutes: Mapped[int] = mapped_column(Integer, nullable=False)

    def __repr__(self) -> str:
        return (
            f"<PlayerCareerSeason player_api_id={self.player_api_id} "
            f"season={self.season} competition_code={self.competition_code}>"
        )
