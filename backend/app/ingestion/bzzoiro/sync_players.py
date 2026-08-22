"""Sync bzz_players from Bzzoiro API."""
from __future__ import annotations

import logging
from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.ingestion.bzzoiro.client import BzzoiroClient
from app.models.bzzoiro import BzzPlayer

logger = logging.getLogger(__name__)


def _parse_date(raw: str | None) -> date | None:
    if not raw:
        return None
    try:
        return date.fromisoformat(raw[:10])
    except (ValueError, TypeError):
        return None


def build_player_values(row: dict[str, Any], now: datetime) -> dict[str, Any] | None:
    """Construit la ligne bzz_players. Rend None si le joueur n'a pas d'identifiant."""
    # ID canonique = le 'id' stable de Bzzoiro. L'API renvoie parfois un
    # 'api_id' supplementaire (autre espace d'identifiants) : le prendre
    # comme cle creait jusqu'a deux lignes par joueur (fusionnees le
    # 10/07/2026 par app/scripts/merge_duplicate_players.py).
    api_id = row.get("id") or row.get("api_id")
    if not api_id:
        return None

    team = row.get("current_team") or {}
    nat_team = row.get("national_team") or {}

    return {
        "api_id": api_id,
        "internal_id": api_id,
        "name": row.get("name", ""),
        "short_name": row.get("short_name"),
        "nationality": row.get("nationality"),
        "date_of_birth": _parse_date(row.get("date_of_birth")),
        "height": row.get("height"),
        "jersey_number": row.get("jersey_number"),
        "position": row.get("position"),
        "market_value": row.get("market_value"),
        # current_team.id fait foi : c'est l'espace de /api/events/ et de
        # /api/player-stats/?event=. api_id releve d'un autre espace et a
        # produit des rattachements faux (Bastoni -> "Gimnastica Torrelavega").
        "current_team_api_id": team.get("id") or team.get("api_id"),
        "current_team_name": team.get("name"),
        "national_team_api_id": nat_team.get("id") or nat_team.get("api_id"),
        "synced_at": now,
    }


async def sync_players_for_team(
    session: AsyncSession, client: BzzoiroClient, team_api_id: int
) -> int:
    """Charge l'effectif d'un club. Rend le nombre de joueurs ecrits."""
    rows = await client.get_all("/api/players/", {"team": team_api_id})
    if not rows:
        return 0

    now = datetime.now(UTC)
    count = 0
    for row in rows:
        values = build_player_values(row, now)
        if values is None:
            continue
        ins = pg_insert(BzzPlayer).values(**values)
        update_set = {
            k: ins.excluded[k] for k in values if k not in ("api_id", "internal_id")
        }
        # Only fill NULL internal_id — never overwrite an existing value
        update_set["internal_id"] = func.coalesce(
            BzzPlayer.__table__.c.internal_id,
            ins.excluded.internal_id,
        )
        await session.execute(
            ins.on_conflict_do_update(index_elements=["api_id"], set_=update_set)
        )
        count += 1

    await session.commit()
    return count


async def sync_players(session: AsyncSession, client: BzzoiroClient) -> int:
    """Charge les effectifs des clubs engages, un appel par club.

    La liste mondiale compte 117 439 joueurs pagines par 50, soit 2 349 pages,
    alors que get_all plafonne a 500 : 78 % des joueurs n'etaient jamais
    rafraichis depuis leur premiere ecriture. Le referentiel compte 96 clubs —
    96 appels suffisent, et ne ramenent que des joueurs du perimetre.
    """
    from app.models.canonical_teams import CanonicalTeam

    clubs = (await session.execute(
        select(CanonicalTeam.bzz_team_id)
        .where(CanonicalTeam.league_api_id.is_not(None))
        .where(CanonicalTeam.bzz_team_id.is_not(None))
    )).scalars().all()

    if not clubs:
        logger.warning(
            "Aucun club engage dans canonical_teams — "
            "lancer app.scripts.rebuild_team_registry d'abord"
        )
        return 0

    total = 0
    for club_id in clubs:
        try:
            total += await sync_players_for_team(session, client, club_id)
        except Exception as exc:
            logger.warning("Echec effectif club %s : %s", club_id, exc)

    logger.info("Effectifs : %d joueurs sur %d clubs", total, len(clubs))
    return total
