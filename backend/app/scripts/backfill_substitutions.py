"""Backfill échelonné des substitutions 2025-26 dans match_events.

Pour chaque match terminé de la saison qui a des buts en base mais aucune ligne
substitution, re-fetch les incidents Bzzoiro et stocke les substitutions.
Idempotent : un match déjà pourvu de substitutions est ignoré, de même qu'un
match marqué par la sentinelle subs_unavailable (incidents sans subs ou 404).
Garde 48h : un match n'entre dans la sélection que si son coup d'envoi remonte
à plus de 48h (Fixture.kickoff_utc), pour laisser le temps à Bzzoiro de publier
des subs en retard avant de les considérer comme éligibles au backfill — même
principe que la garde 48h de sync_incidents avant sentinelle terminale.
Échelonné : --limit matchs par run (défaut 300), --sleep secondes entre appels
(défaut 2.0), --league pour cibler une ligue (ordre conseillé = l'ordre
alphabétique des clés, celui effectivement utilisé par --league via
sorted(TARGET_LEAGUE_API_IDS) ; hypothèse de priorisation non validée — cf.
rapport spike).

Usage : cd backend && python -m app.scripts.backfill_substitutions --league premier_league --limit 300

Le lien vers Bzzoiro se fait via Fixture.external_id = "bzz_{api_id}" — il n'existe
PAS de colonne BzzEvent.fixture_id dans le schéma (cf. app/models/bzzoiro.py) ; ce
script reprend donc le même mécanisme de jointure que sync_incidents.sync_incidents.
"""

import argparse
import asyncio
import logging
from datetime import UTC, datetime, timedelta

import httpx
from sqlalchemy import and_, exists, select

from app.config import settings
from app.db import async_session
from app.ingestion.bzzoiro.client import BzzoiroClient
from app.ingestion.bzzoiro.constants import TARGET_LEAGUE_API_IDS
from app.ingestion.bzzoiro.sync_incidents import _parse_incidents, _store_events
from app.models.fixtures import Fixture
from app.models.match_events import MatchEvent

logger = logging.getLogger(__name__)

# Sentinelle stockée quand les incidents d'un match ne contiennent aucune
# substitution (réponse valide sans subs) ou renvoient un 404 permanent : la
# fixture sort de la sélection au lieu d'être re-fetchée à chaque run
# (gaspillage de quota + famine du backlog avec le tri kickoff ASC + LIMIT).
# Même pattern de nommage que les sentinelles de sync_incidents
# (__processed__/match_processed, __incidents_unavailable__/events_unavailable).
# minute=0 obligatoire (comme dans sync_incidents) : avec NULL la contrainte
# unique uq_match_event ne s'applique pas (NULLs distincts) et la sentinelle
# se dupliquerait à chaque run.
SENTINEL_NO_SUBS = "__subs_unavailable__"
SENTINEL_NO_SUBS_TYPE = "subs_unavailable"  # <= 20 chars (varchar(20))


def _fixtures_sans_subs_query(league_key: str | None, limit: int):
    """Fixtures terminées 2025-26 ayant des buts mais aucune substitution.

    Jointure via Fixture.external_id LIKE 'bzz\\_%' (pas de BzzEvent.fixture_id,
    ce champ n'existe pas). Le filtre ligue se fait sur Fixture.league (la clé
    string type "premier_league", cf. sync_fixtures_from_bzz.py), pas sur un
    league_api_id numérique.

    Exclut les fixtures ayant déjà une substitution OU la sentinelle
    subs_unavailable (incidents sans subs / 404 : inutile de re-fetch).

    Garde anti-sentinelle-précoce : n'inclut que les matchs dont le coup
    d'envoi remonte à plus de 48h. Bzzoiro publie parfois les substitutions
    en retard ; sans cette garde, un run lancé juste après un matchday
    pourrait re-fetcher des incidents encore incomplets et, en l'absence de
    subs à cet instant, marquer à tort la fixture subs_unavailable de façon
    définitive.
    """
    has_goal = exists().where(
        and_(MatchEvent.fixture_id == Fixture.id, MatchEvent.event_type == "goal")
    )
    has_sub_or_sentinel = exists().where(
        and_(
            MatchEvent.fixture_id == Fixture.id,
            MatchEvent.event_type.in_(("substitution", SENTINEL_NO_SUBS_TYPE)),
        )
    )
    query = (
        select(Fixture.id, Fixture.external_id)
        .where(
            Fixture.season == "2025-2026",
            Fixture.status == "finished",
            Fixture.external_id.like("bzz\\_%"),
            has_goal,
            ~has_sub_or_sentinel,
            Fixture.kickoff_utc < datetime.now(UTC) - timedelta(hours=48),
        )
        .order_by(Fixture.kickoff_utc)
        .limit(limit)
    )
    if league_key is not None:
        query = query.where(Fixture.league == league_key)
    return query


def _parse_bzz_api_id(external_id: str | None) -> int | None:
    """Extrait l'api_id Bzzoiro depuis Fixture.external_id ("bzz_<api_id>")."""
    try:
        return int((external_id or "").removeprefix("bzz_"))
    except (ValueError, AttributeError):
        return None


async def _store_no_subs_sentinel(fixture_id: int) -> None:
    """Marque la fixture comme définitivement sans substitutions disponibles."""
    async with async_session() as session:
        await _store_events(session, fixture_id, [{
            "player_name": SENTINEL_NO_SUBS,
            "event_type": SENTINEL_NO_SUBS_TYPE,
            "minute": 0,
        }])
        await session.commit()


async def run(league_key: str | None, limit: int, sleep_s: float, dry_run: bool) -> None:
    async with async_session() as session:
        rows = (await session.execute(_fixtures_sans_subs_query(league_key, limit))).all()
    logger.info("%d matchs sans substitutions à backfiller (league=%s)", len(rows), league_key)
    if dry_run:
        print(f"[dry-run] {len(rows)} matchs seraient traités")
        return

    assert settings.bzzoiro_api_key, "BZZOIRO_API_KEY manquante"
    done = errors = 0
    async with BzzoiroClient(settings.bzzoiro_api_key) as client:
        for fixture_id, external_id in rows:
            bzz_api_id = _parse_bzz_api_id(external_id)
            if bzz_api_id is None:
                errors += 1
                logger.error("Fixture %d: external_id invalide %r", fixture_id, external_id)
                continue
            try:
                payload = await client.get_page(f"/api/v2/events/{bzz_api_id}/incidents/")
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code == 404:
                    # 404 permanent (événement supprimé côté Bzzoiro) : sentinelle
                    # pour sortir la fixture de la sélection — pas de retry.
                    try:
                        await _store_no_subs_sentinel(fixture_id)
                        logger.warning(
                            "Fixture %d (bzz %d): 404 permanent, sentinelle "
                            "subs_unavailable stockée — plus de retry",
                            fixture_id, bzz_api_id,
                        )
                        done += 1
                    except Exception as store_exc:
                        errors += 1
                        logger.error(
                            "Fixture %d (bzz %d): échec stockage sentinelle 404: %s",
                            fixture_id, bzz_api_id, store_exc,
                        )
                else:
                    errors += 1
                    logger.error("Fixture %d (bzz %d): HTTP %d: %s",
                                 fixture_id, bzz_api_id, exc.response.status_code, exc)
                await asyncio.sleep(sleep_s)
                continue
            except Exception as exc:
                errors += 1
                logger.error("Fixture %d (bzz %d): %s", fixture_id, bzz_api_id, exc)
                await asyncio.sleep(sleep_s)
                continue

            try:
                # sync_incidents lit "incidents" (ou le payload brut si c'est
                # déjà une liste) — pas de clé "results" pour cet endpoint.
                raw_incidents = payload if isinstance(payload, list) else payload.get("incidents", [])
                subs = [r for r in _parse_incidents(raw_incidents) if r["event_type"] == "substitution"]
                if subs:
                    async with async_session() as session:
                        await _store_events(session, fixture_id, subs)
                        await session.commit()
                else:
                    # Réponse valide mais aucune substitution dans les incidents :
                    # sentinelle pour ne pas re-fetch ce match à chaque run.
                    await _store_no_subs_sentinel(fixture_id)
                    logger.info(
                        "Fixture %d (bzz %d): incidents sans substitution, "
                        "sentinelle subs_unavailable stockée",
                        fixture_id, bzz_api_id,
                    )
                done += 1
            except Exception as exc:
                errors += 1
                logger.error("Fixture %d (bzz %d): %s", fixture_id, bzz_api_id, exc)
            await asyncio.sleep(sleep_s)
    print(f"Backfill terminé: {done} matchs traités, {errors} erreurs")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--league", choices=sorted(TARGET_LEAGUE_API_IDS), default=None)
    parser.add_argument("--limit", type=int, default=300)
    parser.add_argument("--sleep", type=float, default=2.0)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    asyncio.run(run(args.league, args.limit, args.sleep, args.dry_run))
