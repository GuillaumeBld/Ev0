# backend/app/api/wc2026_lineups.py
"""WC2026 expected lineups CRUD endpoints."""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

import unicodedata

from app.db import get_db
from app.ingestion.wc2026.formations import FORMATIONS, default_minutes_for_role, parse_formation, validate_lineup_formation
from app.ingestion.wc2026.sync_rotowire_lineups import seed_from_rotowire


def _norm_name(name: str) -> str:
    n = unicodedata.normalize("NFKD", name.lower().strip())
    return "".join(c for c in n if not unicodedata.combining(c))


def _lookup_shirt(
    lineup_name: str,
    lineup_position: str,
    squad: list,
) -> int | None:
    """Find shirt number with exact-norm match, then prefix+position fallback.

    Handles Brazilian-style single-name players (e.g. 'Douglas' → 'Douglas Santos').
    """
    key = _norm_name(lineup_name)
    # 1. Exact normalized match
    for sp in squad:
        if _norm_name(sp.player_name) == key:
            return sp.shirt_number
    # 2. Prefix match: squad name starts with lineup name (word boundary)
    candidates = [
        sp for sp in squad
        if _norm_name(sp.player_name).startswith(key + " ")
    ]
    if not candidates:
        return None
    if len(candidates) == 1:
        return candidates[0].shirt_number
    # 3. Disambiguate by position
    pos_filtered = [sp for sp in candidates if sp.position == lineup_position]
    if len(pos_filtered) == 1:
        return pos_filtered[0].shirt_number
    # 4. Still ambiguous: pick lowest shirt number (more established player)
    best = min(pos_filtered or candidates, key=lambda sp: sp.shirt_number or 999)
    return best.shirt_number
from app.models.wc2026 import WC2026SquadPlayer
from app.models.wc2026_lineups import WC2026ExpectedLineup, WC2026ExpectedLineupPlayer
from app.models.bzzoiro import BzzEvent, BzzTeam, BzzPlayerMatchStat

# ── Bzzoiro team name (EN) → French squad nation ──────────────────────────────
_BZZ_TO_NATION: dict[str, str] = {
    "Mexico":                  "Mexique",
    "South Africa":            "Afrique du Sud",
    "South Korea":             "Corée du Sud",
    "Czechia":                 "République Tchèque",
    "Czech Republic":          "République Tchèque",
    "Germany":                 "Allemagne",
    "England":                 "Angleterre",
    "Spain":                   "Espagne",
    "France":                  "France",
    "Brazil":                  "Brésil",
    "Argentina":               "Argentine",
    "Portugal":                "Portugal",
    "Netherlands":             "Pays-Bas",
    "Belgium":                 "Belgique",
    "Croatia":                 "Croatie",
    "Switzerland":             "Suisse",
    "Morocco":                 "Maroc",
    "Senegal":                 "Sénégal",
    "Japan":                   "Japon",
    "Australia":               "Australie",
    "United States":           "États-Unis",
    "USA":                     "États-Unis",
    "Canada":                  "Canada",
    "Uruguay":                 "Uruguay",
    "Colombia":                "Colombie",
    "Ecuador":                 "Équateur",
    "Saudi Arabia":            "Arabie Saoudite",
    "Iran":                    "Iran",
    "Japan":                   "Japon",
    "Ivory Coast":             "Côte d'Ivoire",
    "Côte d'Ivoire":           "Côte d'Ivoire",
    "Ghana":                   "Ghana",
    "Egypt":                   "Égypte",
    "Algeria":                 "Algérie",
    "Tunisia":                 "Tunisie",
    "DR Congo":                "RD Congo",
    "Democratic Republic of Congo": "RD Congo",
    "Cameroon":                "Cameroun",
    "Panama":                  "Panama",
    "Paraguay":                "Paraguay",
    "New Zealand":             "Nouvelle-Zélande",
    "Scotland":                "Écosse",
    "Norway":                  "Norvège",
    "Sweden":                  "Suède",
    "Austria":                 "Autriche",
    "Turkey":                  "Turquie",
    "Uzbekistan":              "Ouzbékistan",
    "Jordan":                  "Jordanie",
    "Iraq":                    "Irak",
    "Qatar":                   "Qatar",
    "Cape Verde":              "Cap-Vert",
    "Haiti":                   "Haïti",
    "Curaçao":                 "Curaçao",
    "Bosnia and Herzegovina":  "Bosnie-Herzégovine",
    "Bosnia":                  "Bosnie-Herzégovine",
    "Costa Rica":              "Costa Rica",
    "Honduras":                "Honduras",
}

_ROUND_TO_CONTEXT: dict[int, str] = {
    1: "matchday_1",
    2: "matchday_2",
    3: "matchday_3",
}

_BZZ_POS_TO_LINEUP: dict[str, str] = {
    "G": "GK", "D": "DEF", "M": "MID", "F": "FWD",
}


def _parse_formation_flexible(formation: str | None) -> list[int]:
    """Parse formation string to line counts, with fallback to dynamic parsing."""
    if not formation:
        return [4, 3, 3]
    if formation in FORMATIONS:
        return FORMATIONS[formation]
    # Dynamic: "4-1-4-1" → [4,1,4,1]
    parts = [int(x) for x in formation.split("-") if x.isdigit()]
    return parts if len(parts) >= 2 else [4, 3, 3]


def _assign_to_lines(players: list[dict], formation: str | None) -> list[dict]:
    """Assign line_index and slot_index to starters based on formation."""
    lines = _parse_formation_flexible(formation)
    pos_order = {"G": 0, "D": 1, "M": 2, "F": 3}
    sorted_p = sorted(players, key=lambda p: pos_order.get(p["bzz_pos"], 2))
    gks = [p for p in sorted_p if p["bzz_pos"] == "G"]
    outfield = [p for p in sorted_p if p["bzz_pos"] != "G"]

    result = []
    for i, gk in enumerate(gks):
        result.append({**gk, "line_index": 0, "slot_index": i})

    cursor = 0
    for line_idx, count in enumerate(lines, start=1):
        for slot_idx in range(count):
            if cursor < len(outfield):
                result.append({**outfield[cursor], "line_index": line_idx, "slot_index": slot_idx})
                cursor += 1

    return result


def _find_squad_name(bzz_name: str, bzz_pos: str, squad: list) -> str:
    """Match a Bzzoiro player name to the canonical squad player name."""
    pos_map = {"G": "GK", "D": "DEF", "M": "MID", "F": "FWD"}
    lineup_pos = pos_map.get(bzz_pos, bzz_pos)
    norm = _norm_name(bzz_name)
    # 1. Exact
    for sp in squad:
        if _norm_name(sp.player_name) == norm:
            return sp.player_name
    # 2. Last word (last name) match
    last = norm.split()[-1]
    candidates = [sp for sp in squad if last in _norm_name(sp.player_name).split()]
    if len(candidates) == 1:
        return candidates[0].player_name
    if len(candidates) > 1:
        pos_match = [sp for sp in candidates if sp.position == lineup_pos]
        if len(pos_match) == 1:
            return pos_match[0].player_name
    # 3. No match: return Bzzoiro name as-is
    return bzz_name


async def sync_match_lineups_to_editor(
    bzz_id: int,
    session: AsyncSession,
) -> dict[str, str]:
    """Auto-fill lineup editor from a confirmed Bzzoiro match lineup.

    Returns a dict of {nation: status} for both teams.
    Only fills matchday_1/2/3. Skips if source='manual'.
    """
    result = await session.execute(
        select(BzzEvent).where(BzzEvent.api_id == bzz_id)
    )
    event = result.scalar_one_or_none()
    if not event or event.status != "finished":
        return {}
    round_num = event.round_number
    if round_num not in _ROUND_TO_CONTEXT:
        return {}
    context = _ROUND_TO_CONTEXT[round_num]

    lineups_data = event.lineups
    if not isinstance(lineups_data, dict) or not lineups_data:
        return {}

    # Load team names
    team_ids = [x for x in [event.home_team_api_id, event.away_team_api_id] if x]
    teams_r = await session.execute(
        select(BzzTeam.api_id, BzzTeam.name).where(BzzTeam.api_id.in_(team_ids))
    )
    team_names: dict[int, str] = {r.api_id: r.name for r in teams_r.all()}

    # Load player stats for minutes
    stats_r = await session.execute(
        select(BzzPlayerMatchStat.player_api_id, BzzPlayerMatchStat.minutes_played, BzzPlayerMatchStat.is_home)
        .where(BzzPlayerMatchStat.event_api_id == bzz_id)
    )
    minutes_by_pid: dict[int, int] = {}
    is_home_by_pid: dict[int, bool] = {}
    for row in stats_r.all():
        minutes_by_pid[row.player_api_id] = row.minutes_played or 0
        is_home_by_pid[row.player_api_id] = bool(row.is_home)

    statuses: dict[str, str] = {}

    for side_key, team_id in [("home", event.home_team_api_id), ("away", event.away_team_api_id)]:
        bzz_name = team_names.get(team_id, "")
        nation = _BZZ_TO_NATION.get(bzz_name)
        if not nation:
            statuses[bzz_name] = "no_nation_mapping"
            continue

        side_data = lineups_data.get(side_key) or {}
        formation = side_data.get("formation")
        raw_starters = side_data.get("players") or []
        raw_subs = side_data.get("substitutes") or []

        if not raw_starters:
            statuses[nation] = "no_lineup_data"
            continue

        # Check existing lineup
        existing_r = await session.execute(
            select(WC2026ExpectedLineup)
            .options(selectinload(WC2026ExpectedLineup.players))
            .where(
                WC2026ExpectedLineup.nation == nation,
                WC2026ExpectedLineup.context == context,
            )
        )
        existing = existing_r.scalar_one_or_none()
        if existing and existing.source == "manual":
            statuses[nation] = "skipped_manual"
            continue

        # Load squad for name matching
        squad_r = await session.execute(
            select(WC2026SquadPlayer).where(WC2026SquadPlayer.nation == nation)
        )
        squad = squad_r.scalars().all()

        # Build starter entries with line/slot assignments
        starter_entries = [
            {
                "bzz_id": p.get("id"),
                "bzz_pos": p.get("position", "M"),
                "name": _find_squad_name(p.get("name", "?"), p.get("position", "M"), squad),
            }
            for p in raw_starters
        ]
        assigned = _assign_to_lines(starter_entries, formation)

        # Build sub entries
        sub_entries = [
            {
                "bzz_id": p.get("id"),
                "bzz_pos": p.get("position", "M"),
                "name": _find_squad_name(p.get("name", "?"), p.get("position", "M"), squad),
            }
            for p in raw_subs
        ]

        # Upsert lineup header
        if existing:
            existing.formation = formation or "4-3-3"
            existing.source = "bzzoiro"
            existing.updated_at = datetime.now(timezone.utc)
            for p in existing.players:
                await session.delete(p)
            await session.flush()
            lineup = existing
        else:
            lineup = WC2026ExpectedLineup(
                nation=nation,
                context=context,
                formation=formation or "4-3-3",
                source="bzzoiro",
            )
            session.add(lineup)
            await session.flush()

        # Insert starters
        for entry in assigned:
            pid = entry.get("bzz_id")
            mins = minutes_by_pid.get(pid, 90) if pid else 90
            mins = max(0, min(120, mins))
            lineup_pos = _BZZ_POS_TO_LINEUP.get(entry["bzz_pos"], "MID")
            session.add(WC2026ExpectedLineupPlayer(
                lineup_id=lineup.id,
                player_name=entry["name"],
                position=lineup_pos,
                line_index=entry["line_index"],
                slot_index=entry["slot_index"],
                is_starter=True,
                role="starter",
                expected_minutes=mins,
            ))

        # Insert substitutes
        for slot_idx, entry in enumerate(sub_entries):
            pid = entry.get("bzz_id")
            mins = minutes_by_pid.get(pid, 0) if pid else 0
            mins = max(0, min(120, mins))
            role = "sub_planned" if mins > 0 else "reserve"
            lineup_pos = _BZZ_POS_TO_LINEUP.get(entry["bzz_pos"], "MID")
            session.add(WC2026ExpectedLineupPlayer(
                lineup_id=lineup.id,
                player_name=entry["name"],
                position=lineup_pos,
                line_index=-1,
                slot_index=slot_idx,
                is_starter=False,
                role=role,
                expected_minutes=mins,
            ))

        await session.commit()
        statuses[nation] = "synced"

    return statuses

router = APIRouter(prefix="/wc2026/lineups", tags=["wc2026"])

_VALID_CONTEXTS = {
    "default", "matchday_1", "matchday_2", "matchday_3",
    "r16", "qf", "sf", "final",
}


def _context_valid(ctx: str) -> bool:
    return ctx in _VALID_CONTEXTS


def _role_to_minutes(role: str) -> int:
    return default_minutes_for_role(role)


# ── Pydantic schemas ──────────────────────────────────────────────────────────

class LineupPlayerIn(BaseModel):
    player_name: str
    position: str       # GK / DEF / MID / FWD
    line_index: int
    slot_index: int
    is_starter: bool = True
    role: str           # starter | sub_planned | sub_tactical | reserve
    expected_minutes: int


class LineupUpsertIn(BaseModel):
    formation: str
    players: list[LineupPlayerIn]


class LineupPlayerOut(BaseModel):
    player_name: str
    position: str
    line_index: int
    slot_index: int
    is_starter: bool
    role: str
    expected_minutes: int
    shirt_number: int | None = None


class LineupOut(BaseModel):
    nation: str
    context: str
    formation: str
    source: str
    players: list[LineupPlayerOut]


class NationStatusOut(BaseModel):
    nation: str
    group_letter: str
    flag_emoji: str | None
    complete: bool     # True if a "default" lineup with 11 starters exists
    starters_count: int


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.get("", response_model=list[NationStatusOut])
async def list_nations(session: AsyncSession = Depends(get_db)) -> list[NationStatusOut]:
    """List all WC2026 nations with their lineup completion status."""
    # Distinct nations from squad
    nations_result = await session.execute(
        select(
            WC2026SquadPlayer.nation,
            WC2026SquadPlayer.group_letter,
            WC2026SquadPlayer.flag_emoji,
        ).distinct(WC2026SquadPlayer.group_letter, WC2026SquadPlayer.nation).order_by(
            WC2026SquadPlayer.group_letter, WC2026SquadPlayer.nation
        )
    )
    nations = nations_result.all()

    # Load default lineups
    lineups_result = await session.execute(
        select(WC2026ExpectedLineup).where(WC2026ExpectedLineup.context == "default")
    )
    lineups_by_nation: dict[str, WC2026ExpectedLineup] = {
        l.nation: l for l in lineups_result.scalars().all()
    }

    # Load starter counts for default lineups in one query
    starters_count: dict[str, int] = {}
    if lineups_by_nation:
        lineup_ids = [l.id for l in lineups_by_nation.values()]
        id_to_nation = {l.id: n for n, l in lineups_by_nation.items()}
        counts_result = await session.execute(
            select(WC2026ExpectedLineupPlayer.lineup_id, func.count())
            .where(
                WC2026ExpectedLineupPlayer.lineup_id.in_(lineup_ids),
                WC2026ExpectedLineupPlayer.is_starter.is_(True),
            )
            .group_by(WC2026ExpectedLineupPlayer.lineup_id)
        )
        for lineup_id, count in counts_result.all():
            nation = id_to_nation[lineup_id]
            starters_count[nation] = count

    out = []
    for row in nations:
        nation = row.nation
        count = starters_count.get(nation, 0)
        out.append(NationStatusOut(
            nation=nation,
            group_letter=row.group_letter,
            flag_emoji=row.flag_emoji,
            complete=count == 11,
            starters_count=count,
        ))
    return out


@router.get("/{nation}", response_model=dict)
async def get_nation_lineups(
    nation: str,
    session: AsyncSession = Depends(get_db),
) -> dict:
    """Return all lineups for a nation (default + matchday overrides) and the full squad."""
    # Squad players for the panel
    squad_result = await session.execute(
        select(WC2026SquadPlayer).where(WC2026SquadPlayer.nation == nation).order_by(
            WC2026SquadPlayer.position, WC2026SquadPlayer.shirt_number
        )
    )
    squad = squad_result.scalars().all()
    if not squad:
        raise HTTPException(status_code=404, detail=f"Nation not found: {nation}")

    # All lineups for this nation
    lineups_result = await session.execute(
        select(WC2026ExpectedLineup).where(WC2026ExpectedLineup.nation == nation)
    )
    lineups = lineups_result.scalars().all()

    # Load players for each lineup
    lineups_out: dict[str, LineupOut] = {}
    for lineup in lineups:
        players_result = await session.execute(
            select(WC2026ExpectedLineupPlayer).where(
                WC2026ExpectedLineupPlayer.lineup_id == lineup.id
            ).order_by(
                WC2026ExpectedLineupPlayer.line_index,
                WC2026ExpectedLineupPlayer.slot_index,
            )
        )
        players = players_result.scalars().all()
        lineups_out[lineup.context] = LineupOut(
            nation=lineup.nation,
            context=lineup.context,
            formation=lineup.formation,
            source=lineup.source,
            players=[
                LineupPlayerOut(
                    player_name=p.player_name,
                    position=p.position,
                    line_index=p.line_index,
                    slot_index=p.slot_index,
                    is_starter=p.is_starter,
                    role=p.role,
                    expected_minutes=p.expected_minutes,
                    shirt_number=_lookup_shirt(p.player_name, p.position, squad),
                )
                for p in players
            ],
        )

    return {
        "nation": nation,
        "flag_emoji": squad[0].flag_emoji,
        "squad": [
            {
                "player_name": p.player_name,
                "position": p.position,
                "shirt_number": p.shirt_number,
            }
            for p in squad
        ],
        "lineups": {ctx: lo.model_dump() for ctx, lo in lineups_out.items()},
    }


@router.put("/{nation}/{context}", response_model=LineupOut)
async def upsert_lineup(
    nation: str,
    context: str,
    body: LineupUpsertIn,
    session: AsyncSession = Depends(get_db),
) -> LineupOut:
    """Create or replace a lineup for a nation+context. Replaces all players atomically."""
    if not _context_valid(context):
        raise HTTPException(status_code=422, detail=f"Invalid context: {context!r}")

    if body.formation not in FORMATIONS:
        raise HTTPException(status_code=422, detail=f"Unknown formation: {body.formation!r}")

    # Validate starter count — 10 outfield + exactly 1 GK
    starters = [p for p in body.players if p.is_starter]
    gk_count = sum(1 for p in starters if p.line_index == 0)
    if gk_count != 1:
        raise HTTPException(status_code=422, detail=f"Expected exactly 1 GK (line_index=0), got {gk_count}")
    try:
        validate_lineup_formation(
            body.formation,
            [p.model_dump() for p in starters if p.line_index > 0],
        )
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))

    # Upsert lineup header — eager-load players to avoid MissingGreenlet in async
    result = await session.execute(
        select(WC2026ExpectedLineup)
        .options(selectinload(WC2026ExpectedLineup.players))
        .where(
            WC2026ExpectedLineup.nation == nation,
            WC2026ExpectedLineup.context == context,
        )
    )
    lineup = result.scalar_one_or_none()
    if lineup is None:
        lineup = WC2026ExpectedLineup(nation=nation, context=context, formation=body.formation, source="manual")
        session.add(lineup)
        await session.flush()
    else:
        lineup.formation = body.formation
        lineup.source = "manual"
        lineup.updated_at = datetime.now(timezone.utc)
        for player in lineup.players:
            await session.delete(player)
        await session.flush()

    # Insert new players
    for p in body.players:
        session.add(WC2026ExpectedLineupPlayer(
            lineup_id=lineup.id,
            player_name=p.player_name,
            position=p.position,
            line_index=p.line_index,
            slot_index=p.slot_index,
            is_starter=p.is_starter,
            role=p.role,
            expected_minutes=p.expected_minutes,
        ))

    await session.commit()
    await session.refresh(lineup)

    players_result = await session.execute(
        select(WC2026ExpectedLineupPlayer).where(
            WC2026ExpectedLineupPlayer.lineup_id == lineup.id
        ).order_by(
            WC2026ExpectedLineupPlayer.line_index,
            WC2026ExpectedLineupPlayer.slot_index,
        )
    )
    players = players_result.scalars().all()

    squad_result = await session.execute(
        select(WC2026SquadPlayer).where(WC2026SquadPlayer.nation == nation)
    )
    squad = squad_result.scalars().all()

    return LineupOut(
        nation=lineup.nation,
        context=lineup.context,
        formation=lineup.formation,
        source=lineup.source,
        players=[
            LineupPlayerOut(
                player_name=p.player_name,
                position=p.position,
                line_index=p.line_index,
                slot_index=p.slot_index,
                is_starter=p.is_starter,
                role=p.role,
                expected_minutes=p.expected_minutes,
                shirt_number=_lookup_shirt(p.player_name, p.position, squad),
            )
            for p in players
        ],
    )


@router.post("/sync-from-matches")
async def sync_from_matches(session: AsyncSession = Depends(get_db)) -> dict:
    """Scan all finished WC2026 group stage matches and auto-fill lineup editor from Bzzoiro."""
    finished_r = await session.execute(
        select(BzzEvent.api_id)
        .where(
            BzzEvent.league_api_id == 27,
            BzzEvent.status == "finished",
            BzzEvent.round_number.in_([1, 2, 3]),
            BzzEvent.lineups.isnot(None),
        )
    )
    bzz_ids = [row[0] for row in finished_r.all()]

    all_statuses: dict[str, str] = {}
    for bzz_id in bzz_ids:
        statuses = await sync_match_lineups_to_editor(bzz_id, session)
        all_statuses.update(statuses)

    synced = sum(1 for s in all_statuses.values() if s == "synced")
    return {"synced": synced, "total_matches": len(bzz_ids), "detail": all_statuses}


@router.post("/sync-rotowire")
async def sync_rotowire(session: AsyncSession = Depends(get_db)) -> dict:
    """Scrape Rotowire and pre-populate missing lineups (skips manual lineups)."""
    statuses = await seed_from_rotowire(session)
    seeded = sum(1 for s in statuses.values() if s == "seeded")
    skipped = sum(1 for s in statuses.values() if s == "skipped_manual")
    no_match = sum(1 for s in statuses.values() if s == "no_match")
    return {"seeded": seeded, "skipped_manual": skipped, "no_match": no_match, "detail": statuses}
