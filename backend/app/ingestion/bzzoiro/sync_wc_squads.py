"""Sync WC2026 squad call-ups from Bzzoiro into wc2026_squad_players.

Replaces the Wikipedia-based seed_squads.py script.

Source: GET /api/v2/worldcup/squads/
        GET /api/v2/worldcup/squads/{team_id}/

Bzzoiro returns the official FIFA-confirmed squads with position, shirt number,
club, and team metadata. Called once before the tournament and re-run if squad
updates occur (injuries, late call-ups).
"""
from __future__ import annotations

import logging
from typing import Any

import httpx
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.ingestion.bzzoiro.client import BzzoiroClient
from app.models.wc2026 import WC2026SquadPlayer

logger = logging.getLogger(__name__)

# Bzzoiro position codes → our canonical codes
_POS_MAP: dict[str, str] = {
    "G":   "GK",
    "GK":  "GK",
    "D":   "DEF",
    "DF":  "DEF",
    "DEF": "DEF",
    "M":   "MID",
    "MF":  "MID",
    "MID": "MID",
    "F":   "FWD",
    "FW":  "FWD",
    "FWD": "FWD",
    "A":   "FWD",
    "ATT": "FWD",
}

# WC2026 group assignments — Bzzoiro doesn't provide group letters
_NATION_GROUP: dict[str, str] = {
    "Mexico": "A", "South Africa": "A", "South Korea": "A", "Czech Republic": "A",
    "Canada": "B", "Qatar": "B", "Bosnia and Herzegovina": "B", "Switzerland": "B",
    "Argentina": "C", "Chile": "C", "Honduras": "C", "Albania": "C",
    "Brazil": "D", "Ecuador": "D", "Australia": "D", "Iran": "D",
    "Spain": "E", "Croatia": "E", "Morocco": "E", "Uzbekistan": "E",
    "Portugal": "F", "Turkey": "F", "Senegal": "F", "Curaçao": "F",
    "France": "G", "England": "G", "DRC": "G", "Saudi Arabia": "G",
    "Germany": "H", "Japan": "H", "Côte d'Ivoire": "H", "Bahrain": "H",
    "Netherlands": "I", "Serbia": "I", "Nigeria": "I", "New Zealand": "I",
    "Colombia": "J", "Uruguay": "J", "Panama": "J", "Benin": "J",
    "USA": "K", "Paraguay": "K", "Cameroon": "K", "Slovenia": "K",
    "Belgium": "L", "Italy": "L", "Egypt": "L", "Jordan": "L",
}

# Bzzoiro team name → flag emoji (fallback)
_FLAG_MAP: dict[str, str] = {
    "Mexico": "🇲🇽", "South Africa": "🇿🇦", "South Korea": "🇰🇷",
    "Czech Republic": "🇨🇿", "Canada": "🇨🇦", "Qatar": "🇶🇦",
    "Bosnia and Herzegovina": "🇧🇦", "Switzerland": "🇨🇭",
    "Argentina": "🇦🇷", "Chile": "🇨🇱", "Honduras": "🇭🇳", "Albania": "🇦🇱",
    "Brazil": "🇧🇷", "Ecuador": "🇪🇨", "Australia": "🇦🇺", "Iran": "🇮🇷",
    "Spain": "🇪🇸", "Croatia": "🇭🇷", "Morocco": "🇲🇦", "Uzbekistan": "🇺🇿",
    "Portugal": "🇵🇹", "Turkey": "🇹🇷", "Senegal": "🇸🇳", "Curaçao": "🇨🇼",
    "France": "🇫🇷", "England": "🏴󠁧󠁢󠁥󠁮󠁧󠁿", "DR Congo": "🇨🇩", "Saudi Arabia": "🇸🇦",
    "Germany": "🇩🇪", "Japan": "🇯🇵", "Ivory Coast": "🇨🇮", "Bahrain": "🇧🇭",
    "Netherlands": "🇳🇱", "Serbia": "🇷🇸", "Nigeria": "🇳🇬", "New Zealand": "🇳🇿",
    "Colombia": "🇨🇴", "Uruguay": "🇺🇾", "Panama": "🇵🇦", "Benin": "🇧🇯",
    "USA": "🇺🇸", "Paraguay": "🇵🇾", "Cameroon": "🇨🇲", "Slovenia": "🇸🇮",
    "Belgium": "🇧🇪", "Italy": "🇮🇹", "Egypt": "🇪🇬", "Jordan": "🇯🇴",
}


def _map_position(raw: str | None) -> str:
    if not raw:
        return "MID"
    return _POS_MAP.get(raw.upper().strip(), "MID")


async def sync_wc_squads(session: AsyncSession, client: BzzoiroClient) -> int:
    """Fetch all WC2026 squads from Bzzoiro and upsert into wc2026_squad_players.

    Returns total number of rows upserted.
    """
    squads_data = await client.get_all("/api/v2/worldcup/squads/")

    if not squads_data:
        logger.warning("sync_wc_squads: no data returned from /api/v2/worldcup/squads/")
        return 0

    total = 0
    skipped_teams = 0

    for team_entry in squads_data:
        team = team_entry.get("team") or team_entry
        team_name: str = team.get("name", "")
        team_id: int | None = team.get("id") or team.get("api_id")
        flag: str = team.get("flag") or _FLAG_MAP.get(team_name, "")
        group: str = _NATION_GROUP.get(team_name, "?")

        players: list[dict[str, Any]] = team_entry.get("players") or []

        if not players and team_id:
            # Fetch detailed squad if not embedded. Un 404 (id référencé par la
            # liste mais absent du détail — incohérence upstream Bzzoiro) ou
            # toute erreur réseau ne doit PAS interrompre tout le job : on
            # loggue et on passe à l'équipe suivante.
            try:
                detail = await client.get_page(f"/api/v2/worldcup/squads/{team_id}/")
                players = detail.get("players") or []
            except httpx.HTTPStatusError as exc:
                skipped_teams += 1
                logger.warning(
                    "sync_wc_squads: détail effectif indisponible pour %s (id=%s): HTTP %s — équipe ignorée",
                    team_name or "?", team_id, exc.response.status_code,
                )
                continue
            except Exception as exc:
                skipped_teams += 1
                logger.warning(
                    "sync_wc_squads: échec récupération effectif %s (id=%s): %s — équipe ignorée",
                    team_name or "?", team_id, exc,
                )
                continue

        for player in players:
            player_name: str = player.get("name") or player.get("shortName") or ""
            if not player_name:
                continue

            club_obj = player.get("team") or {}
            club: str | None = club_obj.get("name") if isinstance(club_obj, dict) else None

            values = {
                "nation":        team_name,
                "group_letter":  group,
                "player_name":   player_name,
                "club":          club,
                "position":      _map_position(player.get("position")),
                "shirt_number":  player.get("shirtNumber") or player.get("jersey_number"),
                "flag_emoji":    flag or None,
            }

            stmt = (
                pg_insert(WC2026SquadPlayer)
                .values(**values)
                .on_conflict_do_update(
                    index_elements=["nation", "player_name"],
                    set_={k: v for k, v in values.items() if k not in ("nation", "player_name")},
                )
            )
            await session.execute(stmt)
            total += 1

        if players:
            logger.info("sync_wc_squads: %s → %d players", team_name, len(players))

    await session.commit()
    logger.info(
        "sync_wc_squads: %d total rows upserted across all nations (%d équipes ignorées)",
        total, skipped_teams,
    )
    return total
