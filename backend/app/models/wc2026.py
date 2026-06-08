"""WC 2026 official squad players."""

from sqlalchemy import Index, SmallInteger, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class WC2026SquadPlayer(Base, TimestampMixin):
    """Official WC 2026 squad — one row per player per nation."""

    __tablename__ = "wc2026_squad_players"
    __table_args__ = (
        UniqueConstraint("nation", "player_name", name="uq_wc2026_nation_player"),
        Index("ix_wc2026_nation", "nation"),
        Index("ix_wc2026_group", "group_letter"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    nation: Mapped[str] = mapped_column(String(60), nullable=False)
    group_letter: Mapped[str] = mapped_column(String(1), nullable=False)
    player_name: Mapped[str] = mapped_column(String(100), nullable=False)
    club: Mapped[str | None] = mapped_column(String(100), nullable=True)
    position: Mapped[str] = mapped_column(String(3), nullable=False)  # GK DEF MID FWD
    shirt_number: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    flag_emoji: Mapped[str | None] = mapped_column(String(10), nullable=True)
