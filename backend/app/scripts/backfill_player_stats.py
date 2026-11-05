"""Rattrapage historique des statistiques joueur, par match.

Reutilise sync_player_stats_for_event : aucune logique d'ingestion n'est
dupliquee ici. Ce script ne fait qu'enumerer les matchs a traiter.

Volume mesure le 21/08/2026 sur les six competitions du perimetre :
2 042 matchs pour la saison 2024-2025, soit environ 10 200 appels pour les
cinq saisons.

L'enumeration passe par league + date_from + date_to. Le parametre season=
de l'API est inoperant : ?season=2024-2025 rend 408 110 evenements remontant
a 1930.

Les identifiants d'equipe viennent de home_team_obj.id / away_team_obj.id --
/api/events/ n'expose pas de home_team_api_id. Ces identifiants relevent du
meme espace que canonical_teams.bzz_team_id (le PSG y vaut 114 des deux
cotes).

Reprenable : les matchs deja **complets** sont ignores, une execution
interrompue redemarre donc sans retraiter ce qui est fait.

Le critere est la completude, pas la presence. L'ancienne ingestion par
joueur ramenait toute la carriere du joueur interroge, ce qui a seme des
lignes eparses sur 122 795 matchs -- dont beaucoup n'en portent qu'une
poignee. Mesure du 21/08/2026 sur la Ligue 1 2024-2025 : 196 matchs complets
sur 310, 113 partiels, 1 vide. Un critere de simple presence aurait saute
les 310 en annoncant un succes.

Perimetre, ligne de commande -- ce script tape l'API Bzzoiro externe
directement, il n'est PAS gate par bzz_events local. Un lancement nu doit
donc rester petit par defaut :

    python -m app.scripts.backfill_player_stats
        -> perimetre big-5 + C1 (TARGET_LEAGUE_INTERNAL_ID_LIST, 6
           competitions), le defaut historique de ce script.

    python -m app.scripts.backfill_player_stats --all-leagues
        -> perimetre registre complet (PLAYER_STATS_LEAGUE_IDS, ~55
           competitions) : ~275 paires (ligue, saison) au lieu de ~30.
           Opt-in explicite, jamais le defaut d'un lancement nu.

    python -m app.scripts.backfill_player_stats --leagues 1,3,10
        -> liste arbitraire d'identifiants internes de ligue.

    --seasons 2024-2025,2025-2026
        -> remplace BACKFILL_SEASONS.

La fonction backfill() reste appelable programmatiquement avec une liste de
ligues explicite (l'orchestrateur de la Tache 6 lui passe deliberement
PLAYER_STATS_LEAGUE_IDS) ; c'est seulement son defaut, et celui de ce
_main(), qui ne fait plus le fan-out registre implicite.
"""
from __future__ import annotations

import argparse
import asyncio
import logging
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ingestion.bzzoiro.constants import (
    BACKFILL_SEASONS,
    TARGET_LEAGUE_INTERNAL_ID_LIST,
)
from app.ingestion.bzzoiro.league_registry import PLAYER_STATS_LEAGUE_IDS
from app.ingestion.bzzoiro.sync_player_stats import sync_player_stats_for_event

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger(__name__)


def season_window(season: str) -> tuple[str, str]:
    """Rend (date_from, date_to) pour une saison 'AAAA-AAAA'."""
    debut, fin = season.split("-")
    return f"{debut}-07-01", f"{fin}-06-30"


# Une feuille de match complete compte les deux effectifs, titulaires et
# remplaces : environ 40 lignes (44 observees, 40 sur Le Havre-PSG). Le seuil
# est volontairement bas pour tolerer les rencontres a effectif reduit sans
# jamais considerer complet un match a moitie rempli.
LIGNES_MATCH_COMPLET = 30


async def _events_complets(
    session: AsyncSession, seuil: int = LIGNES_MATCH_COMPLET
) -> set[int]:
    """Identifiants des matchs portant deja une feuille complete.

    Le critere est la completude et non la presence : voir le docstring du
    module. Un match a 3 lignes doit etre retraite, pas saute.
    """
    from app.models.bzzoiro import BzzPlayerMatchStat

    result = await session.execute(
        select(BzzPlayerMatchStat.event_api_id)
        .group_by(BzzPlayerMatchStat.event_api_id)
        .having(func.count() >= seuil)
    )
    return set(result.scalars().all())


async def backfill(
    session: AsyncSession,
    client: Any,
    seasons: list[str] | None = None,
    leagues: list[int] | None = None,
) -> tuple[int, int]:
    """Rattrape les statistiques manquantes.

    `leagues` par defaut vaut TARGET_LEAGUE_INTERNAL_ID_LIST (big-5 + C1, 6
    competitions) -- le perimetre historique de ce script. Ce script tape
    l'API Bzzoiro externe directement (il n'est pas gate par bzz_events
    local) : le fan-out vers tout le registre (PLAYER_STATS_LEAGUE_IDS, ~55
    competitions, ~275 paires ligue/saison contre ~30) doit rester un choix
    explicite de l'appelant, jamais un defaut implicite. Les appelants qui
    veulent ce perimetre complet (ex. l'orchestrateur de la Tache 6) passent
    `leagues=PLAYER_STATS_LEAGUE_IDS` deliberement.

    Retourne (matchs traites, matchs ignores).
    """
    seasons = seasons or BACKFILL_SEASONS
    leagues = leagues or TARGET_LEAGUE_INTERNAL_ID_LIST

    complets = await _events_complets(session)
    logger.info("%d matchs deja complets, ils seront ignores", len(complets))

    traites = ignores = 0

    for season in seasons:
        date_from, date_to = season_window(season)
        for league in leagues:
            events = await client.get_all(
                "/api/events/",
                {"league": league, "date_from": date_from, "date_to": date_to},
            )
            logger.info(
                "Saison %s / competition %s : %d matchs", season, league, len(events)
            )

            for event in events:
                event_api_id = event.get("id")
                if event_api_id is None or event.get("status") != "finished":
                    continue
                if event_api_id in complets:
                    ignores += 1
                    continue

                domicile = event.get("home_team_obj") or {}
                exterieur = event.get("away_team_obj") or {}

                try:
                    lignes = await sync_player_stats_for_event(
                        session, client, event_api_id,
                        domicile.get("id"),
                        exterieur.get("id"),
                    )
                    if lignes >= LIGNES_MATCH_COMPLET:
                        complets.add(event_api_id)
                    traites += 1
                except Exception as exc:
                    logger.warning("Echec match %s : %s", event_api_id, exc)

            logger.info("  Cumul : %d matchs traites, %d ignores", traites, ignores)

    logger.info("Rattrapage termine : %d traites, %d ignores", traites, ignores)
    return traites, ignores


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Rattrapage historique des stats joueur. Ce script tape l'API "
            "Bzzoiro externe directement (pas de garde-fou bzz_events "
            "local) : par defaut le perimetre reste big-5 + C1 (6 "
            "competitions). Le registre complet (~55 competitions, ~275 "
            "paires ligue/saison) est strictement opt-in via --all-leagues."
        )
    )
    scope = parser.add_mutually_exclusive_group()
    scope.add_argument(
        "--all-leagues", "--registry",
        dest="all_leagues", action="store_true",
        help=(
            "Perimetre registre complet (PLAYER_STATS_LEAGUE_IDS, ~55 "
            "competitions) au lieu du defaut big-5 + C1."
        ),
    )
    scope.add_argument(
        "--leagues", dest="leagues", default=None,
        help="Identifiants internes de ligue, separes par des virgules (ex: 1,3,10).",
    )
    parser.add_argument(
        "--seasons", dest="seasons", default=None,
        help=(
            "Saisons 'AAAA-AAAA' separees par des virgules. "
            "Par defaut BACKFILL_SEASONS (les cinq dernieres)."
        ),
    )
    return parser.parse_args(argv)


def _select_leagues(args: argparse.Namespace) -> list[int]:
    """Resout le perimetre de ligues choisi en ligne de commande.

    Defaut = big-5 + C1 : un lancement nu ne doit jamais declencher le
    fan-out vers les ~55 competitions du registre. Ce fan-out est
    strictement opt-in via --all-leagues/--registry.
    """
    if args.leagues:
        return [int(x) for x in args.leagues.split(",") if x.strip()]
    if args.all_leagues:
        return list(PLAYER_STATS_LEAGUE_IDS)
    return list(TARGET_LEAGUE_INTERNAL_ID_LIST)


def _select_seasons(args: argparse.Namespace) -> list[str]:
    if args.seasons:
        return [s.strip() for s in args.seasons.split(",") if s.strip()]
    return list(BACKFILL_SEASONS)


async def _main(argv: list[str] | None = None) -> None:
    from app.config import settings
    from app.db import async_session
    from app.ingestion.bzzoiro.client import BzzoiroClient

    args = _parse_args(argv)
    leagues = _select_leagues(args)
    seasons = _select_seasons(args)
    logger.info(
        "Perimetre retenu : %d ligue(s) [%s], %d saison(s)",
        len(leagues), ", ".join(str(l) for l in leagues), len(seasons),
    )

    async with async_session() as session, BzzoiroClient(
        settings.bzzoiro_api_key
    ) as client:
        await backfill(session, client, seasons=seasons, leagues=leagues)


if __name__ == "__main__":
    asyncio.run(_main())
