"""Match event model — stores individual goal/assist events per fixture."""

from sqlalchemy import ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin


class MatchEvent(Base, TimestampMixin):
    """An individual event (goal, assist) in a finished match."""

    __tablename__ = "match_events"

    id: Mapped[int] = mapped_column(primary_key=True)

    # Match reference
    fixture_id: Mapped[int] = mapped_column(ForeignKey("fixtures.id"), index=True)

    # Player
    player_name: Mapped[str] = mapped_column(String(200))
    player_id: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # Event details
    event_type: Mapped[str] = mapped_column(
        String(20), index=True
    )  # goal, assist, own_goal, penalty_goal
    minute: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # Relationships
    fixture = relationship("Fixture")

    __table_args__ = (
        UniqueConstraint(
            "fixture_id",
            "player_name",
            "event_type",
            "minute",
            name="uq_match_event",
        ),
    )

    def __repr__(self) -> str:
        return f"<MatchEvent {self.player_name} {self.event_type} min={self.minute}>"
