"""Scrape Rotowire WC2026 probable lineups and seed wc2026_expected_lineups."""
from __future__ import annotations

import logging
import re
import unicodedata
from collections import defaultdict

import httpx
from bs4 import BeautifulSoup
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ingestion.wc2026.formations import FORMATIONS, default_minutes_for_role
from app.models.wc2026_lineups import WC2026ExpectedLineup, WC2026ExpectedLineupPlayer

logger = logging.getLogger(__name__)

ROTOWIRE_URL = "https://www.rotowire.com/soccer/lineups.php?league=WOC"

# Rotowire position abbreviation → DB position
_POS_MAP = {
    "GK": "GK",
    "DL": "DEF", "DC": "DEF", "DR": "DEF",
    "DMC": "MID", "MC": "MID", "ML": "MID", "MR": "MID",
    "AMC": "MID", "AML": "MID", "AMR": "MID",
    "FW": "FWD", "FWL": "FWD", "FWR": "FWD",
}

# Line index for Rotowire position (GK=0, DEF=1, MID=2, FWD=3 — simplified for seeder)
_LINE_MAP = {
    "GK": 0,
    "DL": 1, "DC": 1, "DR": 1,
    "DMC": 2, "MC": 2, "ML": 2, "MR": 2,
    "AMC": 2, "AML": 2, "AMR": 2,
    "FW": 3, "FWL": 3, "FWR": 3,
}


def _normalize(name: str) -> str:
    n = name.lower().strip()
    n = unicodedata.normalize("NFKD", n)
    n = "".join(c for c in n if not unicodedata.combining(c))
    return re.sub(r"['.,-]", " ", n).strip()


async def scrape_rotowire_lineups() -> dict[str, list[dict]]:
    """Fetch Rotowire WC lineups page. Returns {team_name: [player_dicts]}.

    Raises httpx.HTTPError on network/HTTP failure.
    """
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(ROTOWIRE_URL, headers={"User-Agent": "Mozilla/5.0"})
            resp.raise_for_status()
    except httpx.TimeoutException as exc:
        logger.error("Rotowire scrape timed out: %s", exc)
        raise
    except httpx.HTTPStatusError as exc:
        logger.error("Rotowire HTTP error %s: %s", exc.response.status_code, exc)
        raise

    soup = BeautifulSoup(resp.text, "html.parser")
    result: dict[str, list[dict]] = {}

    # Each lineup block has class "lineup" with a team name and player list
    for lineup_div in soup.select(".lineup__list"):
        parent = lineup_div.find_parent(class_="lineup")
        if parent is None:
            continue
        team_header = parent.select_one(".lineup__team-name")
        if team_header is None:
            continue
        team_name = team_header.get_text(strip=True)

        players = []
        for player_el in lineup_div.select(".lineup__player"):
            name_el = player_el.select_one(".lineup__name")
            pos_el = player_el.select_one(".lineup__pos")
            if name_el is None:
                continue
            player_name = name_el.get_text(strip=True)
            pos_abbr = pos_el.get_text(strip=True) if pos_el else "MC"
            position = _POS_MAP.get(pos_abbr, "MID")
            line_index = _LINE_MAP.get(pos_abbr, 2)
            players.append({
                "player_name": player_name,
                "position": position,
                "line_index": line_index,
            })

        if players:
            result[team_name] = players

    return result


async def seed_from_rotowire(session: AsyncSession) -> dict[str, str]:
    """Seed wc2026_expected_lineups from Rotowire. Returns {nation: status}.

    Status values: "seeded" | "skipped_manual" | "no_match" | "invalid_formation" | "missing_gk"
    """
    from app.models.wc2026 import WC2026SquadPlayer  # local import avoids circular

    raw = await scrape_rotowire_lineups()
    statuses: dict[str, str] = {}

    # Build nation lookup from DB
    nations_result = await session.execute(
        select(WC2026SquadPlayer.nation).distinct()
    )
    db_nations = {_normalize(n): n for n in (row[0] for row in nations_result.all())}

    for rw_team, players in raw.items():
        norm = _normalize(rw_team)
        db_nation = db_nations.get(norm)
        if db_nation is None:
            logger.warning("Rotowire: no DB match for %r (normalized: %r)", rw_team, norm)
            statuses[rw_team] = "no_match"
            continue

        # Skip if a manual lineup already exists
        existing_result = await session.execute(
            select(WC2026ExpectedLineup).where(
                WC2026ExpectedLineup.nation == db_nation,
                WC2026ExpectedLineup.context == "default",
            )
        )
        existing_lineup = existing_result.scalar_one_or_none()
        if existing_lineup is not None and existing_lineup.source == "manual":
            statuses[db_nation] = "skipped_manual"
            continue

        # Validate GK presence
        gk_players = [p for p in players if p["position"] == "GK"][:1]
        if not gk_players:
            logger.warning("Rotowire: no GK found for %r, skipping", db_nation)
            statuses[db_nation] = "missing_gk"
            continue

        # Determine formation from player positions
        outfield = [p for p in players if p["position"] != "GK"][:10]
        defs = sum(1 for p in outfield if p["position"] == "DEF")
        mids = sum(1 for p in outfield if p["position"] == "MID")
        fwds = sum(1 for p in outfield if p["position"] == "FWD")
        formation_str = f"{defs}-{mids}-{fwds}"
        if formation_str not in FORMATIONS:
            logger.warning(
                "Rotowire: inferred formation %r not in FORMATIONS for %r, skipping",
                formation_str, db_nation,
            )
            statuses[db_nation] = "invalid_formation"
            continue

        all_starters = gk_players + outfield

        if existing_lineup is None:
            lineup = WC2026ExpectedLineup(
                nation=db_nation,
                context="default",
                formation=formation_str,
                source="rotowire",
            )
            session.add(lineup)
            await session.flush()
        else:
            lineup = existing_lineup
            lineup.formation = formation_str
            lineup.source = "rotowire"
            existing_players = await session.execute(
                select(WC2026ExpectedLineupPlayer).where(
                    WC2026ExpectedLineupPlayer.lineup_id == lineup.id
                )
            )
            for p in existing_players.scalars().all():
                await session.delete(p)
            await session.flush()

        # Group by line_index for slot assignment
        by_line: dict[int, list[dict]] = defaultdict(list)
        for p in all_starters:
            by_line[p["line_index"]].append(p)

        for line_idx, line_players in by_line.items():
            for slot_idx, p in enumerate(line_players):
                session.add(WC2026ExpectedLineupPlayer(
                    lineup_id=lineup.id,
                    player_name=p["player_name"],
                    position=p["position"],
                    line_index=line_idx,
                    slot_index=slot_idx,
                    is_starter=True,
                    role="starter",
                    expected_minutes=default_minutes_for_role("starter"),
                ))

        statuses[db_nation] = "seeded"

    await session.commit()
    logger.info(
        "Rotowire seed complete: seeded=%d, skipped_manual=%d, no_match=%d, invalid=%d, missing_gk=%d",
        sum(1 for s in statuses.values() if s == "seeded"),
        sum(1 for s in statuses.values() if s == "skipped_manual"),
        sum(1 for s in statuses.values() if s == "no_match"),
        sum(1 for s in statuses.values() if s == "invalid_formation"),
        sum(1 for s in statuses.values() if s == "missing_gk"),
    )
    return statuses
