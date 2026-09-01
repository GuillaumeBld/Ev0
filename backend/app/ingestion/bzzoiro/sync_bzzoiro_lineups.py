"""Parse lineups from bzz_events.lineups JSONB → TeamLineup / TeamLineupPlayer.

bzz_events.lineups is populated by sync_events from the Bzzoiro events API.
This module reads those JSONB blobs and writes official-quality lineup rows
so lineup_resolver can use them (lineup_type="bzzoiro").

Run every 30 min. Lineups appear in Bzzoiro ~1h before KO.

For WC2026 matches, lineups are also written to wc2026_expected_lineups with
the appropriate knockout context (r32, r16, qf, sf, final, 3rd_place) so the
WC2026 lineups page and the calculator share the same data.
"""
from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.bzzoiro import BzzEvent, BzzPlayer
from app.models.fixtures import Fixture
from app.models.lineups import TeamLineup, TeamLineupPlayer
from app.models.wc2026_lineups import WC2026ExpectedLineup, WC2026ExpectedLineupPlayer

logger = logging.getLogger(__name__)

_POSITION_MAP: dict[str, str] = {
    "G": "GK",
    "D": "DEF",
    "M": "MID",
    "F": "FWD",
}

# Bzzoiro round_number → wc2026_expected_lineups context
_ROUND_TO_CONTEXT: dict[int, str] = {
    1: "matchday_1",
    2: "matchday_2",
    3: "matchday_3",
    6: "r32",
    5: "r16",
    27: "qf",
    28: "sf",
    29: "final",
    50: "3rd_place",
}

# Bzzoiro English team name → French nation (wc2026_expected_lineups.nation)
_BZZ_TO_NATION: dict[str, str] = {
    "Algeria":                "Algérie",
    "Argentina":              "Argentine",
    "Australia":              "Australie",
    "Austria":                "Autriche",
    "Belgium":                "Belgique",
    "Bosnia and Herzegovina": "Bosnie-Herzégovine",
    "Bosnia & Herzegovina":   "Bosnie-Herzégovine",
    "Brazil":                 "Brésil",
    "Cabo Verde":             "Cap-Vert",
    "Canada":                 "Canada",
    "Colombia":               "Colombie",
    "Croatia":                "Croatie",
    "Curaçao":                "Curaçao",
    "Czechia":                "République Tchèque",
    "Czech Republic":         "République Tchèque",
    "Côte d'Ivoire":          "Côte d'Ivoire",
    "DR Congo":               "RD Congo",
    "Ecuador":                "Équateur",
    "Egypt":                  "Égypte",
    "England":                "Angleterre",
    "France":                 "France",
    "Germany":                "Allemagne",
    "Ghana":                  "Ghana",
    "Haiti":                  "Haïti",
    "Iran":                   "Iran",
    "Iraq":                   "Irak",
    "Ivory Coast":            "Côte d'Ivoire",
    "Japan":                  "Japon",
    "Jordan":                 "Jordanie",
    "Mexico":                 "Mexique",
    "Morocco":                "Maroc",
    "Netherlands":            "Pays-Bas",
    "New Zealand":            "Nouvelle-Zélande",
    "Norway":                 "Norvège",
    "Panama":                 "Panama",
    "Paraguay":               "Paraguay",
    "Portugal":               "Portugal",
    "Qatar":                  "Qatar",
    "Saudi Arabia":           "Arabie Saoudite",
    "Scotland":               "Écosse",
    "Senegal":                "Sénégal",
    "South Africa":           "Afrique du Sud",
    "South Korea":            "Corée du Sud",
    "Spain":                  "Espagne",
    "Sweden":                 "Suède",
    "Switzerland":            "Suisse",
    "Tunisia":                "Tunisie",
    "Turkey":                 "Turquie",
    "Türkiye":                "Turquie",
    "USA":                    "États-Unis",
    "United States":          "États-Unis",
    "Uruguay":                "Uruguay",
    "Uzbekistan":             "Ouzbékistan",
}


def _map_position(raw: str | None) -> str:
    if not raw:
        return "MID"
    return _POSITION_MAP.get(raw.strip().upper(), "MID")


async def sync_bzzoiro_lineups(
    session: AsyncSession,
    days_forward: int = 2,
) -> int:
    """Extract lineups from upcoming bzz_events and write to TeamLineup.

    Only events with non-null lineups and a matching Fixture are processed.
    Existing lineup rows with lineup_type="bzzoiro" are overwritten.

    For WC2026 fixtures, also writes to wc2026_expected_lineups with the
    appropriate round context (r32, r16, qf, sf, final, 3rd_place).

    Player names are resolved from bzz_players using the player API ID so
    the pricing engine can match them correctly (Bzzoiro JSONB uses short
    names like "E. Haaland", but bzz_players stores "Erling Haaland").

    Args:
        session: Async SQLAlchemy session.
        days_forward: Look ahead window in days.

    Returns:
        Number of TeamLineup rows created/updated (one per team per fixture).
    """
    now = datetime.now(UTC)
    cutoff = now + timedelta(days=days_forward)
    # Inclure les matchs commencés dans les 2 dernières heures (compos publiées avant KO)
    lookback = now - timedelta(hours=2)

    result = await session.execute(
        select(BzzEvent).where(
            BzzEvent.event_date >= lookback,
            BzzEvent.event_date <= cutoff,
            BzzEvent.lineups.is_not(None),
        )
    )
    events = result.scalars().all()

    count = 0
    for event in events:
        if not event.lineups:
            continue

        # Deux formes coexistent dans la colonne. sync_match_detail y archive
        # la reponse v2 entiere -- {"lineups": {...}, "lineup_status": ...} --
        # pour ne pas perdre le statut ni l'horodatage. Les ecritures plus
        # anciennes n'y mettaient que le bloc par camp. On deballe la premiere,
        # on prend la seconde telle quelle.
        brut: dict[str, Any] = event.lineups
        lineups_data: dict[str, Any] = brut.get("lineups") or brut

        # Collect all player API IDs so we can bulk-load full names
        all_player_ids: set[int] = set()
        for side in ("home", "away"):
            side_data = lineups_data.get(side) or {}
            for section in ("players", "substitutes"):
                for p in side_data.get(section) or []:
                    if p.get("id"):
                        all_player_ids.add(int(p["id"]))

        # Bulk-load full names from bzz_players (full names match pricing engine)
        player_name_map: dict[int, str] = {}
        if all_player_ids:
            name_res = await session.execute(
                select(BzzPlayer.api_id, BzzPlayer.name)
                .where(BzzPlayer.api_id.in_(all_player_ids))
            )
            for api_id, name in name_res.all():
                player_name_map[api_id] = name

        fixture_result = await session.execute(
            select(Fixture).where(
                Fixture.external_id == f"bzz_{event.api_id}"
            ).limit(1)
        )
        fixture = fixture_result.scalar_one_or_none()
        if fixture is None:
            continue

        is_wc2026 = fixture.league == "world_cup_2026"
        wc_context = _ROUND_TO_CONTEXT.get(event.round_number) if event.round_number else None

        team_map = {
            "home": fixture.home_team,
            "away": fixture.away_team,
        }

        for side, team_name in team_map.items():
            side_data = lineups_data.get(side) or {}
            players_raw = side_data.get("players") or []
            substitutes_raw = side_data.get("substitutes") or []
            formation = side_data.get("formation") or ""

            if not players_raw:
                continue

            # ── team_lineups (calculator + lineup_resolver) ───────────────────
            existing_result = await session.execute(
                select(TeamLineup).where(
                    TeamLineup.fixture_id == fixture.id,
                    TeamLineup.team == team_name,
                    TeamLineup.lineup_type == "bzzoiro",
                ).limit(1)
            )
            existing = existing_result.scalar_one_or_none()
            if existing is not None:
                await session.delete(existing)
                await session.flush()

            lineup = TeamLineup(
                fixture_id=fixture.id,
                team=team_name,
                lineup_type="bzzoiro",
                source="bzzoiro",
                created_by="sync_bzzoiro_lineups",
            )
            session.add(lineup)
            await session.flush()

            for p in players_raw:
                pid = int(p["id"]) if p.get("id") else None
                # Prefer full name from bzz_players; fall back to JSONB name
                full_name = player_name_map.get(pid, p.get("name", "")) if pid else p.get("name", "")
                session.add(TeamLineupPlayer(
                    lineup_id=lineup.id,
                    player_name=full_name,
                    position=_map_position(p.get("position")),
                    is_starter=True,
                    jersey_number=int(p["jersey_number"]) if p.get("jersey_number") else None,
                ))
            for p in substitutes_raw:
                pid = int(p["id"]) if p.get("id") else None
                full_name = player_name_map.get(pid, p.get("name", "")) if pid else p.get("name", "")
                session.add(TeamLineupPlayer(
                    lineup_id=lineup.id,
                    player_name=full_name,
                    position=_map_position(p.get("position")),
                    is_starter=False,
                    jersey_number=int(p["jersey_number"]) if p.get("jersey_number") else None,
                ))

            count += 1
            logger.info(
                "sync_bzzoiro_lineups: %s — %s (%d starters, %d subs)",
                team_name, fixture.external_id, len(players_raw), len(substitutes_raw),
            )

            # ── wc2026_expected_lineups (WC2026 lineups page + calculator fallback) ─
            if is_wc2026 and wc_context:
                nation = _BZZ_TO_NATION.get(team_name, team_name)
                await _upsert_wc2026_expected_lineup(
                    session=session,
                    nation=nation,
                    context=wc_context,
                    formation=formation,
                    players_raw=players_raw,
                    substitutes_raw=substitutes_raw,
                    player_name_map=player_name_map,
                )

    await session.commit()
    logger.info("sync_bzzoiro_lineups: %d team lineups written", count)
    return count


async def _upsert_wc2026_expected_lineup(
    session: AsyncSession,
    nation: str,
    context: str,
    formation: str,
    players_raw: list[dict],
    substitutes_raw: list[dict],
    player_name_map: dict[int, str],
) -> None:
    """Write/replace a WC2026 expected lineup from Bzzoiro pre-match data."""
    # Delete existing entry for (nation, context)
    existing_res = await session.execute(
        select(WC2026ExpectedLineup).where(
            WC2026ExpectedLineup.nation == nation,
            WC2026ExpectedLineup.context == context,
        ).limit(1)
    )
    existing = existing_res.scalar_one_or_none()
    if existing is not None:
        await session.delete(existing)
        await session.flush()

    wc_lineup = WC2026ExpectedLineup(
        nation=nation,
        context=context,
        formation=formation or "4-3-3",
        source="bzzoiro",
    )
    session.add(wc_lineup)
    await session.flush()

    # Assign line_index based on position order (GK=0, DEF=1, MID=2, FWD=3)
    line_counters: dict[str, int] = {}
    line_for_pos = {"G": 0, "D": 1, "M": 2, "F": 3}

    for p in players_raw:
        pid = int(p["id"]) if p.get("id") else None
        full_name = player_name_map.get(pid, p.get("name", "")) if pid else p.get("name", "")
        if not full_name:
            continue
        bzz_pos = (p.get("position") or "M").upper()
        line_idx = line_for_pos.get(bzz_pos, 2)
        slot_idx = line_counters.get(bzz_pos, 0)
        line_counters[bzz_pos] = slot_idx + 1
        session.add(WC2026ExpectedLineupPlayer(
            lineup_id=wc_lineup.id,
            player_name=full_name,
            position=_map_position(p.get("position")),
            line_index=line_idx,
            slot_index=slot_idx,
            is_starter=True,
            role="starter",
            expected_minutes=85,
        ))

    sub_counters: dict[str, int] = {}
    for p in substitutes_raw:
        pid = int(p["id"]) if p.get("id") else None
        full_name = player_name_map.get(pid, p.get("name", "")) if pid else p.get("name", "")
        if not full_name:
            continue
        bzz_pos = (p.get("position") or "M").upper()
        line_idx = line_for_pos.get(bzz_pos, 2)
        slot_idx = sub_counters.get(bzz_pos, 0)
        sub_counters[bzz_pos] = slot_idx + 1
        session.add(WC2026ExpectedLineupPlayer(
            lineup_id=wc_lineup.id,
            player_name=full_name,
            position=_map_position(p.get("position")),
            line_index=line_idx,
            slot_index=slot_idx,
            is_starter=False,
            role="reserve",
            expected_minutes=20,
        ))

    logger.info(
        "sync_bzzoiro_lineups: WC2026 expected lineup upserted — %s %s (%s, %d starters)",
        nation, context, formation, len(players_raw),
    )
