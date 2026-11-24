# backend/app/ingestion/odds_storage.py
"""Persist MatchScrapeResult to match_odds_snapshots + player_odds_snapshots."""
from __future__ import annotations

import logging
from datetime import datetime

from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.ingestion.scrape_result import AVEC_SUB, MatchScrapeResult, PlayerOdds
from app.models.match_odds import MatchOddsSnapshot
from app.models.player_odds_snapshot import PlayerOddsSnapshot
from app.services.player_identity import SquadPlayer, load_fixture_squad, resolve_odds_player

logger = logging.getLogger(__name__)


async def store_match_scrape_result(
    result: MatchScrapeResult,
    session: AsyncSession,
    squad_cache: dict[int, list[SquadPlayer]] | None = None,
) -> tuple[int, int]:
    """Write MatchScrapeResult to both snapshot tables.

    Chaque cote joueur porte `player_api_id` : le joueur Atlas résolu contre
    l'effectif des deux équipes du match (chargé une fois par match grâce à
    `squad_cache`, partagé entre bookmakers d'un même passage). Non relié =
    None, compté par la génération des recos qui alerte au-delà de 10 %.

    Returns (match_odds_rows_inserted, player_odds_rows_upserted).
    Uses ON CONFLICT DO NOTHING for match odds (immutable per-timestamp snapshots).
    Uses ON CONFLICT DO UPDATE for player odds (keep latest odds per player).
    """
    now = result.scraped_at

    # ── 1. match_odds_snapshots ───────────────────────────────────────────────
    match_rows: list[dict] = []
    if result.h2h:
        for outcome, odds in result.h2h.items():
            match_rows.append(_match_row(result, "h2h", outcome, odds, now))
    if result.totals:
        for outcome, odds in result.totals.items():
            match_rows.append(_match_row(result, "totals", outcome, odds, now))
    if result.btts:
        for outcome, odds in result.btts.items():
            match_rows.append(_match_row(result, "btts", outcome, odds, now))

    match_inserted = 0
    if match_rows:
        stmt = (
            pg_insert(MatchOddsSnapshot)
            .values(match_rows)
            .on_conflict_do_nothing(
                index_elements=["fixture_id", "bookmaker", "market_type", "outcome", "snapshot_utc"]
            )
        )
        res = await session.execute(stmt)
        match_inserted = res.rowcount or 0

    # ── 2. player_odds_snapshots ──────────────────────────────────────────────
    player_rows_raw: list[dict] = []
    if result.goalscorer or result.assist:
        cache = squad_cache if squad_cache is not None else {}
        if result.fixture_id not in cache:
            cache[result.fixture_id] = await load_fixture_squad(session, result.fixture_id)
        squad = cache[result.fixture_id]
        for p in result.goalscorer:
            player_rows_raw.append(_player_row(result, "goalscorer", p, now, squad))
        for p in result.assist:
            player_rows_raw.append(_player_row(result, "assist", p, now, squad))
        unresolved = sorted({r["player_name"] for r in player_rows_raw if r["player_api_id"] is None})
        if unresolved:
            logger.warning(
                "odds_storage: fixture=%d %s — %d cote(s) joueur non reliée(s) à l'Atlas : %s",
                result.fixture_id, result.bookmaker, len(unresolved), unresolved,
            )

    # Deduplicate on (fixture_id, bookmaker, market_type, player_name).
    # PostgreSQL's ON CONFLICT DO UPDATE cannot handle two rows with the same conflict key
    # in a single batch, so duplicates must be resolved before the INSERT.
    # La cote avec sub l'emporte toujours sur la cote sans sub ; à variante
    # égale, la meilleure cote est gardée.
    #
    # Un même joueur peut arriver sous deux écritures (« Samba, Floyd » dans un
    # marché, « Floyd Samba » dans l'autre) : une fois relié à l'Atlas, c'est
    # l'identifiant qui départage, pour ne jamais garder à la fois sa cote
    # avec sub et sa cote sans sub.
    dedup: dict[tuple, dict] = {}
    for row in player_rows_raw:
        who = row["player_api_id"] if row["player_api_id"] is not None else row["player_name"]
        key = (row["fixture_id"], row["bookmaker"], row["market_type"], who)
        cur = dedup.get(key)
        if cur is None or _row_rank(row) > _row_rank(cur):
            dedup[key] = row
    player_rows = list(dedup.values())

    player_upserted = 0
    if player_rows:
        insert_stmt = pg_insert(PlayerOddsSnapshot).values(player_rows)
        excluded = insert_stmt.excluded
        stmt2 = insert_stmt.on_conflict_do_update(
            index_elements=["fixture_id", "bookmaker", "market_type", "player_name"],
            set_={
                "odds": excluded.odds,
                "variant": excluded.variant,
                "scraped_at": excluded.scraped_at,
                "player_api_id": excluded.player_api_id,
            },
        )
        res2 = await session.execute(stmt2)
        player_upserted = res2.rowcount or 0

    await session.commit()
    logger.debug(
        "odds_storage: fixture=%d %s match_rows=%d player_rows=%d",
        result.fixture_id, result.bookmaker, match_inserted, player_upserted,
    )
    return match_inserted, player_upserted


def _match_row(
    r: MatchScrapeResult,
    market: str,
    outcome: str,
    odds: float,
    now: datetime,
) -> dict:
    return {
        "fixture_id": r.fixture_id,
        "bookmaker": r.bookmaker,
        "market_type": market,
        "outcome": outcome,
        "odds": odds,
        "snapshot_utc": now,
        "source": r.bookmaker,
        "source_url": None,
        "parse_version": "v2",
        "fallback_used": False,
    }


def _row_rank(row: dict) -> tuple[int, float]:
    """Ordre de préférence entre deux cotes du même joueur : avec sub d'abord."""
    return (1 if row["variant"] == AVEC_SUB else 0, row["odds"])


def _player_row(
    r: MatchScrapeResult,
    market: str,
    p: PlayerOdds,
    now: datetime,
    squad: list[SquadPlayer],
) -> dict:
    return {
        "fixture_id": r.fixture_id,
        "bookmaker": r.bookmaker,
        "market_type": market,
        "player_name": p.player_name,
        "player_api_id": resolve_odds_player(p.player_name, squad),
        "odds": p.odds,
        "variant": p.variant,
        "scraped_at": now,
    }


async def backfill_player_api_ids(
    session: AsyncSession,
    fixture_ids: list[int] | None = None,
) -> int:
    """Renseigne `player_api_id` des cotes joueur écrites avant le rapprochement.

    Pour chaque match ayant des cotes sans identifiant (ou pour `fixture_ids`),
    charge l'effectif Atlas une fois et résout chaque nom. Une cote qui reste
    non reliée garde None. Retourne le nombre de lignes reliées.
    """
    from sqlalchemy import select

    stmt = select(PlayerOddsSnapshot).where(PlayerOddsSnapshot.player_api_id.is_(None))
    if fixture_ids is not None:
        if not fixture_ids:
            return 0
        stmt = stmt.where(PlayerOddsSnapshot.fixture_id.in_(fixture_ids))
    rows = list((await session.execute(stmt)).scalars().all())

    by_fixture: dict[int, list[PlayerOddsSnapshot]] = {}
    for row in rows:
        by_fixture.setdefault(row.fixture_id, []).append(row)

    linked = 0
    for fixture_id, fixture_rows in by_fixture.items():
        squad = await load_fixture_squad(session, fixture_id)
        unresolved: set[str] = set()
        for row in fixture_rows:
            api_id = resolve_odds_player(row.player_name, squad)
            if api_id is None:
                unresolved.add(row.player_name)
                continue
            row.player_api_id = api_id
            linked += 1
        if unresolved:
            logger.warning(
                "backfill_player_api_ids: fixture=%d — %d nom(s) toujours non relié(s) : %s",
                fixture_id, len(unresolved), sorted(unresolved),
            )
    await session.commit()
    logger.info("backfill_player_api_ids: %d/%d cotes reliées", linked, len(rows))
    return linked
