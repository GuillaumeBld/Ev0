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
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ingestion.transfermarkt.player_match import MatchReport, match_players
from app.ingestion.transfermarkt.squad_scraper import TMPlayer, fetch_club_squad
from app.models.bzzoiro import BzzEvent, BzzPlayer, BzzPlayerMatchStat, BzzTeam
from app.models.canonical_teams import CanonicalTeam
from app.models.squad_sync import SquadSyncRun
from app.scripts.transfermarkt_career import TransfermarktClient

logger = logging.getLogger(__name__)

# Sentinelle globale : au-dela de ce ratio de clubs KO (echec scrape/parsing),
# le run entier est annule cote ecritures (cf. docstring module).
MAX_FAILED_CLUB_RATIO = 0.30

# Fenetre du garde-fou "il a joue" (voir `_ayant_joue_pour_le_club`).
JOURS_PRESENCE_TERRAIN = 45

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


async def _ayant_joue_pour_le_club(
    session: AsyncSession, bzz_team_id: int, depuis: date
) -> set[int]:
    """Joueurs ayant dispute des minutes dans un match de `bzz_team_id` depuis
    `depuis`. Ils ne peuvent JAMAIS etre detaches de ce club.

    Pourquoi ce garde-fou. Le matching TM -> `bzz_players` exige un nom complet
    identique ; or les deux sources n'ecrivent pas les noms pareil
    ("Andrew Robertson" contre "Andy Robertson", "Pathe Ismael Ciss" contre
    "Pathe Ciss", les joueurs connus par un surnom : Chupete, Natan, Alisson).
    Ces joueurs ressortent "non apparies" a chaque run, et la regle des deux
    absences consecutives finissait par les detacher de tout club — donc les
    faire disparaitre du site. Le 05/09/2026, un passage sur 124 clubs a ainsi
    detache 222 joueurs qui avaient joue la saison en cours.

    Un joueur sur la pelouse appartient a l'effectif : c'est un fait constate,
    pas une deduction. Il prime donc sur l'absence d'une page Transfermarkt.

    Aucune ambiguite d'identite ici, contrairement a la deduction du club
    depuis les feuilles de match (cf. l'ancien `sync_loan_teams`, supprime) :
    on ne DEDUIT pas le club du joueur, on verifie seulement qu'un joueur DEJA
    rattache a ce club etait sur le terrain lors d'un de ses matchs.
    """
    stmt = (
        select(BzzPlayerMatchStat.player_api_id)
        .join(BzzEvent, BzzEvent.api_id == BzzPlayerMatchStat.event_api_id)
        .where(
            BzzPlayerMatchStat.minutes_played > 0,
            BzzEvent.event_date >= depuis,
            or_(
                BzzEvent.home_team_api_id == bzz_team_id,
                BzzEvent.away_team_api_id == bzz_team_id,
            ),
        )
        .distinct()
    )
    return set((await session.execute(stmt)).scalars().all())


async def _load_team_names(session: AsyncSession, bzz_team_ids: set[int]) -> dict[int, str]:
    """`bzz_team_id -> name` pour les clubs cibles du rattachement (une seule
    requete, jamais par joueur). Sert a poser `current_team_name` de facon
    coherente avec `current_team_api_id` (l'API joueurs affiche/filtre via
    `COALESCE(loan_team_name, current_team_name)`)."""
    if not bzz_team_ids:
        return {}
    stmt = select(BzzTeam.api_id, BzzTeam.name).where(BzzTeam.api_id.in_(bzz_team_ids))
    return {row.api_id: row.name for row in (await session.execute(stmt)).all()}


async def sync_squads(
    session: AsyncSession,
    client: TransfermarktClient,
    clubs: list[CanonicalTeam],
    *,
    mode: str,
    today: date | None = None,
) -> tuple[SquadSyncRun, dict[int, str]]:
    """Reconcilie les effectifs Transfermarkt de `clubs` avec `bzz_players`.

    `clubs` : les `canonical_teams` a traiter (deja filtres en amont par
    l'appelant sur `transfermarkt_club_id` et `bzz_team_id` NON NULL, voir
    `resolve_clubs.py`).

    Renvoie `(run, samples)` : `run` est la ligne `SquadSyncRun` persistee,
    `samples` = `{transfermarkt_club_id: raw_html}` des clubs KO
    (`status != "ok"`) dont la page a ete recuperee (chaine vide exclue,
    ex: club dont le fetch reseau a echoue avant meme d'obtenir une reponse).
    Destine a `failure_surface.surface_failure` (fixture de non-regression
    sur echec) -> collecte en memoire UNIQUEMENT, jamais persiste en base
    (le HTML brut n'a pas sa place dans `squad_sync_runs`).

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
    # {transfermarkt_club_id: raw_html} des clubs KO dont la page a ete
    # recuperee (jamais persiste en base, voir docstring de `sync_squads`).
    samples: dict[int, str] = {}

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
            if squad_result.raw_html:
                samples[club.transfermarkt_club_id] = squad_result.raw_html
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
        return run, samples

    # -- Etape 3 : ecritures (sentinelle franchie). --------------------------

    # Snapshot PREALABLE (avant toute ecriture) de l'effectif de chaque club OK.
    snapshots: dict[int, list[BzzPlayer]] = {
        club.id: await _snapshot_players_at_club(session, club.bzz_team_id)
        for club, _report in ok_results
    }

    run_matched: set[int] = set()
    for _club, report in ok_results:
        run_matched.update(report.matched.values())

    # Nom des clubs cibles du rattachement, charge une seule fois (map
    # bzz_team_id -> name) pour poser `current_team_name` sans requeter par
    # joueur et rester coherent avec `current_team_api_id`.
    team_names = await _load_team_names(
        session, {club.bzz_team_id for club, _report in ok_results}
    )

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
        club_name = team_names.get(club.bzz_team_id)
        for player in players:
            player.current_team_api_id = club.bzz_team_id
            # Cas improbable (aucun bzz_teams pour ce bzz_team_id) : on laisse
            # current_team_name inchange plutot que de le nuller (mieux vaut
            # un ancien nom qu'un trou d'affichage).
            if club_name is not None:
                player.current_team_name = club_name
            player.loan_team_api_id = None
            player.loan_team_name = None
            player.tm_absent_streak = 0
            players_updated += 1

    # Pass "departs" (prudent) : sur le snapshot PREALABLE, jamais sur un
    # joueur matche/reassigne ce run (deja rattache par le pass ci-dessus), et
    # JAMAIS sur un joueur vu sur le terrain avec ce club (garde-fou, voir
    # `_ayant_joue_pour_le_club`).
    depuis = today - timedelta(days=JOURS_PRESENCE_TERRAIN)
    players_detached = 0
    proteges = 0
    for club, _report in ok_results:
        sur_le_terrain = await _ayant_joue_pour_le_club(
            session, club.bzz_team_id, depuis
        )
        for player in snapshots[club.id]:
            if player.api_id in run_matched:
                continue
            if player.api_id in sur_le_terrain:
                # Il a joue pour ce club : Transfermarkt ne l'a pas reconnu
                # (orthographe du nom), pas l'inverse. On le garde et on remet
                # son compteur a zero.
                player.tm_absent_streak = 0
                proteges += 1
                continue
            player.tm_absent_streak += 1
            if player.tm_absent_streak >= 2:
                player.current_team_api_id = None
                player.current_team_name = None
                player.loan_team_api_id = None
                player.loan_team_name = None
                players_detached += 1

    if proteges:
        # Jamais silencieux : un chiffre qui grimpe signale une derive du
        # matching de noms, pas un alea.
        logger.warning(
            "sync_squads: %d joueur(s) conserve(s) malgre leur absence de la "
            "page Transfermarkt de leur club — ils ont joue depuis le %s. "
            "Un chiffre eleve signale un matching de noms defaillant.",
            proteges, depuis,
        )

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
    return run, samples


def _unmatched_entry(tm_player: TMPlayer) -> dict[str, object]:
    return {
        "tm_player_id": tm_player.tm_player_id,
        "name": tm_player.name,
        "age": tm_player.age,
    }
