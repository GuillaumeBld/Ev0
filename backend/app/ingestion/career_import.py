"""Import automatique des carrieres joueurs (Transfermarkt).

Pourquoi ce module existe
-------------------------
`app/scripts/transfermarkt_career.py` (recuperation) et
`app/scripts/import_career.py` (ecriture en base) fonctionnent, mais s'utilisent
a la main, chaines par un fichier JSON :

    python app/scripts/transfermarkt_career.py --input joueurs.json --out r.json
    python -m app.scripts.import_career --input r.json

Ils n'ont tourne que les 29 et 30 juillet 2026. Tout joueur arrive depuis est
reste sans carriere : au 10/09/2026, 840 joueurs des cinq grands championnats
et de la C1, dont les transferts de fin de mercato. Or la carriere est le socle
du pricing joueur — sans elle, un joueur est cote sur deux matchs de donnees.

Ce module chaine les deux scripts en memoire, sans fichier intermediaire, et
n'en modifie aucun : il appelle `build_player_report` puis `import_career_data`.

Ce qu'il ajoute
---------------
Une file d'attente bornee et une trace des tentatives. Transfermarkt impose
2 secondes entre chaque requete et un joueur en coute trois a quatre : sans
trace, un joueur que le resolveur ne sait pas identifier serait retente a
chaque passage, indefiniment, pour rien.
"""

from __future__ import annotations

import datetime as dt
import logging
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.career_import import (
    OUTCOME_ERROR,
    OUTCOME_OK,
    OUTCOME_UNMATCHED,
    CareerImportAttempt,
)
from app.models.player_career import PlayerCareerSeason
from app.scripts.import_career import import_career_data
from app.scripts.transfermarkt_career import (
    TransfermarktError,
    build_player_report,
)

logger = logging.getLogger(__name__)

# Championnats dont les joueurs sont cotes. Un joueur hors de ce perimetre ne
# vaut pas une requete Transfermarkt : la MLS et la Copa do Brasil pesent a
# elles seules 2 000 joueurs qu'Ev0 ne price pas.
PRICED_LEAGUES: tuple[str, ...] = (
    "Premier League",
    "La Liga",
    "Serie A",
    "Bundesliga",
    "Ligue 1",
    "Champions League",
)

# Delai avant de retenter, selon l'issue de la derniere tentative.
# Un joueur non identifie par Transfermarkt ne le sera pas davantage demain :
# on ne reessaie qu'apres un mois, le temps qu'une fiche soit creee ou qu'un
# nom soit corrige. Une panne reseau, elle, se retente le lendemain.
RETRY_AFTER: dict[str, dt.timedelta] = {
    OUTCOME_UNMATCHED: dt.timedelta(days=30),
    OUTCOME_ERROR: dt.timedelta(days=1),
}

# Plafond par passage. 762 joueurs a ~8 s chacun = 1 h 40 : on etale plutot que
# de tenir le thread une nuit entiere.
DEFAULT_BATCH_LIMIT = 120


@dataclass
class CareerImportRun:
    """Bilan d'un passage."""

    candidates: int = 0
    attempted: int = 0
    imported: int = 0
    unmatched: int = 0
    errors: int = 0
    seasons_written: int = 0
    unmatched_names: list[str] = field(default_factory=list)

    @property
    def status(self) -> str:
        if self.errors and not self.imported:
            return "failed"
        if self.errors or self.unmatched:
            return "partial"
        return "ok"


def classify_report(report: dict[str, Any]) -> tuple[str, str | None]:
    """(issue, raison) d'un rapport rendu par `build_player_report`.

    Fonction pure : c'est la seule regle de decision du module, elle se teste
    sans base ni reseau.
    """
    if report.get("matched"):
        return OUTCOME_OK, None
    return OUTCOME_UNMATCHED, report.get("reason")


def should_retry(outcome: str, attempted_at: dt.datetime, now: dt.datetime) -> bool:
    """Une tentative passee autorise-t-elle un nouvel essai maintenant ?

    Un import reussi n'est jamais rejoue : la carriere est en base, et la file
    d'attente ecarte de toute facon les joueurs qui en ont une.
    """
    if outcome == OUTCOME_OK:
        return False
    delay = RETRY_AFTER.get(outcome)
    if delay is None:
        return True
    return (now - attempted_at) >= delay


# Joueurs du perimetre cote, sans carriere, avec une date de naissance.
#
# La date de naissance est obligatoire : `import_career.py` matche sur
# (nom normalise, date de naissance) et refuse de trancher sans elle. La
# demander ici evite une requete Transfermarkt qui finirait rejetee.
#
# `minutes_played > 0` ecarte les joueurs listes dans un effectif mais qui
# n'ont pas joue une minute cette saison.
_QUEUE_SQL = text(
    """
    SELECT DISTINCT
        p.api_id            AS player_api_id,
        p.name              AS name,
        p.nationality       AS nationality,
        p.date_of_birth     AS dob,
        p.current_team_name AS club
    FROM bzz_player_season_stats s
    JOIN bzz_leagues  l ON l.api_id = s.league_api_id
    JOIN bzz_players  p ON p.api_id = s.player_api_id
    LEFT JOIN career_import_attempts a ON a.player_api_id = p.api_id
    WHERE (s.season LIKE '2025%' OR s.season LIKE '2026%')
      AND COALESCE(s.minutes_played, 0) > 0
      AND l.name = ANY(:leagues)
      AND p.date_of_birth IS NOT NULL
      AND NOT EXISTS (
          SELECT 1 FROM player_career_seasons c
          WHERE c.player_api_id = p.api_id
      )
      AND (
          a.player_api_id IS NULL
          OR (a.outcome <> 'ok' AND a.attempted_at < :retry_before)
      )
    ORDER BY p.api_id
    LIMIT :limit
    """
)


async def players_needing_career(
    session: AsyncSession,
    limit: int = DEFAULT_BATCH_LIMIT,
    now: dt.datetime | None = None,
) -> list[dict[str, Any]]:
    """File d'attente : les joueurs a traiter au prochain passage.

    Rendus au format attendu par `build_player_report` : name, nationality,
    dob (ISO), club.
    """
    now = now or dt.datetime.now(dt.timezone.utc)
    # Un seul seuil pour les deux issues : on prend le plus permissif (erreur
    # reseau, 1 jour) et `should_retry` affine ensuite joueur par joueur. Cela
    # garde la requete simple sans jamais retenter trop tot.
    retry_before = now - RETRY_AFTER[OUTCOME_ERROR]

    rows = (
        await session.execute(
            _QUEUE_SQL,
            {
                "leagues": list(PRICED_LEAGUES),
                "retry_before": retry_before,
                "limit": limit,
            },
        )
    ).mappings().all()

    out: list[dict[str, Any]] = []
    for r in rows:
        dob = r["dob"]
        out.append({
            "player_api_id": r["player_api_id"],
            "name": r["name"],
            "nationality": r["nationality"] or "",
            "dob": dob.isoformat() if hasattr(dob, "isoformat") else str(dob),
            "club": r["club"] or "",
        })
    return out


async def record_attempt(
    session: AsyncSession,
    player_api_id: int,
    outcome: str,
    reason: str | None = None,
    tm_id: str | None = None,
    now: dt.datetime | None = None,
) -> None:
    """Enregistre l'issue d'une tentative (une ligne par joueur, mise a jour).

    `attempts` s'incremente : un joueur retente dix fois sans succes devient
    reperable dans les donnees sans avoir a lire les logs.
    """
    now = now or dt.datetime.now(dt.timezone.utc)
    stmt = pg_insert(CareerImportAttempt).values(
        player_api_id=player_api_id,
        attempted_at=now,
        outcome=outcome,
        reason=reason,
        tm_id=tm_id,
        attempts=1,
    )
    await session.execute(stmt.on_conflict_do_update(
        constraint="uq_career_import_attempt_player",
        set_={
            "attempted_at": stmt.excluded.attempted_at,
            "outcome": stmt.excluded.outcome,
            "reason": stmt.excluded.reason,
            # Le tm_id resolu ne se perd pas si une tentative ulterieure echoue
            # avant de le retrouver : c'est l'etape la plus couteuse.
            "tm_id": stmt.excluded.tm_id,
            "attempts": CareerImportAttempt.attempts + 1,
        },
    ))


async def run_career_import(
    session: AsyncSession,
    client: Any,
    cache: dict[str, Any] | None = None,
    limit: int = DEFAULT_BATCH_LIMIT,
    now: dt.datetime | None = None,
) -> CareerImportRun:
    """Un passage : file d'attente -> Transfermarkt -> base.

    `client` est un `TransfermarktClient` (synchrone, rate-limite a 2 s par
    requete) : cette coroutine doit tourner dans son propre thread, jamais sur
    la boucle du worker. Voir `_run_career_import_blocking` cote worker.
    """
    now = now or dt.datetime.now(dt.timezone.utc)
    cache = cache if cache is not None else {}

    queue = await players_needing_career(session, limit=limit, now=now)
    run = CareerImportRun(candidates=len(queue))
    if not queue:
        logger.info("Import carriere : aucun joueur a traiter.")
        return run

    logger.info("Import carriere : %d joueurs a traiter.", len(queue))

    # Rapports que Transfermarkt a su resoudre, en attente d'ecriture.
    # L'issue n'est PAS enregistree ici : `import_career_data` refuse encore des
    # joueurs pourtant identifies (date de naissance absente cote bzz_players,
    # homonyme de date de naissance, tm_id non numerique). Les marquer "ok" des
    # maintenant les sortirait de la file pour toujours, sans carriere et sans
    # trace — l'echec silencieux exact qu'on veut interdire.
    resolus: list[tuple[int, dict[str, Any]]] = []

    for player in queue:
        api_id = player["player_api_id"]
        run.attempted += 1
        try:
            report = build_player_report(client, player, cache)
        except TransfermarktError as exc:
            # Panne reseau ou HTTP : ce n'est pas un verdict sur le joueur.
            # On la trace comme telle pour la retenter des le lendemain, et on
            # continue le lot plutot que de le perdre entier.
            run.errors += 1
            logger.warning("Import carriere : echec reseau '%s' — %s", player["name"], exc)
            await record_attempt(session, api_id, OUTCOME_ERROR, reason=str(exc), now=now)
            continue

        outcome, reason = classify_report(report)
        if outcome == OUTCOME_OK:
            resolus.append((api_id, report))
        else:
            run.unmatched += 1
            run.unmatched_names.append(player["name"])
            logger.info("Import carriere : non resolu '%s' — %s", player["name"], reason)
            await record_attempt(session, api_id, outcome, reason=reason, now=now)

    if resolus:
        stats = await import_career_data(session, [r for _, r in resolus])
        run.seasons_written = stats.seasons_upserted

        # Verdict par la base, pas par les compteurs : `import_career_data`
        # n'expose pas toutes ses raisons de refus (un tm_id non numerique est
        # ecarte sans figurer dans `unresolved_players`). Seule la presence
        # d'une ligne de carriere prouve que le joueur est traite.
        ecrits = set((await session.execute(
            select(PlayerCareerSeason.player_api_id)
            .where(PlayerCareerSeason.player_api_id.in_([api_id for api_id, _ in resolus]))
            .distinct()
        )).scalars().all())

        for api_id, report in resolus:
            if api_id in ecrits:
                run.imported += 1
                await record_attempt(
                    session, api_id, OUTCOME_OK, tm_id=report.get("tm_id"), now=now
                )
            else:
                run.unmatched += 1
                run.unmatched_names.append(report.get("input_name") or str(api_id))
                await record_attempt(
                    session, api_id, OUTCOME_UNMATCHED,
                    reason="identifie chez Transfermarkt mais refuse a l'ecriture "
                           "(date de naissance manquante, homonyme, ou tm_id invalide)",
                    tm_id=report.get("tm_id"), now=now,
                )

    await session.commit()
    logger.info(
        "Import carriere : %d importes, %d non resolus, %d erreurs (%d saisons ecrites).",
        run.imported, run.unmatched, run.errors, run.seasons_written,
    )
    return run
