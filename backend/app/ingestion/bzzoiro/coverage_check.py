"""Controle de completude des donnees joueurs — garde-fou « zero joueur a
moitie » (Tache 5 de l'expansion ~57 competitions).

Pour chaque ligue du perimetre, pour chaque club engage (`CanonicalTeam` avec
`league_api_id`/`season` renseignes pour cette ligue) :

  1. les evenements de la saison sont-ils synchronises pour la ligue ?
     (presence d'au moins un `BzzEvent` pour `league_api_id` dans la fenetre
     de la saison — voir `app.services.season_service`) ;
  2. chaque match **termine** (`status == "finished"`) du club dans cette
     fenetre a-t-il au moins une ligne `BzzPlayerMatchStat` **rattachable a
     ce club** ? Rattachement par club (et non globalement par evenement) :
     signal primaire `BzzPlayerMatchStat.team_api_id == club.bzz_team_id` ;
     repli (car `team_api_id` est parfois NULL a l'ingestion, cf.
     `sync_player_stats.py`) un joueur de l'effectif du club possede une
     ligne pour cet evenement (jointure par `player_api_id`). Un match
     termine sans aucun des deux signaux est un trou **pour ce club** — un
     adversaire bien couvert sur le meme evenement ne couvre pas ce club ;
  3. chaque joueur de l'effectif du club (`BzzPlayer` dont le club effectif —
     pret si present, sinon club courant — est ce club) a-t-il une ligne
     `BzzPlayerSeasonStat` pour `(league_api_id, season)` ? **0 minute est
     admis** si la ligne existe ; **l'absence de ligne est le trou**.
     Un club engage sans aucun `BzzPlayer` dans son effectif est lui-meme un
     trou (`reason="effectif vide"`) — jamais compte "ok" par vacuite.

Un club est compte "incomplet" s'il a au moins un trou de type (2) ou (3).
Rien n'est jamais affiche a moitie sans etre signale : chaque ligue produit
une ligne `DataCoverageRun` (ok/partial/failed/empty) avec le detail
structure des clubs incomplets, et un log WARNING resume chaque ligue non
"ok". C'est l'alerte elle-meme — aucun cablage Telegram ici
(planification/branchement : Tache 6).

Une ligue sans AUCUN club rattache (`clubs_total == 0` — typiquement les
ligues Tier-4 "world" et les selections, dont le rattachement club n'est pas
implemente) n'est PAS comptee "ok" : ce serait un faux "tout couvert". Elle
recoit le statut distinct "empty", honnete sur le fait que rien n'a pu etre
verifie (pas une decision de rattacher ces clubs — differee).

Garde-fou de robustesse : l'enumeration d'une ligue qui leve une exception
(bug, requete invalide, contrainte DB...) ne doit JAMAIS faire remonter
l'exception a l'appelant. Cette ligue est consignee `status="failed"` et le
traitement continue avec les autres ligues du perimetre.
"""
from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ingestion.bzzoiro.league_registry import PLAYER_STATS_LEAGUE_IDS
from app.models.bzzoiro import BzzEvent, BzzPlayer, BzzPlayerMatchStat, BzzPlayerSeasonStat
from app.models.canonical_teams import CanonicalTeam
from app.models.data_coverage import DataCoverageRun
from app.services.season_service import current_season
from app.services.season_service import season_end as season_end_of
from app.services.season_service import season_start as season_start_of

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class CoverageReport:
    """Resultat du controle de completude pour UNE ligue (reflete la ligne
    `DataCoverageRun` persistee — `run_id` est `None` seulement si meme la
    persistance du run "failed" a elle-meme echoue, voir `check_coverage`)."""

    league_api_id: int
    season: str
    clubs_total: int
    clubs_ok: int
    clubs_incomplets: int
    joueurs_sans_stats: int
    status: str  # "ok" | "partial" | "failed" | "empty" (0 club rattache, honnetete seulement)
    detail: dict
    run_id: int | None


async def check_coverage(
    session: AsyncSession,
    *,
    season: str | None = None,
    league_ids: list[int] | None = None,
) -> list[CoverageReport]:
    """Controle de completude club-par-club, ligue-par-ligue.

    `season` : defaut = saison courante (`season_service.current_season`).
    `league_ids` : defaut = `PLAYER_STATS_LEAGUE_IDS` (perimetre ingestion
    stats joueurs, Tache 3).

    Jamais d'exception propagee : une ligue dont l'enumeration leve une
    exception est consignee `status="failed"` et le traitement continue.
    """
    if season is None:
        season = await current_season(session)
    if league_ids is None:
        league_ids = PLAYER_STATS_LEAGUE_IDS

    reports: list[CoverageReport] = []

    for league_api_id in league_ids:
        started_at = datetime.now(UTC)
        try:
            report = await _check_league_coverage(session, league_api_id, season, started_at)
        except Exception as exc:  # noqa: BLE001 — garde-fou volontairement large
            logger.exception(
                "coverage_check: echec de l'enumeration pour la ligue %d (saison %s) — "
                "run marque 'failed', on continue avec les autres ligues.",
                league_api_id, season,
            )
            report = await _persist_failed_run(session, league_api_id, season, started_at, exc)
        reports.append(report)

    return reports


async def _persist_failed_run(
    session: AsyncSession,
    league_api_id: int,
    season: str,
    started_at: datetime,
    exc: Exception,
) -> CoverageReport:
    """Consigne une ligue dont l'enumeration a leve une exception.

    Rollback d'abord (la session peut etre dans un etat "transaction a
    annuler" selon ou l'exception a eu lieu), puis tentative de persistance
    d'une ligne `DataCoverageRun(status="failed")`. Si CETTE persistance
    echoue egalement, elle est elle aussi consignee (log) sans jamais
    propager — un double echec ne doit pas faire sauter les ligues
    suivantes."""
    detail = {"error": f"{exc.__class__.__name__}: {exc}"}
    try:
        await session.rollback()
    except Exception:  # noqa: BLE001
        logger.exception(
            "coverage_check: rollback echoue apres l'echec de la ligue %d.", league_api_id,
        )

    run_id: int | None = None
    try:
        run = DataCoverageRun(
            started_at=started_at,
            finished_at=datetime.now(UTC),
            league_api_id=league_api_id,
            clubs_total=None,
            clubs_ok=None,
            clubs_incomplets=None,
            joueurs_sans_stats=None,
            status="failed",
            detail=detail,
        )
        session.add(run)
        await session.commit()
        run_id = run.id
    except Exception:  # noqa: BLE001
        logger.exception(
            "coverage_check: echec de la persistance du run 'failed' pour la ligue %d — "
            "abandon de la persistance pour cette ligue uniquement.",
            league_api_id,
        )
        try:
            await session.rollback()
        except Exception:  # noqa: BLE001
            logger.exception(
                "coverage_check: rollback echoue apres l'echec de persistance (ligue %d).",
                league_api_id,
            )

    return CoverageReport(
        league_api_id=league_api_id,
        season=season,
        clubs_total=0,
        clubs_ok=0,
        clubs_incomplets=0,
        joueurs_sans_stats=0,
        status="failed",
        detail=detail,
        run_id=run_id,
    )


async def _check_league_coverage(
    session: AsyncSession,
    league_api_id: int,
    season: str,
    started_at: datetime,
) -> CoverageReport:
    clubs = list(
        (
            await session.execute(
                select(CanonicalTeam).where(
                    CanonicalTeam.league_api_id == league_api_id,
                    CanonicalTeam.season == season,
                )
            )
        )
        .scalars()
        .all()
    )

    clubs_total = len(clubs)

    if clubs_total == 0:
        # Rien d'engage pour cette ligue/saison : PAS un "ok" honnete. Les
        # ligues Tier-4 "world" (Bresil, Saudi, Liga MX, J1, K-League,
        # Chinese, Botola, Tunisie, A-League) et les selections ont leurs
        # events/stats synchronises mais aucun club rattache — le
        # rattachement n'existe que pour les domestiques EFFECTIFS_STRICTS.
        # status="ok" ici mentirait ("tout couvert") pour une ligue qui n'est
        # en realite pas segmentee : on persiste un statut distinct
        # "empty" pour que le garde-fou ne pretende jamais couvrir ce qu'il
        # n'a pas les moyens de verifier. Decision produit de rattacher ces
        # clubs differee — ce correctif est honnetete seulement, n'essaie
        # pas de les rattacher.
        reason = "aucun club rattache (ligue non segmentee)"
        detail = {
            "season": season,
            "events_synced": None,
            "incomplete_clubs": [],
            "reason": reason,
        }
        logger.info(
            "coverage_check: ligue %d (saison %s) sans aucun club rattache — "
            "statut 'empty' (%s).",
            league_api_id, season, reason,
        )
        return await _persist_ok_or_partial_run(
            session, league_api_id, started_at,
            clubs_total=0, clubs_ok=0, clubs_incomplets=0, joueurs_sans_stats=0,
            status="empty", detail=detail,
        )

    season_start_date = season_start_of(season)
    season_end_date = season_end_of(season)

    clubs_with_bzz_id = [c for c in clubs if c.bzz_team_id is not None]
    clubs_missing_bzz_id = [c for c in clubs if c.bzz_team_id is None]
    club_bzz_ids = [c.bzz_team_id for c in clubs_with_bzz_id]
    club_bzz_ids_set = set(club_bzz_ids)

    # -- 1. Evenements de la saison synchronises pour la ligue ? ------------
    events_synced = (
        await session.execute(
            select(BzzEvent.api_id)
            .where(
                BzzEvent.league_api_id == league_api_id,
                BzzEvent.event_date >= season_start_date,
                BzzEvent.event_date < season_end_date,
            )
            .limit(1)
        )
    ).first() is not None

    # -- 2a. Matchs termines par club. ---------------------------------------
    finished_events = (
        await session.execute(
            select(BzzEvent.api_id, BzzEvent.home_team_api_id, BzzEvent.away_team_api_id).where(
                BzzEvent.league_api_id == league_api_id,
                BzzEvent.status == "finished",
                BzzEvent.event_date >= season_start_date,
                BzzEvent.event_date < season_end_date,
            )
        )
    ).all()

    finished_events_by_club: dict[int, list[int]] = defaultdict(list)
    all_finished_event_ids: set[int] = set()
    for event_api_id, home_team_api_id, away_team_api_id in finished_events:
        all_finished_event_ids.add(event_api_id)
        if home_team_api_id is not None and home_team_api_id in club_bzz_ids_set:
            finished_events_by_club[home_team_api_id].append(event_api_id)
        if away_team_api_id is not None and away_team_api_id in club_bzz_ids_set:
            finished_events_by_club[away_team_api_id].append(event_api_id)

    # -- 2b. Effectif par club (utilise pour le repli match-stats ET pour
    #        le controle season-stats). ---------------------------------
    roster_by_club: dict[int, list[int]] = defaultdict(list)
    if club_bzz_ids:
        player_rows = (
            await session.execute(
                select(
                    BzzPlayer.api_id,
                    BzzPlayer.current_team_api_id,
                    BzzPlayer.loan_team_api_id,
                ).where(
                    or_(
                        BzzPlayer.current_team_api_id.in_(club_bzz_ids),
                        BzzPlayer.loan_team_api_id.in_(club_bzz_ids),
                    )
                )
            )
        ).all()
        for player_api_id, current_team_api_id, loan_team_api_id in player_rows:
            effective_club = (
                loan_team_api_id if loan_team_api_id is not None else current_team_api_id
            )
            if effective_club in club_bzz_ids_set:
                roster_by_club[effective_club].append(player_api_id)

    # -- 2c. Rattachement des match-stats par club : signal primaire
    #        team_api_id, repli par effectif (player_api_id) quand
    #        team_api_id est NULL a l'ingestion. Un seul aller-retour DB,
    #        recoupe en memoire par club (pas de N+1). -----------------
    events_team_stats: dict[int, set[int]] = defaultdict(set)
    events_player_stats: dict[int, set[int]] = defaultdict(set)
    if all_finished_event_ids:
        stat_rows = (
            await session.execute(
                select(
                    BzzPlayerMatchStat.event_api_id,
                    BzzPlayerMatchStat.team_api_id,
                    BzzPlayerMatchStat.player_api_id,
                ).where(BzzPlayerMatchStat.event_api_id.in_(all_finished_event_ids))
            )
        ).all()
        for event_api_id, team_api_id, player_api_id in stat_rows:
            if team_api_id is not None:
                events_team_stats[event_api_id].add(team_api_id)
            if player_api_id is not None:
                events_player_stats[event_api_id].add(player_api_id)

    players_with_season_stats: set[int] = set(
        (
            await session.execute(
                select(BzzPlayerSeasonStat.player_api_id).where(
                    BzzPlayerSeasonStat.league_api_id == league_api_id,
                    BzzPlayerSeasonStat.season == season,
                )
            )
        )
        .scalars()
        .all()
    )

    # -- Agregation club par club. -------------------------------------------
    clubs_ok = 0
    joueurs_sans_stats = 0
    incomplete_clubs: list[dict[str, object]] = []

    for club in clubs_missing_bzz_id:
        # Aucune correspondance Bzzoiro pour ce club engage : aucune des
        # verifications 2/3 n'est possible -> signale, jamais saute en silence.
        incomplete_clubs.append(
            {
                "canonical_team_id": club.id,
                "bzz_team_id": None,
                "name_fr": club.name_fr,
                "reason": "bzz_team_id manquant — club non rattache a Bzzoiro",
                "missing_match_stats_events": [],
                "players_without_season_stats": [],
            }
        )

    for club in clubs_with_bzz_id:
        bzz_id = club.bzz_team_id
        roster = roster_by_club.get(bzz_id, [])

        if not roster:
            # Club engage mais aucun BzzPlayer ne lui pointe : "a moitie"
            # typique — ne jamais compter "ok" par vacuite (ni (2) ni (3)
            # ne peuvent etre verifies sans effectif).
            incomplete_clubs.append(
                {
                    "canonical_team_id": club.id,
                    "bzz_team_id": bzz_id,
                    "name_fr": club.name_fr,
                    "reason": "effectif vide",
                    "missing_match_stats_events": [],
                    "players_without_season_stats": [],
                }
            )
            continue

        roster_set = set(roster)
        missing_match_events = sorted(
            eid
            for eid in finished_events_by_club.get(bzz_id, [])
            if bzz_id not in events_team_stats.get(eid, set())
            and not (roster_set & events_player_stats.get(eid, set()))
        )
        missing_players = sorted(pid for pid in roster if pid not in players_with_season_stats)

        if not missing_match_events and not missing_players:
            clubs_ok += 1
            continue

        joueurs_sans_stats += len(missing_players)
        incomplete_clubs.append(
            {
                "canonical_team_id": club.id,
                "bzz_team_id": bzz_id,
                "name_fr": club.name_fr,
                "missing_match_stats_events": missing_match_events,
                "players_without_season_stats": missing_players,
            }
        )

    clubs_incomplets = len(incomplete_clubs)
    status = "ok" if (events_synced and clubs_incomplets == 0) else "partial"

    detail = {
        "season": season,
        "events_synced": events_synced,
        "incomplete_clubs": incomplete_clubs,
    }

    if status != "ok":
        logger.warning(
            "coverage_check: ligue %d (saison %s) incomplete — events_synced=%s, "
            "%d/%d club(s) incomplet(s), %d joueur(s) sans ligne de stats saison.",
            league_api_id, season, events_synced, clubs_incomplets, clubs_total, joueurs_sans_stats,
        )

    return await _persist_ok_or_partial_run(
        session, league_api_id, started_at,
        clubs_total=clubs_total, clubs_ok=clubs_ok, clubs_incomplets=clubs_incomplets,
        joueurs_sans_stats=joueurs_sans_stats, status=status, detail=detail,
    )


async def _persist_ok_or_partial_run(
    session: AsyncSession,
    league_api_id: int,
    started_at: datetime,
    *,
    clubs_total: int,
    clubs_ok: int,
    clubs_incomplets: int,
    joueurs_sans_stats: int,
    status: str,
    detail: dict,
) -> CoverageReport:
    run = DataCoverageRun(
        started_at=started_at,
        finished_at=datetime.now(UTC),
        league_api_id=league_api_id,
        clubs_total=clubs_total,
        clubs_ok=clubs_ok,
        clubs_incomplets=clubs_incomplets,
        joueurs_sans_stats=joueurs_sans_stats,
        status=status,
        detail=detail,
    )
    session.add(run)
    await session.commit()

    return CoverageReport(
        league_api_id=league_api_id,
        season=detail.get("season", ""),
        clubs_total=clubs_total,
        clubs_ok=clubs_ok,
        clubs_incomplets=clubs_incomplets,
        joueurs_sans_stats=joueurs_sans_stats,
        status=status,
        detail=detail,
        run_id=run.id,
    )
