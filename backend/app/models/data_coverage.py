"""DataCoverageRun — one row per coverage check run for a single competition.

Mirrors SquadSyncRun's shape (app/models/squad_sync.py) but is scoped to one
league per row rather than a whole sync pass: the ~57-competition expansion
(Task 1+) needs to know, per competition, how many clubs/players are
actually usable — not just whether the overall sync succeeded. `league_api_id`
is the Bzzoiro internal id (espace A), the same space as `leagues.id` and
`LEAGUE_REGISTRY`'s key (see app/ingestion/bzzoiro/league_registry.py) —
despite the inherited "api_id" naming, consistent with every other
`league_api_id` column in this codebase (BzzEvent, CanonicalTeam, ...).

No foreign key to `leagues.id`: a coverage run for a league not yet (or no
longer) in the registry must still be recorded rather than rejected — same
rationale as CareerImportAttempt.player_api_id (app/models/career_import.py).
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Integer, String, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class DataCoverageRun(Base):
    """One row per per-league data coverage check (clubs/joueurs completeness)."""

    __tablename__ = "data_coverage_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    league_api_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)

    clubs_total: Mapped[int | None] = mapped_column(Integer, nullable=True)
    clubs_ok: Mapped[int | None] = mapped_column(Integer, nullable=True)
    clubs_incomplets: Mapped[int | None] = mapped_column(Integer, nullable=True)
    joueurs_sans_stats: Mapped[int | None] = mapped_column(Integer, nullable=True)

    status: Mapped[str | None] = mapped_column(String(10), nullable=True)  # ok/partial/failed
    detail: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    def __repr__(self) -> str:
        return (
            f"<DataCoverageRun id={self.id} league_api_id={self.league_api_id} "
            f"status={self.status} started_at={self.started_at}>"
        )
