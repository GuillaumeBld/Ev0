"""Orchestration de la reconciliation d'effectifs Transfermarkt -> bzz_players.

Assemble les taches precedentes (`squad_scraper.fetch_club_squad`,
`player_match.match_players`) en un run complet, ecrit dans `bzz_players`
(rattachement/detachement) et trace dans `squad_sync_runs`.

Algorithme (voir docstring de `sync_squads` pour le detail) :
  1. Scrape + matche chaque club de `clubs` (un club KO -> aucune ecriture
     pour LUI, mais le run continue pour les autres).
  2. Sentinelle globale : si trop de clubs KO (> 30%), le run entier est
     annule cote effectifs (zero ecriture bzz_players), seule la ligne
     `squad_sync_runs` (status="failed") est persistee.
  3. Sinon, applique les ecritures : rattachement des joueurs matches
     (nouvelle recrue, retour de pret, transfert) puis, prudemment,
     incremente/detache les joueurs absents de l'effectif TM de leur club
     actuel (detachement seulement a partir de 2 runs consecutifs d'absence,
     jamais pour un joueur matche/reassigne ce run).

Structure transactionnelle
---------------------------
Toute la phase 1 (scrape + matching, club par club) est PUREMENT EN LECTURE
cote `bzz_players` : `fetch_club_squad` ne touche pas a la base, et
`match_players` ne fait que des `SELECT`. Aucun objet `BzzPlayer` n'est donc
modifie avant que la decision de sentinelle (etape 2) ne soit prise -> si la
sentinelle declenche un echec, il n'y a tout simplement RIEN a annuler cote
effectifs : on cree seulement la ligne `SquadSyncRun` (status="failed") et on
commit. Aucun rollback explicite n'est necessaire.

Si la sentinelle est franchie (assez de clubs OK), les mutations
`BzzPlayer` (pass "matched" puis pass "departs") sont accumulees sur les
memes instances ORM (identity map de la session : une meme ligne chargee
plusieurs fois au sein de la session reste le meme objet Python, donc les
deux passes restent coherentes entre elles) puis flushees en une seule fois
avec la ligne `SquadSyncRun` via UN SEUL `await session.commit()` final,
partageant ainsi la meme transaction DB. Un echec de flush (contrainte DB,
etc.) fait donc echouer/rollback ensemble effectifs ET ligne de run : jamais
d'etat incoherent ou `bzz_players` serait modifie sans trace du run
correspondant.
"""
from __future__ import annotations

import logging
from datetime import UTC, date, datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ingestion.transfermarkt.player_match import MatchReport, match_players
from app.ingestion.transfermarkt.squad_scraper import TMPlayer, fetch_club_squad
from app.models.bzzoiro import BzzPlayer
from app.models.canonical_teams import CanonicalTeam
from app.models.squad_sync import SquadSyncRun
from app.scripts.transfermarkt_career import TransfermarktClient

logger = logging.getLogger(__name__)

# Sentinelle globale : au-dela de ce ratio de clubs KO (echec scrape/parsing),
# le run entier est annule cote ecritures (cf. docstring module).
MAX_FAILED_CLUB_RATIO = 0.30

# Nombre d'entrees max conservees dans `detail.unmatched_sample` (evite de
# gonfler la ligne JSONB avec des centaines de joueurs non matches).
UNMATCHED_SAMPLE_SIZE = 20


async def _snapshot_players_at_club(session: AsyncSession, bzz_team_id: int) -> list[BzzPlayer]:
    """Effectif actuel (avant ecriture) du club `bzz_team_id`, cote
    `bzz_players` : un joueur "appartient" au club de son pret s'il est
    prete (`loan_team_api_id`), sinon a son club courant."""
    stmt = select(BzzPlayer).where(
        func.coalesce(BzzPlayer.loan_team_api_id, BzzPlayer.current_team_api_id) == bzz_team_id
    )
    return list((await session.execute(stmt)).scalars().all())


async def _load_players_by_api_id(session: AsyncSession, api_ids: set[int]) -> list[BzzPlayer]:
    if not api_ids:
        return []
    stmt = select(BzzPlayer).where(BzzPlayer.api_id.in_(api_ids))
    return list((await session.execute(stmt)).scalars().all())


async def sync_squads(
    session: AsyncSession,
    client: TransfermarktClient,
    clubs: list[CanonicalTeam],
    *,
    mode: str,
    today: date | None = None,
) -> SquadSyncRun:
    """Reconcilie les effectifs Transfermarkt de `clubs` avec `bzz_players`.

    `clubs` : les `canonical_teams` a traiter (deja filtres en amont par
    l'appelant sur `transfermarkt_club_id` et `bzz_team_id` NON NULL, voir
    `resolve_clubs.py`).

    Etape 1 - scrape + matching (lecture seule) :
        pour chaque club, `fetch_club_squad` puis, si `status == "ok"`,
        `match_players` sur son effectif. Un club dont le scrape echoue
        (`status != "ok"`) est compte KO et exclu de toute ecriture, mais ne
        bloque pas le traitement des autres clubs.

    Etape 2 - sentinelle globale :
        si `clubs_failed / clubs_total > MAX_FAILED_CLUB_RATIO`, le run est
        marque `status="failed"` et AUCUNE ecriture `bzz_players` n'a lieu
        (voir docstring module : rien n'a ete mute avant ce point, donc rien
        a annuler). Seule la ligne `squad_sync_runs` est persistee.

    Etape 3 - ecritures (si la sentinelle est franchie) :
        - snapshot PREALABLE (avant toute ecriture) de l'effectif de chaque
          club OK, cote `bzz_players` ;
        - `run_matched` = union de tous les `bzz_api_id` matches, tous
          clubs OK confondus ;
        - pass "matched" : chaque joueur matche a un club OK X est rattache
          a X (`current_team_api_id = X.bzz_team_id`) et son pret est
          efface (`loan_team_api_id/name = NULL`), son compteur d'absence
          remis a zero ;
        - pass "departs" : pour chaque club OK X, chaque joueur de son
          snapshot PREALABLE absent de `run_matched` (donc ni reconduit a X,
          ni reassigne ailleurs ce run) voit son compteur d'absence
          incremente ; a partir de 2 (absences consecutives), il est
          detache (`current_team_api_id/name = NULL`,
          `loan_team_api_id/name = NULL`).
    """
    today = today or date.today()
    started_at = datetime.now(UTC)

    clubs_total = len(clubs)
    ok_results: list[tuple[CanonicalTeam, MatchReport]] = []
    failed_clubs: list[dict[str, object]] = []

    # -- Etape 1 : scrape + matching, lecture seule cote bzz_players. -------
    for club in clubs:
        squad_result = await fetch_club_squad(client, club.transfermarkt_club_id)
        if squad_result.status != "ok":
            logger.warning(
                "Club TM %s (canonical_team id=%s) KO : status=%s.",
                club.transfermarkt_club_id, club.id, squad_result.status,
            )
            failed_clubs.append(
                {
                    "canonical_team_id": club.id,
                    "transfermarkt_club_id": club.transfermarkt_club_id,
                    "status": squad_result.status,
                }
            )
            continue

        report = await match_players(session, squad_result.players, today)
        ok_results.append((club, report))

    clubs_ok = len(ok_results)
    clubs_failed = clubs_total - clubs_ok
    failure_ratio = (clubs_failed / clubs_total) if clubs_total else 0.0

    # -- Etape 2 : sentinelle globale. ---------------------------------------
    if failure_ratio > MAX_FAILED_CLUB_RATIO:
        logger.error(
            "Sentinelle squad sync declenchee : %d/%d clubs KO (%.0f%% > %.0f%%) -> "
            "aucune ecriture bzz_players, run marque failed.",
            clubs_failed, clubs_total, failure_ratio * 100, MAX_FAILED_CLUB_RATIO * 100,
        )
        run = SquadSyncRun(
            started_at=started_at,
            finished_at=datetime.now(UTC),
            mode=mode,
            clubs_total=clubs_total,
            clubs_ok=clubs_ok,
            clubs_failed=clubs_failed,
            players_updated=0,
            players_detached=0,
            status="failed",
            detail={"failed_clubs": failed_clubs, "reason": "sentinel_threshold_exceeded"},
        )
        session.add(run)
        await session.commit()
        return run

    # -- Etape 3 : ecritures (sentinelle franchie). --------------------------

    # Snapshot PREALABLE (avant toute ecriture) de l'effectif de chaque club OK.
    snapshots: dict[int, list[BzzPlayer]] = {
        club.id: await _snapshot_players_at_club(session, club.bzz_team_id)
        for club, _report in ok_results
    }

    run_matched: set[int] = set()
    for _club, report in ok_results:
        run_matched.update(report.matched.values())

    # Pass "matched" : rattachement (recrue, retour de pret, transfert).
    players_updated = 0
    for club, report in ok_results:
        matched_api_ids = set(report.matched.values())
        players = await _load_players_by_api_id(session, matched_api_ids)
        found_ids = {p.api_id for p in players}
        missing = matched_api_ids - found_ids
        if missing:
            # Defense en profondeur : match_players ne renvoie que des
            # bzz_api_id issus d'une requete sur bzz_players, donc ce cas ne
            # devrait jamais se produire (course/suppression concurrente ?).
            logger.warning(
                "Club canonical id=%s : %d bzz_api_id matches introuvables en base (%s), ignores.",
                club.id, len(missing), sorted(missing),
            )
        for player in players:
            player.current_team_api_id = club.bzz_team_id
            player.loan_team_api_id = None
            player.loan_team_name = None
            player.tm_absent_streak = 0
            players_updated += 1

    # Pass "departs" (prudent) : sur le snapshot PREALABLE, jamais sur un
    # joueur matche/reassigne ce run (deja rattache par le pass ci-dessus).
    players_detached = 0
    for club, _report in ok_results:
        for player in snapshots[club.id]:
            if player.api_id in run_matched:
                continue
            player.tm_absent_streak += 1
            if player.tm_absent_streak >= 2:
                player.current_team_api_id = None
                player.current_team_name = None
                player.loan_team_api_id = None
                player.loan_team_name = None
                players_detached += 1

    unmatched_sample: list[dict[str, object]] = []
    for _club, report in ok_results:
        for tm_player in report.unmatched:
            if len(unmatched_sample) >= UNMATCHED_SAMPLE_SIZE:
                break
            unmatched_sample.append(_unmatched_entry(tm_player))

    run = SquadSyncRun(
        started_at=started_at,
        finished_at=datetime.now(UTC),
        mode=mode,
        clubs_total=clubs_total,
        clubs_ok=clubs_ok,
        clubs_failed=clubs_failed,
        players_updated=players_updated,
        players_detached=players_detached,
        status="ok" if clubs_failed == 0 else "partial",
        detail={"failed_clubs": failed_clubs, "unmatched_sample": unmatched_sample},
    )
    session.add(run)
    await session.commit()
    return run


def _unmatched_entry(tm_player: TMPlayer) -> dict[str, object]:
    return {
        "tm_player_id": tm_player.tm_player_id,
        "name": tm_player.name,
        "age": tm_player.age,
    }
