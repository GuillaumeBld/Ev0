"""Import des carrieres joueurs (format Transfermarkt, voir transfermarkt_career.py)
dans la table `player_career_seasons`.

Pour chaque joueur `matched=true` du JSON d'entree, on retrouve son
`player_api_id` (bzz_players.api_id) en matchant sur nom normalise
(accents-insensible) ET date de naissance == input_dob (tres discriminant :
deux joueurs partageant exactement la meme date de naissance ET un nom
normalise identique sont, en pratique, la meme personne). Aucune devinette :
- joueur `matched=false` cote Transfermarkt -> skip + log (jamais tente).
- pas de `input_dob` exploitable -> skip + log (jamais de match sans dob).
- aucune ligne bzz_players avec cette dob -> skip + log.
- dob trouvee mais nom normalise different -> skip + log (homonyme de date
  de naissance, refus de deviner).
- plusieurs lignes bzz_players avec meme dob ET meme nom normalise ->
  ambigu, skip + log plutot que de choisir au hasard.

Chaque saison/competition est upsertee (ON CONFLICT sur
uq_player_career_season = (player_api_id, season, competition_code)) ->
idempotent, un re-run met simplement a jour les compteurs.

Usage:
    .venv/bin/python -m app.scripts.import_career --input psg_careers.json
"""
from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import json
import logging
import re
import sys
import unicodedata
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.bzzoiro import BzzPlayer
from app.models.player_career import PlayerCareerSeason

logger = logging.getLogger("import_career")
logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s", stream=sys.stderr)

_WHITESPACE_RE = re.compile(r"\s+")


@dataclass
class ImportStats:
    """Compteurs de fin de run, jamais d'echec silencieux : tout skip est compte."""

    players_total: int = 0
    players_matched: int = 0
    players_skipped_not_matched: int = 0
    players_skipped_unresolved: int = 0
    seasons_upserted: int = 0
    unresolved_players: list[str] = field(default_factory=list)


def _normalize_name(name: str) -> str:
    """Normalise un nom pour comparaison : minuscules, sans accents, espaces
    collapses et trimmes."""
    decomposed = unicodedata.normalize("NFKD", name)
    without_accents = "".join(c for c in decomposed if not unicodedata.combining(c))
    return _WHITESPACE_RE.sub(" ", without_accents).strip().lower()


def _parse_dob(input_dob: str | None) -> dt.date | None:
    if not input_dob:
        return None
    try:
        return dt.date.fromisoformat(input_dob)
    except ValueError:
        return None


async def find_player_api_id(
    session: AsyncSession, name: str, dob: dt.date
) -> int | None:
    """Retrouve un bzz_players.api_id par (nom normalise, date de naissance).

    La date de naissance filtre d'abord (tres discriminant) ; le nom
    normalise departage/valide ensuite. Aucun candidat, candidat(s) dont le
    nom ne correspond pas, ou plusieurs candidats identiques -> None
    (jamais de match devine).
    """
    result = await session.execute(
        select(BzzPlayer.api_id, BzzPlayer.name).where(BzzPlayer.date_of_birth == dob)
    )
    rows = result.all()
    if not rows:
        return None

    target = _normalize_name(name)
    matches = [api_id for api_id, candidate_name in rows if _normalize_name(candidate_name) == target]

    if len(matches) == 1:
        return matches[0]
    return None


def _season_values(player_api_id: int, tm_id: int | None, season: dict[str, Any]) -> dict[str, Any]:
    return {
        "player_api_id": player_api_id,
        "tm_id": tm_id,
        "season": season.get("season"),
        "season_start_year": season.get("season_start_year"),
        "competition_code": season.get("competition_code"),
        "competition": season.get("competition"),
        "appearances": season.get("appearances") or 0,
        "goals": season.get("goals") or 0,
        "assists": season.get("assists") or 0,
        "minutes": season.get("minutes") or 0,
    }


async def _upsert_season(session: AsyncSession, values: dict[str, Any]) -> None:
    stmt = pg_insert(PlayerCareerSeason).values(**values).on_conflict_do_update(
        index_elements=["player_api_id", "season", "competition_code"],
        set_={k: v for k, v in values.items() if k not in ("player_api_id", "season", "competition_code")},
    )
    await session.execute(stmt)


async def import_career_data(session: AsyncSession, players: list[dict[str, Any]]) -> ImportStats:
    """Importe une liste de joueurs (format transfermarkt_career.py). Idempotent.

    Ne leve jamais pour un joueur individuel non resolu : chaque skip est
    logge et comptabilise dans les stats retournees.
    """
    stats = ImportStats()

    for player in players:
        stats.players_total += 1
        input_name = player.get("input_name", "?")

        if not player.get("matched"):
            reason = player.get("reason") or "non matche cote Transfermarkt"
            logger.warning("SKIP '%s': %s", input_name, reason)
            stats.players_skipped_not_matched += 1
            stats.unresolved_players.append(input_name)
            continue

        dob = _parse_dob(player.get("input_dob"))
        if dob is None:
            logger.warning("SKIP '%s': input_dob absente ou invalide (%r)", input_name, player.get("input_dob"))
            stats.players_skipped_unresolved += 1
            stats.unresolved_players.append(input_name)
            continue

        player_api_id = await find_player_api_id(session, input_name, dob)
        if player_api_id is None:
            logger.warning(
                "SKIP '%s' (dob=%s): aucun bzz_players resolu de maniere non ambigue", input_name, dob
            )
            stats.players_skipped_unresolved += 1
            stats.unresolved_players.append(input_name)
            continue

        tm_id_raw = player.get("tm_id")
        tm_id = int(tm_id_raw) if tm_id_raw is not None else None

        seasons = player.get("seasons") or []
        for season in seasons:
            values = _season_values(player_api_id, tm_id, season)
            await _upsert_season(session, values)
            stats.seasons_upserted += 1

        await session.commit()
        stats.players_matched += 1
        logger.info("OK '%s' -> player_api_id=%d, %d saison(s) upsertee(s)", input_name, player_api_id, len(seasons))

    logger.info(
        "Import termine: %d/%d joueurs mappes, %d saisons upsertees, %d non-matches TM, %d non-resolus DB",
        stats.players_matched, stats.players_total, stats.seasons_upserted,
        stats.players_skipped_not_matched, stats.players_skipped_unresolved,
    )
    return stats


async def _run(input_path: str) -> ImportStats:
    from app.db import async_session

    with open(input_path, encoding="utf-8") as f:
        players = json.load(f)

    async with async_session() as session:
        return await import_career_data(session, players)


def main() -> int:
    parser = argparse.ArgumentParser(description="Importe les carrieres joueurs (JSON Transfermarkt) en base.")
    parser.add_argument("--input", required=True, help="Fichier JSON produit par transfermarkt_career.py")
    args = parser.parse_args()

    stats = asyncio.run(_run(args.input))

    if stats.players_skipped_not_matched or stats.players_skipped_unresolved:
        logger.warning("Joueurs non importes: %s", stats.unresolved_players)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
