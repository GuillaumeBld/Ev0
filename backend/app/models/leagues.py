"""League — static reference row for one of the ~57 tracked competitions.

One row per entry of `LEAGUE_REGISTRY` (app/ingestion/bzzoiro/league_registry.py),
seeded by the Task 1 migration (see alembic/versions/*_expansion_leagues.py).
`id` is NOT an autoincrement surrogate: it is the Bzzoiro internal id (espace
A) itself, so joins against BzzEvent.league_api_id / CanonicalTeam.league_api_id
need no translation layer.

`tier` is a single VARCHAR holding both the int tiers ("1".."4") and the
literal sentinel "selection" for national-team competitions — LeagueMeta.tier
is typed `int | str` for the same reason (see league_registry.py). A second
nullable int column + boolean flag was considered and rejected: it would
let the two disagree (e.g. tier=2 with is_selection=True), which a single
string column can't do.
"""
from __future__ import annotations

from sqlalchemy import Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class League(Base):
    """One tracked competition — static metadata, no match/standings data."""

    __tablename__ = "leagues"

    # Bzzoiro internal id (espace A) — same space as LEAGUE_REGISTRY's key,
    # CanonicalTeam.league_api_id and BzzEvent.league_api_id.
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)

    name: Mapped[str] = mapped_column(String(100), nullable=False)

    # None for UEFA club competitions and every national-team selection —
    # see league_registry.py's module docstring.
    country: Mapped[str | None] = mapped_column(String(100), nullable=True)

    # "1".."4", or the literal "selection" — see class docstring.
    tier: Mapped[str] = mapped_column(String(20), nullable=False)

    # domestic_1 | domestic_2 | uefa_club | world | selection
    kind: Mapped[str] = mapped_column(String(20), nullable=False)

    # round_robin | groups | knockout
    format: Mapped[str] = mapped_column(String(20), nullable=False)

    # Non-None iff format == "round_robin" — enforced on LEAGUE_REGISTRY by
    # league_registry._validate_registry(), not re-enforced here as a DB
    # constraint to keep this table a plain seeded mirror of the registry.
    expected_count: Mapped[int | None] = mapped_column(Integer, nullable=True)

    rank: Mapped[int] = mapped_column(Integer, nullable=False)

    def __repr__(self) -> str:
        return f"<League id={self.id} name={self.name!r} kind={self.kind}>"
