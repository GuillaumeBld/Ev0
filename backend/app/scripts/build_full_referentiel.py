"""Orchestrateur one-shot — mise en service du referentiel Joueurs sur les
~57 competitions (Tache 6, point C de l'expansion ~57-competitions).

Enchaine, pour la saison courante, dans cet ordre fixe :

  1. `rebuild()`            — segmentation stricte des championnats
     domestiques (Tache 2).
  2. `attach_european()`    — rattachement des clubs qualif-only UEFA
     (Tache 2).
  3. `sync_events()`        — evenements Bzzoiro, tout le perimetre
     `PLAYER_STATS_LEAGUE_IDS` (Tache 1/3).
  4. `backfill_player_stats.backfill()` — statistiques joueur par match,
     meme perimetre, saison courante uniquement.
  5. `aggregate_all_leagues()` — agregation saison (Tache 3).
  6. `check_coverage()`     — garde-fou de completude (Tache 5), dont le
     rapport est imprime : engages vs `expected_count`, clubs incomplets,
     joueurs sans stats.
  7. `seed_crests()`        — reapplique les ecussons ancres dans
     `app/data/crests.json` (par `bzz_team_id`, jamais par nom) : une
     reconstruction complete du referentiel ne doit jamais laisser le
     moindre club sans l'ecusson deja resolu en production.

C'est l'entree de la mise en service (Tache 7) — le point d'appel unique qui
remplace l'enchainement manuel des scripts individuels.

`--include-historical` ajoute, AVANT la boucle normale, le rattrapage one-shot
des tournois historiques (Euro 2024 = 66, Copa America 2024 = 67) via
`backfill_player_stats.backfill(leagues=[66, 67])` — jamais recurrent (voir
`HISTORICAL_SELECTION_LEAGUE_IDS` dans `league_registry.py` et le job
`job_sync_referentiel_expansion` de `worker.py`, qui les exclut
deliberement de son propre perimetre quotidien).

`--dry-run` n'ecrit rien : affiche le perimetre resolu et s'arrete avant la
premiere ecriture.
"""
from __future__ import annotations

import argparse
import asyncio
import logging
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.ingestion.bzzoiro.aggregate import aggregate_all_leagues
from app.ingestion.bzzoiro.coverage_check import CoverageReport, check_coverage
from app.ingestion.bzzoiro.league_registry import (
    HISTORICAL_SELECTION_LEAGUE_IDS,
    LEAGUE_REGISTRY,
    PLAYER_STATS_LEAGUE_IDS,
)
from app.ingestion.bzzoiro.sync_events import sync_events
from app.scripts import backfill_player_stats
from app.scripts.rebuild_team_registry import attach_european, rebuild
from app.scripts.seed_crests import seed_crests
from app.services.season_service import current_season

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger(__name__)


async def run(
    session: AsyncSession,
    client: Any,
    *,
    include_historical: bool = False,
    dry_run: bool = False,
) -> list[CoverageReport]:
    """Enchaine rebuild -> attach_european -> events -> stats -> aggregate ->
    coverage -> seed_crests sur `PLAYER_STATS_LEAGUE_IDS`, pour la saison
    courante.

    L'ordre est fixe et teste (`tests/test_expansion_schedule.py`), mais
    n'est PAS une chaine de lectures locales bout en bout : `rebuild()` et
    `attach_european()` interrogent l'API Bzzoiro directement (via
    `enumerer_engages()`), jamais la table `bzz_events` — c'est pourquoi ils
    peuvent tourner en tete, avant toute ecriture de `sync_events()`.
    `sync_events()` alimente ensuite `bzz_events`, que `check_coverage()`
    relit (`events_synced`, matchs `finished`). `backfill_player_stats.
    backfill()` interroge lui aussi l'API Bzzoiro directement (pas
    `bzz_events`) mais peuple les match-stats locales qu'`aggregate_all_
    leagues()` consomme ensuite. `check_coverage()` ferme la chaine pour
    voir le resultat final de ces deux dernieres etapes locales.

    `seed_crests()` ferme la chaine : il reapplique les ecussons ancres dans
    `app/data/crests.json` par `bzz_team_id`, pour qu'une reconstruction
    complete du referentiel ne laisse jamais un club sans son ecusson deja
    resolu en production (ni, a fortiori, n'en melange un entre deux clubs).

    `include_historical=True` insere, avant ces etapes, le rattrapage
    one-shot des tournois historiques (66, 67) — jamais recurrent.

    `dry_run=True` n'appelle aucune des etapes ecrivantes : affiche juste le
    perimetre resolu.
    """
    league_ids = list(PLAYER_STATS_LEAGUE_IDS)

    if dry_run:
        logger.info(
            "dry-run : %d ligues en perimetre (historique %s)",
            len(league_ids),
            "inclus" if include_historical else "exclu",
        )
        return []

    if include_historical:
        historiques = sorted(HISTORICAL_SELECTION_LEAGUE_IDS)
        logger.info("Rattrapage historique one-shot : ligues %s", historiques)
        await backfill_player_stats.backfill(session, client, leagues=historiques)

    season = await current_season(session)
    logger.info("Saison courante : %s", season)

    logger.info("1/7 rebuild()")
    await rebuild(session, client)

    logger.info("2/7 attach_european()")
    await attach_european(session, client)

    logger.info("3/7 sync_events() — %d ligues", len(league_ids))
    await sync_events(session, client, full_season=True, league_internal_ids=league_ids)

    logger.info("4/7 backfill_player_stats.backfill() — saison %s", season)
    await backfill_player_stats.backfill(
        session, client, seasons=[season], leagues=league_ids
    )

    logger.info("5/7 aggregate_all_leagues()")
    await aggregate_all_leagues(session, season=season, league_ids=league_ids)

    logger.info("6/7 check_coverage()")
    reports = await check_coverage(session, season=season, league_ids=league_ids)

    logger.info("7/7 seed_crests()")
    rapport_crests = await seed_crests(session)
    logger.info(
        "Ecussons reappliques : %d appliques, %d bzz_team_id absents en base",
        rapport_crests["appliques"], len(rapport_crests["bzz_ids_absents_en_base"]),
    )

    _print_report(reports)
    return reports


def _print_report(reports: list[CoverageReport]) -> None:
    """Imprime un rapport par ligue : engages vs expected_count, clubs
    incomplets, joueurs sans stats."""
    print()
    print(f"{'Ligue':>6}  {'Nom':<28}  {'Statut':<8}  {'Clubs':>6}  {'Attendu':>8}  {'Incomplets':>10}  {'Sans stats':>10}")
    for report in reports:
        meta = LEAGUE_REGISTRY.get(report.league_api_id)
        nom = meta.name if meta is not None else "?"
        attendu = meta.expected_count if meta is not None else None
        print(
            f"{report.league_api_id:>6}  {nom:<28}  {report.status:<8}  "
            f"{report.clubs_total:>6}  {attendu if attendu is not None else '-':>8}  "
            f"{report.clubs_incomplets:>10}  {report.joueurs_sans_stats:>10}"
        )
    print()


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Orchestrateur one-shot du referentiel Joueurs (~57 "
            "competitions) : rebuild -> attach_european -> events -> "
            "player-stats -> aggregate -> coverage, puis rapport par ligue."
        )
    )
    parser.add_argument(
        "--include-historical",
        dest="include_historical",
        action="store_true",
        help=(
            "Ajoute, avant l'enchainement normal, le rattrapage one-shot "
            "des tournois historiques Euro 2024 (66) et Copa America 2024 "
            "(67). N'est jamais planifie dans le worker."
        ),
    )
    parser.add_argument(
        "--dry-run",
        dest="dry_run",
        action="store_true",
        help="N'ecrit rien : affiche le perimetre resolu et s'arrete.",
    )
    return parser.parse_args(argv)


async def _main(argv: list[str] | None = None) -> None:
    from app.config import settings
    from app.db import async_session
    from app.ingestion.bzzoiro.client import BzzoiroClient

    args = _parse_args(argv)
    async with async_session() as session, BzzoiroClient(
        settings.bzzoiro_api_key
    ) as client:
        await run(
            session,
            client,
            include_historical=args.include_historical,
            dry_run=args.dry_run,
        )


if __name__ == "__main__":
    asyncio.run(_main())
