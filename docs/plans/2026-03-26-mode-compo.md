# Mode Compo — Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ajouter la gestion des compositions d'équipe (3 modes : official > probable_manual > last_known) pour afficher les titulaires par match et préparer le recalcul xG dynamique.

**Architecture:** Deux nouvelles tables DB (`team_lineups`, `team_lineup_players`) + champ `is_striker` sur `Player`. Fonction de résolution par priorité. API CRUD. Page admin `/dashboard/lineups` pour saisie manuelle. Badge lineup + liste des titulaires dans les recommendations.

**Tech Stack:** FastAPI + SQLAlchemy async + Alembic · Next.js 14 App Router + TypeScript + Lucide React

---

## Chunk 1: Backend — Models + Migration

### Task 1: Modèles SQLAlchemy + migration 013

**Files:**
- Modify: `backend/app/models/players.py` — ajouter `is_striker`
- Create: `backend/app/models/lineups.py` — `TeamLineup` + `TeamLineupPlayer`
- Modify: `backend/app/models/__init__.py` — exporter les nouveaux modèles
- Create: `backend/alembic/versions/013_add_lineup_tables.py`
- Create: `backend/tests/test_lineup_models.py`

- [ ] **Step 1: Écrire les tests (failing)**

```python
# backend/tests/test_lineup_models.py
from app.models.players import Player
from app.models.lineups import TeamLineup, TeamLineupPlayer


def test_player_is_striker_defaults_to_false():
    p = Player(name="Test", external_id="x1")
    assert p.is_striker is False


def test_player_is_striker_can_be_set():
    p = Player(name="Ramos", external_id="x2", is_striker=True)
    assert p.is_striker is True


def test_team_lineup_fields():
    lu = TeamLineup(fixture_id=1, team="psg", lineup_type="probable_manual")
    assert lu.lineup_type == "probable_manual"


def test_team_lineup_player_defaults():
    p = TeamLineupPlayer(lineup_id=1, player_name="Donnarumma", position="GK")
    assert p.is_starter is True
    assert p.jersey_number is None
```

- [ ] **Step 2: Run — confirmer l'échec**

```bash
cd backend && uv run pytest tests/test_lineup_models.py -x -q
```
Expected: `AttributeError: is_striker` ou `ImportError`

- [ ] **Step 3: Ajouter `is_striker` dans `backend/app/models/players.py`**

Après le champ `position` (ligne ~45) :
```python
is_striker: Mapped[bool] = mapped_column(default=False, server_default="false")
```

- [ ] **Step 4: Créer `backend/app/models/lineups.py`**

```python
"""TeamLineup et TeamLineupPlayer — compositions d'équipe par match."""
from __future__ import annotations

from sqlalchemy import Boolean, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin


class TeamLineup(Base, TimestampMixin):
    """Une composition par fixture × équipe × type."""

    __tablename__ = "team_lineups"

    id: Mapped[int] = mapped_column(primary_key=True)
    fixture_id: Mapped[int] = mapped_column(ForeignKey("fixtures.id"), index=True)
    team: Mapped[str] = mapped_column(String(200), index=True)
    # "official" | "probable_manual" | "last_known"
    lineup_type: Mapped[str] = mapped_column(String(20))
    source: Mapped[str] = mapped_column(String(50), default="manual")
    created_by: Mapped[str] = mapped_column(String(100), default="system")

    players: Mapped[list[TeamLineupPlayer]] = relationship(
        back_populates="lineup", cascade="all, delete-orphan"
    )

    __table_args__ = (
        UniqueConstraint("fixture_id", "team", "lineup_type", name="uq_team_lineup"),
    )


class TeamLineupPlayer(Base, TimestampMixin):
    """Un joueur dans une composition."""

    __tablename__ = "team_lineup_players"

    id: Mapped[int] = mapped_column(primary_key=True)
    lineup_id: Mapped[int] = mapped_column(ForeignKey("team_lineups.id"), index=True)
    player_name: Mapped[str] = mapped_column(String(200))
    position: Mapped[str] = mapped_column(String(10))  # GK | DEF | MID | FWD
    is_starter: Mapped[bool] = mapped_column(Boolean, default=True)
    jersey_number: Mapped[int | None] = mapped_column(Integer, nullable=True)

    lineup: Mapped[TeamLineup] = relationship(back_populates="players")
```

- [ ] **Step 5: Mettre à jour `backend/app/models/__init__.py`**

Ajouter après les imports existants :
```python
from app.models.lineups import TeamLineup, TeamLineupPlayer  # noqa: F401
```

- [ ] **Step 6: Run tests — vérifier le passage**

```bash
cd backend && uv run pytest tests/test_lineup_models.py -x -q
```
Expected: 4 passed

- [ ] **Step 7: Créer `backend/alembic/versions/013_add_lineup_tables.py`**

```python
"""add lineup tables and player is_striker

Revision ID: 013
Revises: 012
Create Date: 2026-03-26
"""
from alembic import op
import sqlalchemy as sa

revision = "013"
down_revision = "012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Champ is_striker sur players
    op.add_column(
        "players",
        sa.Column("is_striker", sa.Boolean(), nullable=False, server_default="false"),
    )

    # Table team_lineups
    op.create_table(
        "team_lineups",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("fixture_id", sa.Integer(), sa.ForeignKey("fixtures.id"), nullable=False),
        sa.Column("team", sa.String(200), nullable=False),
        sa.Column("lineup_type", sa.String(20), nullable=False),
        sa.Column("source", sa.String(50), nullable=False, server_default="manual"),
        sa.Column("created_by", sa.String(100), nullable=False, server_default="system"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("fixture_id", "team", "lineup_type", name="uq_team_lineup"),
    )
    op.create_index("ix_team_lineups_fixture_id", "team_lineups", ["fixture_id"])
    op.create_index("ix_team_lineups_team", "team_lineups", ["team"])

    # Table team_lineup_players
    op.create_table(
        "team_lineup_players",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("lineup_id", sa.Integer(), sa.ForeignKey("team_lineups.id"), nullable=False),
        sa.Column("player_name", sa.String(200), nullable=False),
        sa.Column("position", sa.String(10), nullable=False),
        sa.Column("is_starter", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("jersey_number", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_team_lineup_players_lineup_id", "team_lineup_players", ["lineup_id"])


def downgrade() -> None:
    op.drop_table("team_lineup_players")
    op.drop_table("team_lineups")
    op.drop_column("players", "is_striker")
```

- [ ] **Step 8: Lint**

```bash
cd backend && uv run ruff check app/models/lineups.py app/models/players.py
```
Expected: no errors

- [ ] **Step 9: Commit**

```bash
git add backend/app/models/lineups.py backend/app/models/players.py backend/app/models/__init__.py backend/alembic/versions/013_add_lineup_tables.py backend/tests/test_lineup_models.py
git commit -m "feat: add TeamLineup/TeamLineupPlayer models + Player.is_striker"
```

---

## Chunk 2: Backend — Résolution + API

### Task 2: Logique de résolution

**Files:**
- Create: `backend/app/ingestion/lineup_resolver.py`
- Create: `backend/tests/test_lineup_resolver.py`

La règle : `official` > `probable_manual` > `last_known` (dernière compo officielle de l'équipe pour un match précédent).

- [ ] **Step 1: Écrire les tests**

```python
# backend/tests/test_lineup_resolver.py
import pytest
from unittest.mock import MagicMock
from app.ingestion.lineup_resolver import resolve_lineup, ResolvedLineup, PRIORITY


def _make_lineup(lineup_type: str, players=None):
    lu = MagicMock()
    lu.lineup_type = lineup_type
    lu.players = players or []
    lu.id = 1
    return lu


def test_priority_order():
    assert PRIORITY["official"] < PRIORITY["probable_manual"] < PRIORITY["last_known"]


@pytest.mark.asyncio
async def test_resolve_official_wins_over_manual():
    """official bat probable_manual."""
    official = _make_lineup("official")
    manual = _make_lineup("probable_manual")
    result = await resolve_lineup(
        fixture_id=1, team="psg", session=None,
        _overrides=[manual, official],
    )
    assert result is not None
    assert result.lineup_type == "official"


@pytest.mark.asyncio
async def test_resolve_manual_wins_over_last_known():
    """probable_manual bat last_known."""
    manual = _make_lineup("probable_manual")
    last = _make_lineup("last_known")
    result = await resolve_lineup(
        fixture_id=1, team="psg", session=None,
        _overrides=[last, manual],
    )
    assert result.lineup_type == "probable_manual"


@pytest.mark.asyncio
async def test_resolve_returns_none_when_empty():
    result = await resolve_lineup(
        fixture_id=1, team="xyz", session=None,
        _overrides=[],
    )
    assert result is None
```

- [ ] **Step 2: Run — confirmer l'échec**

```bash
cd backend && uv run pytest tests/test_lineup_resolver.py -x -q
```
Expected: `ImportError`

- [ ] **Step 3: Créer `backend/app/ingestion/lineup_resolver.py`**

```python
"""Résolution de la composition active pour un (fixture_id, team).

Priorité : official > probable_manual > last_known
"""
from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.fixtures import Fixture
from app.models.lineups import TeamLineup, TeamLineupPlayer

PRIORITY: dict[str, int] = {
    "official": 0,
    "probable_manual": 1,
    "last_known": 2,
}


@dataclass
class ResolvedLineup:
    lineup_type: str
    team: str
    fixture_id: int
    players: list[TeamLineupPlayer]
    lineup_id: int | None = None


async def resolve_lineup(
    fixture_id: int,
    team: str,
    session: AsyncSession,
    _overrides: list | None = None,  # hook pour les tests
) -> ResolvedLineup | None:
    """Retourne la compo de priorité la plus haute pour ce fixture+team."""
    if _overrides is not None:
        if not _overrides:
            return None
        best = min(_overrides, key=lambda l: PRIORITY.get(l.lineup_type, 99))
        return ResolvedLineup(
            lineup_type=best.lineup_type,
            team=team,
            fixture_id=fixture_id,
            players=best.players,
            lineup_id=getattr(best, "id", None),
        )

    # 1. Chercher official ou probable_manual pour ce fixture précis
    result = await session.execute(
        select(TeamLineup).where(
            TeamLineup.fixture_id == fixture_id,
            TeamLineup.team == team,
            TeamLineup.lineup_type.in_(["official", "probable_manual"]),
        )
    )
    rows = result.scalars().all()
    if rows:
        best = min(rows, key=lambda l: PRIORITY[l.lineup_type])
        players_result = await session.execute(
            select(TeamLineupPlayer).where(TeamLineupPlayer.lineup_id == best.id)
        )
        return ResolvedLineup(
            lineup_type=best.lineup_type,
            team=team,
            fixture_id=fixture_id,
            players=players_result.scalars().all(),
            lineup_id=best.id,
        )

    # 2. Fallback : dernière compo officielle connue pour cette équipe
    fx_result = await session.execute(
        select(Fixture.kickoff_utc).where(Fixture.id == fixture_id)
    )
    kickoff = fx_result.scalar_one_or_none()
    if kickoff is None:
        return None

    prev_result = await session.execute(
        select(TeamLineup)
        .join(Fixture, TeamLineup.fixture_id == Fixture.id)
        .where(
            TeamLineup.team == team,
            TeamLineup.lineup_type == "official",
            Fixture.kickoff_utc < kickoff,
        )
        .order_by(Fixture.kickoff_utc.desc())
        .limit(1)
    )
    prev = prev_result.scalar_one_or_none()
    if prev is None:
        return None

    players_result = await session.execute(
        select(TeamLineupPlayer).where(TeamLineupPlayer.lineup_id == prev.id)
    )
    return ResolvedLineup(
        lineup_type="last_known",
        team=team,
        fixture_id=fixture_id,
        players=players_result.scalars().all(),
        lineup_id=prev.id,
    )
```

- [ ] **Step 4: Run tests**

```bash
cd backend && uv run pytest tests/test_lineup_resolver.py -x -q
```
Expected: 4 passed

- [ ] **Step 5: Commit**

```bash
git add backend/app/ingestion/lineup_resolver.py backend/tests/test_lineup_resolver.py
git commit -m "feat: add lineup resolution logic (official > probable_manual > last_known)"
```

---

### Task 3: Endpoints API lineup

**Files:**
- Create: `backend/app/api/lineups.py`
- Modify: `backend/app/main.py` — enregistrer le router
- Modify: `backend/app/api/players.py` — ajouter PATCH striker

Endpoints :
- `GET /api/v1/lineups/fixture/{fixture_id}` → compos résolues des deux équipes
- `POST /api/v1/lineups` → créer/remplacer une `probable_manual`
- `DELETE /api/v1/lineups/{lineup_id}` → supprimer une compo manuelle
- `GET /api/v1/lineups/team-players/{team}` → liste des noms de joueurs pour le sélecteur
- `PATCH /api/v1/players/{player_id}/striker` → toggle is_striker

- [ ] **Step 1: Créer `backend/app/api/lineups.py`**

```python
# backend/app/api/lineups.py
"""API CRUD pour les compositions d'équipe."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.ingestion.lineup_resolver import resolve_lineup
from app.models.fixtures import Fixture
from app.models.lineups import TeamLineup, TeamLineupPlayer
from app.models.players import Player

router = APIRouter(tags=["lineups"])


# ── Schemas ────────────────────────────────────────────────────────────────

class LineupPlayerIn(BaseModel):
    player_name: str
    position: str       # GK | DEF | MID | FWD
    is_starter: bool = True
    jersey_number: int | None = None


class LineupIn(BaseModel):
    fixture_id: int
    team: str
    players: list[LineupPlayerIn]


class LineupPlayerOut(BaseModel):
    player_name: str
    position: str
    is_starter: bool
    jersey_number: int | None
    is_striker: bool = False


class LineupOut(BaseModel):
    lineup_id: int | None
    lineup_type: str
    team: str
    players: list[LineupPlayerOut]


class FixtureLineupsOut(BaseModel):
    fixture_id: int
    home_team: str
    away_team: str
    home: LineupOut | None
    away: LineupOut | None


# ── Helpers ────────────────────────────────────────────────────────────────

async def _hydrate_strikers(
    players: list[TeamLineupPlayer], session: AsyncSession
) -> list[LineupPlayerOut]:
    """Enrichit chaque joueur avec son flag is_striker depuis la table Player."""
    names = [p.player_name for p in players]
    result = await session.execute(
        select(Player.name, Player.is_striker).where(Player.name.in_(names))
    )
    striker_map = {row.name: row.is_striker for row in result}
    return [
        LineupPlayerOut(
            player_name=p.player_name,
            position=p.position,
            is_starter=p.is_starter,
            jersey_number=p.jersey_number,
            is_striker=striker_map.get(p.player_name, False),
        )
        for p in players
    ]


# ── Routes ─────────────────────────────────────────────────────────────────

@router.get("/lineups/fixture/{fixture_id}", response_model=FixtureLineupsOut)
async def get_fixture_lineups(
    fixture_id: int, session: AsyncSession = Depends(get_db)
):
    fx = await session.get(Fixture, fixture_id)
    if fx is None:
        raise HTTPException(404, "Fixture not found")

    home_res = await resolve_lineup(fixture_id, fx.home_team, session)
    away_res = await resolve_lineup(fixture_id, fx.away_team, session)

    home_out = None
    if home_res:
        home_players = await _hydrate_strikers(home_res.players, session)
        home_out = LineupOut(
            lineup_id=home_res.lineup_id,
            lineup_type=home_res.lineup_type,
            team=home_res.team,
            players=home_players,
        )

    away_out = None
    if away_res:
        away_players = await _hydrate_strikers(away_res.players, session)
        away_out = LineupOut(
            lineup_id=away_res.lineup_id,
            lineup_type=away_res.lineup_type,
            team=away_res.team,
            players=away_players,
        )

    return FixtureLineupsOut(
        fixture_id=fixture_id,
        home_team=fx.home_team,
        away_team=fx.away_team,
        home=home_out,
        away=away_out,
    )


@router.post("/lineups", status_code=201, response_model=LineupOut)
async def create_lineup(body: LineupIn, session: AsyncSession = Depends(get_db)):
    """Créer ou remplacer une compo probable_manual."""
    existing = await session.execute(
        select(TeamLineup).where(
            TeamLineup.fixture_id == body.fixture_id,
            TeamLineup.team == body.team,
            TeamLineup.lineup_type == "probable_manual",
        )
    )
    old = existing.scalar_one_or_none()
    if old:
        await session.delete(old)
        await session.flush()

    lineup = TeamLineup(
        fixture_id=body.fixture_id,
        team=body.team,
        lineup_type="probable_manual",
        source="manual",
        created_by="user",
    )
    session.add(lineup)
    await session.flush()

    for p in body.players:
        session.add(
            TeamLineupPlayer(
                lineup_id=lineup.id,
                player_name=p.player_name,
                position=p.position,
                is_starter=p.is_starter,
                jersey_number=p.jersey_number,
            )
        )

    await session.commit()

    players_result = await session.execute(
        select(TeamLineupPlayer).where(TeamLineupPlayer.lineup_id == lineup.id)
    )
    players_out = await _hydrate_strikers(players_result.scalars().all(), session)

    return LineupOut(
        lineup_id=lineup.id,
        lineup_type=lineup.lineup_type,
        team=lineup.team,
        players=players_out,
    )


@router.delete("/lineups/{lineup_id}", status_code=204)
async def delete_lineup(lineup_id: int, session: AsyncSession = Depends(get_db)):
    lineup = await session.get(TeamLineup, lineup_id)
    if lineup is None:
        raise HTTPException(404, "Lineup not found")
    if lineup.lineup_type == "official":
        raise HTTPException(403, "Les compos officielles ne peuvent pas être supprimées")
    await session.delete(lineup)
    await session.commit()


@router.get("/lineups/team-players/{team}", response_model=list[str])
async def get_team_players(team: str, session: AsyncSession = Depends(get_db)):
    """Retourne les noms des joueurs en DB pour cette équipe (pour le sélecteur)."""
    result = await session.execute(
        select(Player.name)
        .where(Player.team.ilike(f"%{team}%"))
        .order_by(Player.name)
    )
    return [row[0] for row in result]
```

- [ ] **Step 2: Enregistrer le router dans `backend/app/main.py`**

Ajouter dans les imports :
```python
from app.api import lineups as lineups_api
```

Ajouter après les autres `include_router` :
```python
app.include_router(lineups_api.router, prefix="/api/v1", tags=["lineups"])
```

- [ ] **Step 3: Ajouter PATCH striker dans `backend/app/api/players.py`**

Trouver le router players et ajouter :
```python
@router.patch("/{player_id}/striker")
async def toggle_striker(player_id: int, session: AsyncSession = Depends(get_db)):
    """Toggle le flag is_striker du joueur."""
    player = await session.get(Player, player_id)
    if player is None:
        raise HTTPException(404, "Player not found")
    player.is_striker = not player.is_striker
    await session.commit()
    return {"id": player_id, "is_striker": player.is_striker}
```

- [ ] **Step 4: Lint**

```bash
cd backend && uv run ruff check app/api/lineups.py app/api/players.py app/main.py
```
Expected: no errors

- [ ] **Step 5: Run full test suite**

```bash
cd backend && uv run pytest tests/ -x -q
```
Expected: all pass

- [ ] **Step 6: Commit**

```bash
git add backend/app/api/lineups.py backend/app/api/players.py backend/app/main.py
git commit -m "feat: add lineup CRUD API + player is_striker toggle"
```

---

## Chunk 3: Frontend — Composants partagés

### Task 4: Composant `LineupDisplay`

**Files:**
- Create: `frontend/src/components/lineups/LineupDisplay.tsx`

Affiche : badge mode · formation (ex: "4-3-3") · lignes de joueurs, BU centré, BU en gras.

- [ ] **Step 1: Créer `frontend/src/components/lineups/LineupDisplay.tsx`**

```tsx
"use client"

import { Badge } from "@/components/ui/badge"

export type LineupPlayer = {
  player_name: string
  position: string      // GK | DEF | MID | FWD
  is_starter: boolean
  is_striker: boolean
}

export type LineupData = {
  lineup_type: "official" | "probable_manual" | "last_known"
  lineup_id: number | null
  team: string
  players: LineupPlayer[]
}

const BADGE_CONFIG = {
  official:        { label: "Officielle",     className: "bg-green-600 text-white hover:bg-green-600" },
  probable_manual: { label: "Probable",        className: "bg-orange-500 text-white hover:bg-orange-500" },
  last_known:      { label: "Dernière compo",  className: "bg-gray-500 text-white hover:bg-gray-500" },
}

const POSITION_ORDER = ["GK", "DEF", "MID", "FWD"] as const

/** Place les BU au centre de leur ligne, les non-BU de chaque côté. */
function centerBU(players: LineupPlayer[]): LineupPlayer[] {
  const bus   = players.filter(p => p.is_striker)
  const wings = players.filter(p => !p.is_striker)
  const split = Math.ceil(wings.length / 2)
  return [...wings.slice(0, split), ...bus, ...wings.slice(split)]
}

/** Déduit la formation en comptant DEF-MID-FWD parmi les titulaires. */
function getFormation(players: LineupPlayer[]): string {
  const starters = players.filter(p => p.is_starter && p.position !== "GK")
  const counts: Record<string, number> = {}
  for (const p of starters) counts[p.position] = (counts[p.position] ?? 0) + 1
  const parts = (["DEF", "MID", "FWD"] as const)
    .map(pos => counts[pos] ?? 0)
    .filter(n => n > 0)
  return parts.join("-")
}

export function LineupDisplay({ lineup }: { lineup: LineupData }) {
  const badge    = BADGE_CONFIG[lineup.lineup_type]
  const starters = lineup.players.filter(p => p.is_starter)
  const formation = getFormation(starters)

  const byPos: Record<string, LineupPlayer[]> = {}
  for (const pos of POSITION_ORDER) byPos[pos] = starters.filter(p => p.position === pos)

  // Détection auto BU : si ≤ 2 FWD et aucun marqué manuellement → tous BU par défaut
  const fwds = byPos["FWD"] ?? []
  if (fwds.length <= 2 && fwds.every(p => !p.is_striker)) {
    byPos["FWD"] = fwds.map(p => ({ ...p, is_striker: true }))
  }

  return (
    <div className="space-y-1 text-sm">
      <div className="flex items-center gap-2 mb-2">
        <Badge className={badge.className}>{badge.label}</Badge>
        {formation && (
          <span className="text-xs text-muted-foreground font-mono">{formation}</span>
        )}
      </div>

      {POSITION_ORDER.map(pos => {
        const line = byPos[pos] ?? []
        if (line.length === 0) return null
        const sorted = pos === "FWD" ? centerBU(line) : line
        return (
          <div key={pos} className="flex gap-x-3 justify-center py-0.5 flex-wrap">
            {sorted.map((p, i) => (
              <span
                key={i}
                className={
                  p.is_striker
                    ? "font-bold underline decoration-orange-400"
                    : "text-foreground"
                }
              >
                {p.player_name}
              </span>
            ))}
          </div>
        )
      })}
    </div>
  )
}
```

- [ ] **Step 2: Vérifier TypeScript**

```bash
cd frontend && npx tsc --noEmit 2>&1 | grep -i lineup
```
Expected: aucune erreur

- [ ] **Step 3: Commit**

```bash
git add frontend/src/components/lineups/LineupDisplay.tsx
git commit -m "feat: add LineupDisplay component (badge + formation + BU centering)"
```

---

### Task 5: Page admin `/dashboard/lineups`

**Files:**
- Create: `frontend/src/components/lineups/LineupEditor.tsx`
- Create: `frontend/src/app/dashboard/lineups/page.tsx`
- Modify: `frontend/src/components/Sidebar.tsx` — ajouter nav item

- [ ] **Step 1: Créer `frontend/src/components/lineups/LineupEditor.tsx`**

```tsx
"use client"

import { useState, useEffect } from "react"
import { Button } from "@/components/ui/button"

const POSITIONS = ["GK", "DEF", "MID", "FWD"] as const

type EditPlayer = {
  player_name: string
  position: string
  is_starter: boolean
}

export function LineupEditor({
  fixtureId,
  team,
  existingLineupId,
  onSaved,
  onDeleted,
}: {
  fixtureId: number
  team: string
  existingLineupId: number | null
  onSaved: () => void
  onDeleted: () => void
}) {
  const [roster, setRoster]     = useState<string[]>([])
  const [selected, setSelected] = useState<EditPlayer[]>([])
  const [saving, setSaving]     = useState(false)

  useEffect(() => {
    fetch(`/api/v1/lineups/team-players/${encodeURIComponent(team)}`)
      .then(r => r.json())
      .then(setRoster)
  }, [team])

  function togglePlayer(name: string) {
    setSelected(prev => {
      if (prev.find(p => p.player_name === name)) {
        return prev.filter(p => p.player_name !== name)
      }
      return [...prev, { player_name: name, position: "MID", is_starter: true }]
    })
  }

  function setPosition(name: string, pos: string) {
    setSelected(prev =>
      prev.map(p => p.player_name === name ? { ...p, position: pos } : p)
    )
  }

  async function handleSave() {
    setSaving(true)
    await fetch("/api/v1/lineups", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ fixture_id: fixtureId, team, players: selected }),
    })
    setSaving(false)
    onSaved()
  }

  async function handleDelete() {
    if (!existingLineupId) return
    await fetch(`/api/v1/lineups/${existingLineupId}`, { method: "DELETE" })
    onDeleted()
  }

  return (
    <div className="space-y-4">
      {/* Sélecteur de joueurs */}
      <div className="flex flex-wrap gap-2">
        {roster.map(name => {
          const sel = selected.find(p => p.player_name === name)
          return (
            <button
              key={name}
              onClick={() => togglePlayer(name)}
              className={`inline-flex items-center gap-1 px-2 py-1 rounded text-sm border transition-colors ${
                sel
                  ? "bg-primary text-primary-foreground border-primary"
                  : "bg-muted border-border hover:border-primary"
              }`}
            >
              {name}
              {sel && (
                <select
                  className="ml-1 text-xs bg-transparent border-none outline-none cursor-pointer"
                  value={sel.position}
                  onChange={e => { e.stopPropagation(); setPosition(name, e.target.value) }}
                  onClick={e => e.stopPropagation()}
                >
                  {POSITIONS.map(pos => <option key={pos} value={pos}>{pos}</option>)}
                </select>
              )}
            </button>
          )
        })}
      </div>

      <p className="text-xs text-muted-foreground">
        {selected.length} joueur(s) sélectionné(s)
      </p>

      <div className="flex gap-2">
        <Button
          onClick={handleSave}
          disabled={saving || selected.length === 0}
          size="sm"
        >
          {saving ? "Sauvegarde…" : "Enregistrer"}
        </Button>
        {existingLineupId && (
          <Button variant="outline" size="sm" onClick={handleDelete}>
            Effacer (retour dernière compo)
          </Button>
        )}
      </div>
    </div>
  )
}
```

- [ ] **Step 2: Créer `frontend/src/app/dashboard/lineups/page.tsx`**

```tsx
"use client"

import { useState, useEffect, useCallback } from "react"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Button } from "@/components/ui/button"
import { LineupDisplay, LineupData } from "@/components/lineups/LineupDisplay"
import { LineupEditor } from "@/components/lineups/LineupEditor"

type Fixture = {
  id: number
  home_team: string
  away_team: string
  kickoff_utc: string
  league: string
}

type FixtureLineups = {
  fixture_id: number
  home_team: string
  away_team: string
  home: LineupData | null
  away: LineupData | null
}

export default function LineupsAdminPage() {
  const [fixtures, setFixtures]     = useState<Fixture[]>([])
  const [selected, setSelected]     = useState<Fixture | null>(null)
  const [lineups, setLineups]       = useState<FixtureLineups | null>(null)
  const [editingTeam, setEditingTeam] = useState<"home" | "away" | null>(null)

  useEffect(() => {
    fetch("/api/v1/fixtures?status=scheduled&limit=30")
      .then(r => r.json())
      .then((data) => setFixtures(Array.isArray(data) ? data : data.fixtures ?? []))
  }, [])

  const loadLineups = useCallback(async (fixture: Fixture) => {
    setSelected(fixture)
    setEditingTeam(null)
    const r = await fetch(`/api/v1/lineups/fixture/${fixture.id}`)
    setLineups(await r.json())
  }, [])

  function handleSaved() {
    if (selected) loadLineups(selected)
    setEditingTeam(null)
  }

  return (
    <div className="p-6 space-y-6">
      <div>
        <h1 className="text-2xl font-bold">Compos probables</h1>
        <p className="text-sm text-muted-foreground mt-1">
          Saisie manuelle des compositions avant les matchs.
        </p>
      </div>

      {/* Sélecteur de match */}
      <div className="flex flex-wrap gap-2">
        {fixtures.map(fx => (
          <Button
            key={fx.id}
            variant={selected?.id === fx.id ? "default" : "outline"}
            size="sm"
            onClick={() => loadLineups(fx)}
          >
            {fx.home_team} vs {fx.away_team}
          </Button>
        ))}
      </div>

      {/* Cartes équipes */}
      {lineups && (
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          {(["home", "away"] as const).map(side => {
            const team   = side === "home" ? lineups.home_team : lineups.away_team
            const lineup = side === "home" ? lineups.home : lineups.away
            return (
              <Card key={side}>
                <CardHeader className="pb-2">
                  <CardTitle className="text-base">{team}</CardTitle>
                </CardHeader>
                <CardContent className="space-y-4">
                  {lineup ? (
                    <LineupDisplay lineup={lineup} />
                  ) : (
                    <p className="text-sm text-muted-foreground">Aucune compo connue</p>
                  )}

                  {editingTeam === side ? (
                    <LineupEditor
                      fixtureId={lineups.fixture_id}
                      team={team}
                      existingLineupId={lineup?.lineup_id ?? null}
                      onSaved={handleSaved}
                      onDeleted={handleSaved}
                    />
                  ) : (
                    <Button
                      size="sm"
                      variant="outline"
                      onClick={() => setEditingTeam(side)}
                    >
                      Modifier
                    </Button>
                  )}
                </CardContent>
              </Card>
            )
          })}
        </div>
      )}
    </div>
  )
}
```

- [ ] **Step 3: Ajouter l'entrée nav dans `frontend/src/components/Sidebar.tsx`**

Trouver le tableau `navigation` (ligne ~29) et ajouter après `Matchs` :
```tsx
import { ClipboardList } from 'lucide-react'   // ajouter dans les imports lucide

// Dans le tableau navigation :
{ name: 'Compos', href: '/dashboard/lineups', icon: ClipboardList },
```

- [ ] **Step 4: TypeScript check**

```bash
cd frontend && npx tsc --noEmit 2>&1 | grep -iE "lineup|compos"
```
Expected: aucune erreur

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/lineups/LineupEditor.tsx frontend/src/app/dashboard/lineups/ frontend/src/components/Sidebar.tsx
git commit -m "feat: add lineups admin page + sidebar nav"
```

---

## Chunk 4: Frontend — Toggle BU + badge recommendations

### Task 6: Toggle BU dans la page Joueurs

**Files:**
- Modify: `frontend/src/app/dashboard/players/page.tsx`

- [ ] **Step 1: Lire `frontend/src/app/dashboard/players/page.tsx`** pour comprendre la structure existante

- [ ] **Step 2: Ajouter l'import Crosshair et le bouton toggle**

Dans les imports lucide existants, ajouter `Crosshair`.

Dans chaque carte/ligne de joueur, ajouter :
```tsx
import { Crosshair } from 'lucide-react'

// Dans le JSX du joueur (après le nom) :
<button
  title={player.is_striker ? "Retirer statut BU" : "Marquer comme avant-centre (BU)"}
  onClick={async () => {
    await fetch(`/api/v1/players/${player.id}/striker`, { method: "PATCH" })
    // déclencher un refetch de la liste
    refetch()   // adapter selon le mécanisme de refresh existant (useState, SWR, etc.)
  }}
  className={`p-1 rounded transition-colors hover:bg-muted ${
    player.is_striker ? "text-orange-500" : "text-muted-foreground"
  }`}
>
  <Crosshair size={14} />
</button>
```

- [ ] **Step 3: Vérifier que `is_striker` est inclus dans les données renvoyées par `/api/v1/players`**

```bash
# Vérifier que le type Player dans le frontend inclut is_striker
grep -n "is_striker" frontend/src/ -r
```
Si absent, ajouter `is_striker: boolean` dans le type Player du frontend.

- [ ] **Step 4: TypeScript check**

```bash
cd frontend && npx tsc --noEmit 2>&1 | grep players
```

- [ ] **Step 5: Commit**

```bash
git add frontend/src/app/dashboard/players/page.tsx
git commit -m "feat: add BU (is_striker) Crosshair toggle in players page"
```

---

### Task 7: Badge lineup dans les recommendations

**Files:**
- Modify: `frontend/src/app/dashboard/recommendations/page.tsx`

- [ ] **Step 1: Lire `frontend/src/app/dashboard/recommendations/page.tsx`** pour comprendre la structure

- [ ] **Step 2: Ajouter le fetch lineup par fixture**

```tsx
import { LineupDisplay, LineupData } from "@/components/lineups/LineupDisplay"

// Hook pour charger les compos à la demande (memoïsé par fixture_id)
const lineupCache = useRef<Record<number, { home: LineupData | null; away: LineupData | null }>>({})

async function getLineupForFixture(fixtureId: number) {
  if (lineupCache.current[fixtureId]) return lineupCache.current[fixtureId]
  const r = await fetch(`/api/v1/lineups/fixture/${fixtureId}`)
  const data = await r.json()
  lineupCache.current[fixtureId] = data
  return data
}
```

- [ ] **Step 3: Afficher le badge dans chaque carte recommendation**

Pour chaque recommendation, déterminer quelle équipe est concernée (home/away) et afficher la lineup correspondante en dessous du nom du joueur :

```tsx
// Dans le rendu de la carte recommendation :
{lineupForFixture?.home && recommendation.team === fixture.home_team && (
  <LineupDisplay lineup={lineupForFixture.home} />
)}
{lineupForFixture?.away && recommendation.team === fixture.away_team && (
  <LineupDisplay lineup={lineupForFixture.away} />
)}
```

- [ ] **Step 4: TypeScript check**

```bash
cd frontend && npx tsc --noEmit
```

- [ ] **Step 5: Commit**

```bash
git add frontend/src/app/dashboard/recommendations/page.tsx
git commit -m "feat: show lineup badge in recommendation cards"
```

---

## Deploy (VPS)

- [ ] **Rebuild backend + frontend**

```bash
cd /etc/dokploy/compose/ev0-compose-z5hvqt/code
docker compose -p ev0-compose-z5hvqt --env-file .env up -d --build --no-deps backend frontend
```

- [ ] **Appliquer la migration**

```bash
docker exec ev0-compose-z5hvqt-backend-1 alembic upgrade head
```
Expected: `Running upgrade 012 -> 013`

- [ ] **Smoke test API**

```bash
curl http://ev0-compose-z5hvqt-e34c1d-213-130-144-204.traefik.me/api/v1/lineups/fixture/1
```
Expected: `{"fixture_id":1,"home_team":"...","away_team":"...","home":null,"away":null}`

- [ ] **Smoke test frontend**

Ouvrir `/dashboard/lineups`, sélectionner un match, vérifier que le sélecteur de joueurs se charge.
