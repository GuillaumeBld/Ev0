"""Player models."""

from datetime import datetime

from sqlalchemy import String, Integer, Float, DateTime, ForeignKey, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class Player(Base, TimestampMixin):
    """A football player."""
    
    __tablename__ = "players"
    
    id: Mapped[int] = mapped_column(primary_key=True)
    external_id: Mapped[str] = mapped_column(String(100), unique=True, index=True)
    
    # Basic info
    name: Mapped[str] = mapped_column(String(200), index=True)
    normalized_name: Mapped[str] = mapped_column(String(200), index=True)  # For matching
    
    # Current team (can change)
    team: Mapped[str | None] = mapped_column(String(100), nullable=True)
    team_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    
    # Position
    position: Mapped[str | None] = mapped_column(String(50), nullable=True)  # FW, MF, DF, GK
    
    # Metadata
    nationality: Mapped[str | None] = mapped_column(String(100), nullable=True)
    birth_year: Mapped[int | None] = mapped_column(Integer, nullable=True)
    
    def __repr__(self) -> str:
        return f"<Player {self.name} ({self.team})>"


class PlayerStats(Base, TimestampMixin):
    """Player statistics snapshot (as of a specific date)."""
    
    __tablename__ = "player_stats"
    
    id: Mapped[int] = mapped_column(primary_key=True)
    player_id: Mapped[int] = mapped_column(ForeignKey("players.id"), index=True)
    
    # Snapshot info
    as_of_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    league: Mapped[str] = mapped_column(String(50))
    season: Mapped[str] = mapped_column(String(10))
    
    # Appearances
    matches_played: Mapped[int] = mapped_column(Integer, default=0)
    minutes_played: Mapped[int] = mapped_column(Integer, default=0)
    starts: Mapped[int] = mapped_column(Integer, default=0)
    
    # Goalscorer stats
    goals: Mapped[int] = mapped_column(Integer, default=0)
    npxg: Mapped[float] = mapped_column(Float, default=0.0)  # Non-penalty xG
    xg: Mapped[float] = mapped_column(Float, default=0.0)
    shots: Mapped[int] = mapped_column(Integer, default=0)
    shots_on_target: Mapped[int] = mapped_column(Integer, default=0)
    
    # Assist stats
    assists: Mapped[int] = mapped_column(Integer, default=0)
    xa: Mapped[float] = mapped_column(Float, default=0.0)  # Expected assists
    key_passes: Mapped[int] = mapped_column(Integer, default=0)
    sca: Mapped[int] = mapped_column(Integer, default=0)  # Shot-creating actions
    
    # Passing
    passes_completed: Mapped[int] = mapped_column(Integer, default=0)
    passes_into_penalty_area: Mapped[int] = mapped_column(Integer, default=0)
    progressive_passes: Mapped[int] = mapped_column(Integer, default=0)
    crosses: Mapped[int] = mapped_column(Integer, default=0)
    
    # Calculated per-90 fields (stored for convenience)
    xg_per_90: Mapped[float | None] = mapped_column(Float, nullable=True)
    xa_per_90: Mapped[float | None] = mapped_column(Float, nullable=True)
    
    __table_args__ = (
        UniqueConstraint("player_id", "as_of_utc", "league", "season", name="uq_player_stats_snapshot"),
    )
    
    def __repr__(self) -> str:
        return f"<PlayerStats player_id={self.player_id} as_of={self.as_of_utc}>"
