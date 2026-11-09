"""Player career history (per season / competition), sourced from Transfermarkt."""

from sqlalchemy import Boolean, ForeignKey, Integer, String, UniqueConstraint, text
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

    # --- Gabarit Bzzoiro complémenté par Transfermarkt (nullable : NULL = inconnu, jamais 0 deviné) ---
    starts: Mapped[int | None] = mapped_column(Integer, nullable=True)
    total_shots: Mapped[int | None] = mapped_column(Integer, nullable=True)
    shots_on_target: Mapped[int | None] = mapped_column(Integer, nullable=True)
    total_pass: Mapped[int | None] = mapped_column(Integer, nullable=True)
    accurate_pass: Mapped[int | None] = mapped_column(Integer, nullable=True)
    total_tackle: Mapped[int | None] = mapped_column(Integer, nullable=True)
    won_tackle: Mapped[int | None] = mapped_column(Integer, nullable=True)
    yellow_card: Mapped[int | None] = mapped_column(Integer, nullable=True)
    red_card: Mapped[int | None] = mapped_column(Integer, nullable=True)
    fouls_committed: Mapped[int | None] = mapped_column(Integer, nullable=True)
    fouls_suffered: Mapped[int | None] = mapped_column(Integer, nullable=True)
    offsides: Mapped[int | None] = mapped_column(Integer, nullable=True)
    source: Mapped[str] = mapped_column(String(16), nullable=False, server_default="transfermarkt")

    # Compartiment Selection (equipe nationale) vs Club : True si TOUS les
    # matchs joues de ce bucket (saison, competition) sont des matchs
    # internationaux (gameInformation.isNationalGame cote Transfermarkt).
    # Defaut False : un bucket club normal, ou tout bucket mixte/ambigu,
    # n'est jamais marque selection a tort.
    is_national: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))

    def __repr__(self) -> str:
        return (
            f"<PlayerCareerSeason player_api_id={self.player_api_id} "
            f"season={self.season} competition_code={self.competition_code}>"
        )
