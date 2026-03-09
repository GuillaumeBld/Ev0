"""Player models."""

from datetime import datetime
from enum import StrEnum

from sqlalchemy import DateTime, Float, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class DataSource(StrEnum):
    """Data source for player stats."""

    UNDERSTAT = "understat"
    SOFASCORE = "sofascore"
    API_FOOTBALL = "api_football"
    AVERAGE = "average"   # Merged average across sources


class Player(Base, TimestampMixin):
    """A football player."""

    __tablename__ = "players"

    id: Mapped[int] = mapped_column(primary_key=True)
    external_id: Mapped[str] = mapped_column(String(100), unique=True, index=True)

    # External IDs per source
    understat_id: Mapped[str | None] = mapped_column(String(100), nullable=True, index=True)
    sofascore_id: Mapped[str | None] = mapped_column(String(100), nullable=True, index=True)
    api_football_id: Mapped[str | None] = mapped_column(String(100), nullable=True, index=True)

    # Basic info
    name: Mapped[str] = mapped_column(String(200), index=True)
    normalized_name: Mapped[str] = mapped_column(String(200), index=True)

    # Current team
    team: Mapped[str | None] = mapped_column(String(100), nullable=True)
    team_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    league: Mapped[str | None] = mapped_column(String(50), nullable=True)

    # Position: FW / MF / W / FB / DF / GK
    position: Mapped[str | None] = mapped_column(String(50), nullable=True)

    # Metadata
    nationality: Mapped[str | None] = mapped_column(String(100), nullable=True)
    birth_year: Mapped[int | None] = mapped_column(Integer, nullable=True)

    def __repr__(self) -> str:
        return f"<Player {self.name} ({self.team})>"


class PlayerStats(Base, TimestampMixin):
    """
    Player statistics snapshot (as of a specific date) from a specific source.

    Model C sources:
      - Understat : xg, npxg, xa, xgchain, xgbuildup, goals, assists
      - Sofascore : shots_on_target, touches_attack_pen_area, big_chances_created,
                    accurate_crosses, through_balls, key_passes, sofascore_rating
      - average   : merged/computed snapshot used by the pricing engine
    """

    __tablename__ = "player_stats"

    id: Mapped[int] = mapped_column(primary_key=True)
    player_id: Mapped[int] = mapped_column(ForeignKey("players.id"), index=True)

    # Snapshot metadata
    as_of_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    league: Mapped[str] = mapped_column(String(50))
    season: Mapped[str] = mapped_column(String(10))
    source: Mapped[str] = mapped_column(String(20), default="average")

    # ── Appearances ───────────────────────────────────────────────
    matches_played: Mapped[int] = mapped_column(Integer, default=0)
    minutes_played: Mapped[int] = mapped_column(Integer, default=0)
    starts: Mapped[int] = mapped_column(Integer, default=0)

    # ── Goalscorer stats ──────────────────────────────────────────
    goals: Mapped[int] = mapped_column(Integer, default=0)
    shots: Mapped[int] = mapped_column(Integer, default=0)

    # From Sofascore
    shots_on_target: Mapped[int] = mapped_column(Integer, default=0)
    touches_attack_pen_area: Mapped[int] = mapped_column(Integer, default=0)

    # From Understat
    xg: Mapped[float] = mapped_column(Float, default=0.0)
    npxg: Mapped[float] = mapped_column(Float, default=0.0)
    xgchain: Mapped[float] = mapped_column(Float, default=0.0)    # xG Chain (Understat)
    xgbuildup: Mapped[float] = mapped_column(Float, default=0.0)  # xG Buildup (Understat)

    # ── Assist stats ──────────────────────────────────────────────
    assists: Mapped[int] = mapped_column(Integer, default=0)

    # From Understat
    xa: Mapped[float] = mapped_column(Float, default=0.0)

    # From Sofascore
    key_passes: Mapped[int] = mapped_column(Integer, default=0)
    big_chances_created: Mapped[int] = mapped_column(Integer, default=0)
    accurate_crosses: Mapped[int] = mapped_column(Integer, default=0)
    total_crosses: Mapped[int] = mapped_column(Integer, default=0)
    through_balls: Mapped[int] = mapped_column(Integer, default=0)

    # ── Performance ───────────────────────────────────────────────
    sofascore_rating: Mapped[float | None] = mapped_column(Float, nullable=True)

    # ── Stored per-90 rates (pricing engine anchor values) ────────
    xg_per_90: Mapped[float | None] = mapped_column(Float, nullable=True)
    npxg_per_90: Mapped[float | None] = mapped_column(Float, nullable=True)
    xa_per_90: Mapped[float | None] = mapped_column(Float, nullable=True)
    xgchain_per_90: Mapped[float | None] = mapped_column(Float, nullable=True)
    shots_on_target_per_90: Mapped[float | None] = mapped_column(Float, nullable=True)
    touches_attack_pen_area_per_90: Mapped[float | None] = mapped_column(Float, nullable=True)
    bcc_per_90: Mapped[float | None] = mapped_column(Float, nullable=True)
    accurate_crosses_per_90: Mapped[float | None] = mapped_column(Float, nullable=True)
    through_balls_per_90: Mapped[float | None] = mapped_column(Float, nullable=True)

    __table_args__ = (
        UniqueConstraint(
            "player_id",
            "as_of_utc",
            "league",
            "season",
            "source",
            name="uq_player_stats_snapshot_source",
        ),
    )

    def compute_per_90s(self) -> None:
        """Recompute all per-90 rates from season totals and store in-place."""
        mins = self.minutes_played or 0
        if mins < 1:
            return

        def p90(val: float | int) -> float:
            return round((float(val) / mins) * 90, 4)

        self.xg_per_90 = p90(self.xg)
        self.npxg_per_90 = p90(self.npxg)
        self.xa_per_90 = p90(self.xa)
        self.xgchain_per_90 = p90(self.xgchain)
        self.shots_on_target_per_90 = p90(self.shots_on_target)
        self.touches_attack_pen_area_per_90 = p90(self.touches_attack_pen_area)
        self.bcc_per_90 = p90(self.big_chances_created)
        self.accurate_crosses_per_90 = p90(self.accurate_crosses)
        self.through_balls_per_90 = p90(self.through_balls)

    def __repr__(self) -> str:
        return (
            f"<PlayerStats player_id={self.player_id} "
            f"source={self.source} as_of={self.as_of_utc}>"
        )


class Team(Base, TimestampMixin):
    """A football team."""

    __tablename__ = "teams"

    id: Mapped[int] = mapped_column(primary_key=True)
    external_id: Mapped[str] = mapped_column(String(100), unique=True, index=True)

    # External IDs per source
    understat_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    sofascore_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    api_football_id: Mapped[str | None] = mapped_column(String(100), nullable=True)

    name: Mapped[str] = mapped_column(String(200), index=True)
    normalized_name: Mapped[str] = mapped_column(String(200), index=True)
    short_name: Mapped[str | None] = mapped_column(String(50), nullable=True)

    league: Mapped[str] = mapped_column(String(50))
    season: Mapped[str] = mapped_column(String(10))

    def __repr__(self) -> str:
        return f"<Team {self.name} ({self.league})>"
