"""Transfermarkt squad sync run tracking (bzz_players reconciliation runs)."""

from datetime import datetime

from sqlalchemy import DateTime, Integer, String, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class SquadSyncRun(Base):
    """One row per Transfermarkt squad reconciliation run (daily/weekly)."""

    __tablename__ = "squad_sync_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    mode: Mapped[str | None] = mapped_column(String(10), nullable=True)  # daily/weekly

    clubs_total: Mapped[int | None] = mapped_column(Integer, nullable=True)
    clubs_ok: Mapped[int | None] = mapped_column(Integer, nullable=True)
    clubs_failed: Mapped[int | None] = mapped_column(Integer, nullable=True)
    players_updated: Mapped[int | None] = mapped_column(Integer, nullable=True)
    players_detached: Mapped[int | None] = mapped_column(Integer, nullable=True)

    status: Mapped[str | None] = mapped_column(String(10), nullable=True)  # ok/partial/failed
    detail: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    def __repr__(self) -> str:
        return (
            f"<SquadSyncRun id={self.id} mode={self.mode} status={self.status} "
            f"started_at={self.started_at}>"
        )
