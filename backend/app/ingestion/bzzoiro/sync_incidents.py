"""Sync match events (goals, assists) from Bzzoiro incidents into match_events.

Replaces the ESPN-based job_sync_match_events. Works for all leagues covered by
Bzzoiro, including WC2026 (league_id=27).

Flow:
  1. Find finished Fixtures with external_id="bzz_{api_id}" that have no match_events yet.
  2. Call /api/v2/events/{api_id}/incidents/ for each.
  3. Parse goals/assists/own_goals and store into match_events.
  4. Store a sentinel row for 0-0 matches so the fixture isn't reprocessed.
"""
from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.ingestion.bzzoiro.client import BzzoiroClient
from app.models.fixtures import Fixture
from app.models.match_events import MatchEvent

logger = logging.getLogger(__name__)

# Sentinel player_name used to mark a fixture as processed when it had 0 goals.
_SENTINEL = "__processed__"
_SENTINEL_TYPE = "match_processed"

# Sentinel stocké quand Bzzoiro renvoie un 404 permanent (événement supprimé
# upstream) : la fixture sort de la sélection au lieu d'être re-fetchée en
# boucle. auto_settle et le settle autopilot traitent ce type en VOID.
SENTINEL_UNAVAILABLE = "__incidents_unavailable__"
SENTINEL_UNAVAILABLE_TYPE = "events_unavailable"  # <= 20 chars (varchar(20))

# Delai avant de tenir un 404 pour definitif.
#
# Un 404 sur cet endpoint a DEUX sens que la reponse ne distingue pas :
# "evenement supprime chez Bzzoiro" (definitif) et "pas encore publie"
# (transitoire). La synchro tourne peu apres la fin des matchs, donc le second
# cas est le plus courant -- et il etait traite comme le premier.
#
# Degats mesures le 06/11/2026 : 1 030 matchs marques definitivement
# indisponibles, plus un seul but ingere depuis aout, et un reglement qui
# annule 8 514 paris sur 9 903 faute de verite terrain. Sur 20 matchs marques
# tires au hasard, 20 avaient leurs incidents disponibles a la verification --
# la sentinelle etait fausse dans tous les cas testes.
#
# Sous ce delai, un 404 ne marque donc plus rien : la fixture est simplement
# reprise au passage suivant, la selection reprenant d'elle-meme toute fixture
# sans sentinelle. Au-dela, le 404 est tenu pour definitif.
DELAI_AVANT_404_DEFINITIF = timedelta(hours=48)


# Correspondance goal_type Bzzoiro -> event_type stocke.
#
# `own_goal` est stocke mais ne doit JAMAIS faire gagner un pari buteur : un
# joueur qui marque contre son camp n'a pas marque. auto_settle le compte en
# revanche dans la couverture, puisqu'il figure au score du match.
_GOAL_TYPES = {
    "regular": "goal",
    "penalty": "penalty_goal",
    "ownGoal": "own_goal",
    # Formes rencontrees dans l'ancien format, gardees par securite.
    "own_goal": "own_goal",
    "penalty_goal": "penalty_goal",
}


def _nom(valeur: Any) -> str:
    """Nom d'un joueur, que Bzzoiro l'envoie en texte ou en objet.

    Depuis juin 2026 c'est une chaine ("M. Gutierrez") ; avant c'etait un
    objet ({"name": ...}). Les deux formes sont acceptees pour qu'un retour en
    arriere cote Bzzoiro ne casse rien.
    """
    if isinstance(valeur, str):
        return valeur.strip()
    if isinstance(valeur, dict):
        return (valeur.get("name") or valeur.get("shortName") or "").strip()
    return ""


def _parse_incidents(raw: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Extrait buts, passes decisives et changements d'une fiche d'incidents.

    Rend des dicts au format MatchEvent : player_name, player_id, event_type,
    minute, related_player_name (le sortant, pour un changement).

    FORMAT. Bzzoiro a change de format en juin 2026 et le parseur ne lisait
    plus rien -- silencieusement, sans erreur, zero but ingere de juin a
    novembre. Consequence : 86 % des paris annules faute de verite terrain.

        avant  {"incidentType": "goal", "player": {"name": "X"}, "assist1": {...}}
        depuis {"type": "goal", "player": "X", "assist": "Y", "goal_type": "regular"}

    Les deux formes sont lues : la cle de type est cherchee sous `type` puis
    `incidentType`, et `_nom` accepte texte comme objet. Un retour en arriere
    cote Bzzoiro ne casserait donc rien.
    """
    rows: list[dict[str, Any]] = []

    for inc in raw:
        inc_type = inc.get("type") or inc.get("incidentType") or ""
        minute = inc.get("minute") if inc.get("minute") is not None else inc.get("time")

        # ── Changements ───────────────────────────────────────────
        # Decisifs pour le reglement : un joueur qui n'est jamais entre rend
        # le pari nul, il ne le perd pas.
        if inc_type in ("substitution", "sub", "substitutionIn"):
            entrant = _nom(inc.get("player_in") or inc.get("playerIn") or inc.get("player"))
            sortant = _nom(inc.get("player_out") or inc.get("playerOut") or inc.get("relatedPlayer"))
            if entrant and sortant:
                rows.append({
                    "player_name": entrant,
                    "player_id": inc.get("player_in_id"),
                    "event_type": "substitution",
                    "minute": minute,
                    "related_player_name": sortant,
                })
            else:
                logger.warning(
                    "Changement incomplet ignore (entrant=%r sortant=%r)", entrant, sortant
                )
            continue

        if inc_type not in ("goal", "addedGoal"):
            continue

        buteur = _nom(inc.get("player"))
        if not buteur:
            logger.warning("But sans buteur identifiable ignore (minute %s)", minute)
            continue

        # `goal_type` depuis juin 2026 ; `isOwnGoal` dans l'ancien format.
        goal_type = inc.get("goal_type")
        if goal_type:
            event_type = _GOAL_TYPES.get(goal_type)
            if event_type is None:
                # Un type inconnu est compte comme un but normal plutot que
                # jete : il figure au score, donc l'ignorer rendrait la
                # couverture incomplete et annulerait tous les paris du match.
                logger.warning(
                    "goal_type inconnu %r (minute %s) — compte comme but normal",
                    goal_type, minute,
                )
                event_type = "goal"
        else:
            event_type = "own_goal" if inc.get("isOwnGoal", False) else "goal"

        rows.append({
            "player_name": buteur,
            "player_id": inc.get("player_id"),
            "event_type": event_type,
            "minute": minute,
            "related_player_name": None,
        })

        # ── Passe decisive ────────────────────────────────────────
        # Pas de passeur sur un csc. Bzzoiro envoie `assist` en texte depuis
        # juin 2026, `assist1` en objet avant.
        if event_type != "own_goal":
            passeur = _nom(inc.get("assist") or inc.get("assist1"))
            if passeur:
                rows.append({
                    "player_name": passeur,
                    "player_id": inc.get("assist_id"),
                    "event_type": "assist",
                    "minute": minute,
                    "related_player_name": None,
                })

    return rows


async def _store_events(
    session: AsyncSession,
    fixture_id: int,
    events: list[dict[str, Any]],
) -> int:
    """Upsert events into match_events. Returns number of rows inserted/updated."""
    count = 0
    for ev in events:
        stmt = (
            pg_insert(MatchEvent)
            .values(
                fixture_id=fixture_id,
                player_name=ev["player_name"],
                # Bzzoiro donne l'identifiant du joueur sur l'incident : un
                # rattachement plus fiable qu'un nom, qui echoue sur les
                # formes abregees ("A. Mac Allister") et sur les homonymes.
                player_id=ev.get("player_id"),
                event_type=ev["event_type"],
                minute=ev.get("minute"),
                related_player_name=ev.get("related_player_name"),
            )
            .on_conflict_do_nothing(constraint="uq_match_event")
        )
        result = await session.execute(stmt)
        count += result.rowcount
    return count


async def sync_incidents(
    session: AsyncSession,
    client: BzzoiroClient,
    limit: int = 100,
) -> int:
    """Fetch and store incidents for finished fixtures that have no events yet.

    Args:
        limit: Max fixtures to process per run (prevents runaway on large backlogs).

    Returns:
        Number of fixtures successfully processed.
    """
    # Fixtures sans events OU avec couverture incomplète (nb events "but" <>
    # score) : le re-fetch complète via l'upsert on_conflict_do_nothing.
    # Les fixtures marquées incidents_unavailable (404 permanent) sont exclues.
    from sqlalchemy import text as sa_text

    ids_res = await session.execute(
        sa_text("""
            SELECT f.id FROM fixtures f
            WHERE f.status = 'finished'
              AND f.external_id LIKE 'bzz\\_%'
              AND NOT EXISTS (
                  SELECT 1 FROM match_events e2
                  WHERE e2.fixture_id = f.id
                    AND e2.event_type = :sentinel
              )
              AND (
                  NOT EXISTS (SELECT 1 FROM match_events e WHERE e.fixture_id = f.id)
                  OR (
                      f.home_score IS NOT NULL AND f.away_score IS NOT NULL
                      AND (SELECT count(*) FROM match_events e
                           WHERE e.fixture_id = f.id
                             AND e.event_type IN ('goal', 'own_goal', 'penalty_goal'))
                          <> f.home_score + f.away_score
                  )
              )
            ORDER BY f.kickoff_utc DESC
            LIMIT :lim
        """),
        {"lim": limit, "sentinel": SENTINEL_UNAVAILABLE_TYPE},
    )
    fixture_ids = [r[0] for r in ids_res.all()]

    if not fixture_ids:
        logger.info("sync_incidents: no finished fixtures missing or incomplete events")
        return 0

    result = await session.execute(select(Fixture).where(Fixture.id.in_(fixture_ids)))
    _by_id = {f.id: f for f in result.scalars().all()}
    fixtures = [_by_id[i] for i in fixture_ids if i in _by_id]  # garde l'ordre SQL

    logger.info("sync_incidents: %d fixtures to process", len(fixtures))
    processed = 0

    for fixture in fixtures:
        # external_id is "bzz_<api_id>"
        try:
            bzz_api_id = int(fixture.external_id.removeprefix("bzz_"))
        except (ValueError, AttributeError):
            logger.warning("sync_incidents: cannot parse external_id=%s", fixture.external_id)
            continue

        try:
            data = await client.get_page(f"/api/v2/events/{bzz_api_id}/incidents/")
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404:
                # Un 404 sur un match termine il y a peu veut dire "pas encore
                # publie", pas "disparu" : on ne marque rien et la fixture est
                # reprise au passage suivant. Voir DELAI_AVANT_404_DEFINITIF.
                age = (
                    datetime.now(UTC) - fixture.kickoff_utc
                    if fixture.kickoff_utc is not None else None
                )
                if age is not None and age < DELAI_AVANT_404_DEFINITIF:
                    logger.info(
                        "sync_incidents: fixture %d (bzz_id=%d) → 404 mais match "
                        "termine il y a %.1f h, nouvelle tentative au prochain passage",
                        fixture.id, bzz_api_id, age.total_seconds() / 3600,
                    )
                    continue

                # Au-dela du delai, ou sans date de coup d'envoi exploitable :
                # 404 tenu pour definitif. Sentinel pour sortir la fixture de
                # la selection. minute=0 obligatoire : avec NULL la contrainte
                # unique ne s'applique pas (NULLs distincts) et le sentinel se
                # duplique.
                await _store_events(session, fixture.id, [{
                    "player_name": SENTINEL_UNAVAILABLE,
                    "event_type": SENTINEL_UNAVAILABLE_TYPE,
                    "minute": 0,
                }])
                await session.commit()
                logger.warning(
                    "sync_incidents: fixture %d (bzz_id=%d) → 404 persistant "
                    "(%s), sentinel 'events_unavailable' stocké — plus de retry",
                    fixture.id, bzz_api_id,
                    f"{age.total_seconds() / 3600:.0f} h apres le match"
                    if age is not None else "sans date de coup d'envoi",
                )
                processed += 1
            else:
                logger.warning(
                    "sync_incidents: failed to fetch incidents for fixture %d (bzz_id=%d): %s",
                    fixture.id, bzz_api_id, exc,
                )
            continue
        except Exception as exc:
            logger.warning(
                "sync_incidents: failed to fetch incidents for fixture %d (bzz_id=%d): %s",
                fixture.id, bzz_api_id, exc,
            )
            continue

        raw_incidents = data if isinstance(data, list) else data.get("incidents", [])
        events = _parse_incidents(raw_incidents)

        if events:
            stored = await _store_events(session, fixture.id, events)
            logger.info(
                "sync_incidents: %s vs %s → %d events stored",
                fixture.home_team, fixture.away_team, stored,
            )
        elif (
            fixture.home_score is not None
            and fixture.away_score is not None
            and fixture.home_score + fixture.away_score == 0
        ):
            # Vrai 0-0 : sentinel pour ne pas re-fetch. minute=0 obligatoire
            # (NULL casse la contrainte unique → duplication infinie).
            await _store_events(session, fixture.id, [{
                "player_name": _SENTINEL,
                "event_type": _SENTINEL_TYPE,
                "minute": 0,
            }])
            logger.debug(
                "sync_incidents: %s vs %s → 0-0, sentinel stored",
                fixture.home_team, fixture.away_team,
            )
        else:
            # Incidents vides alors que le score n'est pas 0-0 (pas encore
            # publiés côté Bzzoiro) : on NE marque PAS la fixture comme
            # traitée — retry au prochain run, sentinel terminal après 48h.
            logger.info(
                "sync_incidents: %s vs %s → incidents vides mais score %s-%s, retry plus tard",
                fixture.home_team, fixture.away_team,
                fixture.home_score, fixture.away_score,
            )

        # Anti-boucle : si après re-fetch la couverture reste incomplète et que
        # le match est fini depuis plus de 48h, les données upstream sont
        # définitivement déficientes → sentinel terminal (les paris concernés
        # passeront VOID au settlement au lieu d'être re-fetchés à l'infini).
        if fixture.home_score is not None and fixture.away_score is not None:
            goal_count_res = await session.execute(
                sa_text(
                    "SELECT count(*) FROM match_events WHERE fixture_id = :fid "
                    "AND event_type IN ('goal', 'own_goal', 'penalty_goal')"
                ),
                {"fid": fixture.id},
            )
            goal_count = goal_count_res.scalar_one()
            still_incomplete = goal_count != fixture.home_score + fixture.away_score
            # Meme delai que pour le 404 : un seul endroit a regler.
            old_enough = (
                fixture.kickoff_utc is not None
                and fixture.kickoff_utc < datetime.now(UTC) - DELAI_AVANT_404_DEFINITIF
            )
            if still_incomplete and old_enough:
                await _store_events(session, fixture.id, [{
                    "player_name": SENTINEL_UNAVAILABLE,
                    "event_type": SENTINEL_UNAVAILABLE_TYPE,
                    "minute": 0,
                }])
                logger.warning(
                    "sync_incidents: fixture %d (%s vs %s) reste incomplète après re-fetch "
                    "(%d events buts pour %d-%d) — sentinel terminal stocké",
                    fixture.id, fixture.home_team, fixture.away_team,
                    goal_count, fixture.home_score, fixture.away_score,
                )

        processed += 1
        await session.commit()

    logger.info("sync_incidents: processed %d/%d fixtures", processed, len(fixtures))
    return processed
