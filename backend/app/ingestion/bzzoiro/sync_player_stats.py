"""Ingestion des statistiques joueur par match depuis Bzzoiro.

L'endpoint /api/player-stats/ accepte un filtre ``event=<api_id>`` qui rend
en une seule page l'integralite des joueurs des deux equipes (44 lignes
observees le 21/08/2026), chacune portant l'identite du joueur sous la cle
``player``.

C'est la voie retenue : une requete par match au lieu d'une par joueur.
L'ancienne approche par joueur demandait environ 30 000 requetes pour couvrir
une saison et n'aboutissait jamais, d'ou une couverture bloquee a 16 % des
joueurs.

Correspondance des identifiants : ``player.id`` de la reponse vaut
``bzz_players.api_id``, et non ``internal_id`` ni ``bzz_players.id``.

Les cles ``team`` et ``is_home`` sont absentes de la reponse -- c'est pourquoi
l'ancien code, qui lisait ``row["team"]``, laissait ces deux colonnes a NULL
sur 1 133 341 lignes sur 1 135 494. Le camp se deduit desormais en comparant
``player.team`` a ``event.home_team`` : les deux chaines proviennent de la
meme reponse et se comparent donc sans ambiguite.
"""
from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.ingestion.bzzoiro.constants import TARGET_LEAGUE_INTERNAL_ID_LIST
from app.models.bzzoiro import BzzEvent

# Identifiants internes Bzzoiro — c'est la valeur stockee dans
# bzz_events.league_api_id pour les six competitions du perimetre.
_ALL_LEAGUE_IDS = TARGET_LEAGUE_INTERNAL_ID_LIST  # [1,3,4,5,6,7]

logger = logging.getLogger(__name__)


def compute_derived_metrics(row: dict[str, Any]) -> dict[str, float | None]:
    """Compute derived efficiency metrics from a raw Bzzoiro player-stat row."""

    def safe_div(numerator: Any, denominator: Any) -> float | None:
        if denominator is None or denominator == 0:
            return None
        if numerator is None:
            return None
        return numerator / denominator

    total_shots = row.get("total_shots")
    shots_on_target = row.get("shots_on_target")
    expected_goals = row.get("expected_goals")
    goals = row.get("goals")
    goal_assist = row.get("goal_assist")
    expected_assists = row.get("expected_assists")
    total_pass = row.get("total_pass")
    accurate_pass = row.get("accurate_pass")
    total_long_balls = row.get("total_long_balls")
    accurate_long_balls = row.get("accurate_long_balls")
    total_cross = row.get("total_cross")
    accurate_cross = row.get("accurate_cross")
    duel_won = row.get("duel_won")
    duel_lost = row.get("duel_lost")
    aerial_won = row.get("aerial_won")
    aerial_lost = row.get("aerial_lost")
    total_tackle = row.get("total_tackle")
    won_tackle = row.get("won_tackle")

    duel_sum: int | None = None
    if duel_won is not None and duel_lost is not None:
        duel_sum = duel_won + duel_lost

    aerial_sum: int | None = None
    if aerial_won is not None and aerial_lost is not None:
        aerial_sum = aerial_won + aerial_lost

    finishing_delta: float | None = None
    if goals is not None and expected_goals is not None:
        finishing_delta = goals - expected_goals

    xa_delta: float | None = None
    if goal_assist is not None and expected_assists is not None:
        xa_delta = goal_assist - expected_assists

    return {
        "shot_accuracy": safe_div(shots_on_target, total_shots),
        "xg_per_shot": safe_div(expected_goals, total_shots),
        "finishing_delta": finishing_delta,
        "xa_delta": xa_delta,
        "pass_completion": safe_div(accurate_pass, total_pass),
        "long_ball_accuracy": safe_div(accurate_long_balls, total_long_balls),
        "cross_accuracy": safe_div(accurate_cross, total_cross),
        "duel_win_rate": safe_div(duel_won, duel_sum),
        "aerial_win_rate": safe_div(aerial_won, aerial_sum),
        "tackle_success_rate": safe_div(won_tackle, total_tackle),
    }


def build_stat_values(
    row: dict[str, Any],
    event_api_id: int,
    team_api_id: int | None,
    is_home: bool | None,
) -> dict[str, Any]:
    """Construit la ligne a inserer dans bzz_player_match_stats."""
    player = row.get("player") or {}
    return {
        "player_api_id": player.get("id"),
        "event_api_id": event_api_id,
        "team_api_id": team_api_id,
        "is_home": is_home,
        "minutes_played": row.get("minutes_played"),
        "rating": row.get("rating"),
        "touches": row.get("touches"),
        "goals": row.get("goals"),
        "goal_assist": row.get("goal_assist"),
        "expected_goals": row.get("expected_goals"),
        "expected_assists": row.get("expected_assists"),
        "total_shots": row.get("total_shots"),
        "shots_on_target": row.get("shots_on_target"),
        "total_pass": row.get("total_pass"),
        "accurate_pass": row.get("accurate_pass"),
        "key_pass": row.get("key_pass"),
        "total_long_balls": row.get("total_long_balls"),
        "accurate_long_balls": row.get("accurate_long_balls"),
        "total_cross": row.get("total_cross"),
        "accurate_cross": row.get("accurate_cross"),
        "duel_won": row.get("duel_won"),
        "duel_lost": row.get("duel_lost"),
        "aerial_won": row.get("aerial_won"),
        "aerial_lost": row.get("aerial_lost"),
        "total_tackle": row.get("total_tackle"),
        "won_tackle": row.get("won_tackle"),
        "total_clearance": row.get("total_clearance"),
        "interception": row.get("interception"),
        "ball_recovery": row.get("ball_recovery"),
        "yellow_card": row.get("yellow_card"),
        "red_card": row.get("red_card"),
        "fouls": row.get("fouls"),
        "was_fouled": row.get("was_fouled"),
        "dispossessed": row.get("dispossessed"),
        "possession_lost": row.get("possession_lost"),
        "saves": row.get("saves"),
        "goals_conceded": row.get("goals_conceded"),
        **compute_derived_metrics(row),
    }


async def ensure_player_exists(session: AsyncSession, player: dict[str, Any]) -> int:
    """Cree le joueur s'il est absent de bzz_players. Rend son api_id.

    bzz_player_match_stats.player_api_id est une cle etrangere vers
    bzz_players.api_id : sans cette creation, l'insertion des statistiques
    echoue. C'est ce mecanisme qui comble les joueurs manquants.

    on_conflict_do_nothing garantit qu'un joueur deja connu n'est jamais
    ecrase : bzz_players est alimentee par un sync dedie, plus riche que ce
    que porte la reponse de statistiques.
    """
    from app.models.bzzoiro import BzzPlayer

    api_id = player["id"]
    stmt = (
        pg_insert(BzzPlayer)
        .values(
            api_id=api_id,
            name=player.get("name") or f"Joueur {api_id}",
            short_name=player.get("short_name"),
            position=player.get("position"),
        )
        .on_conflict_do_nothing(index_elements=["api_id"])
    )
    await session.execute(stmt)
    return api_id


async def sync_player_stats_for_event(
    session: AsyncSession,
    client: Any,
    event_api_id: int,
    home_team_api_id: int | None,
    away_team_api_id: int | None,
) -> int:
    """Ingere les statistiques des deux equipes d'un match.

    Retourne le nombre de lignes ecrites.
    """
    from app.models.bzzoiro import BzzPlayerMatchStat

    rows = await client.get_all("/api/player-stats/", {"event": event_api_id})
    if not rows:
        return 0

    count = 0
    for row in rows:
        player = row.get("player") or {}
        if not player.get("id"):
            continue

        event = row.get("event") or {}
        club = player.get("team")

        if club is not None and club == event.get("home_team"):
            is_home, team_api_id = True, home_team_api_id
        elif club is not None and club == event.get("away_team"):
            is_home, team_api_id = False, away_team_api_id
        else:
            # Club ne correspondant a aucun camp : on ne devine pas. Le reste
            # des statistiques reste exploitable.
            is_home, team_api_id = None, None

        await ensure_player_exists(session, player)

        values = build_stat_values(row, event_api_id, team_api_id, is_home)
        stmt = pg_insert(BzzPlayerMatchStat).values(**values).on_conflict_do_update(
            index_elements=["player_api_id", "event_api_id"],
            set_={
                k: v for k, v in values.items()
                if k not in ("player_api_id", "event_api_id")
            },
        )
        await session.execute(stmt)
        count += 1

    if count:
        await session.commit()

    return count


async def _get_events_to_sync(
    session: AsyncSession,
    days_back: int | None,
) -> list[tuple[int, int | None, int | None]]:
    """Rend (event_api_id, home_team_api_id, away_team_api_id) des matchs termines.

    days_back=None couvre toute la base, sans restriction de date.
    """
    conditions = [
        BzzEvent.status == "finished",
        BzzEvent.league_api_id.in_(_ALL_LEAGUE_IDS),
        BzzEvent.api_id.is_not(None),
    ]
    if days_back is not None:
        conditions.append(
            BzzEvent.event_date >= datetime.now(UTC) - timedelta(days=days_back)
        )

    result = await session.execute(
        select(BzzEvent.api_id, BzzEvent.home_team_api_id, BzzEvent.away_team_api_id)
        .where(*conditions)
        .order_by(BzzEvent.event_date.desc())
    )
    return list(result.all())


async def sync_player_stats(
    session: AsyncSession,
    client: Any,
    days_back: int = 14,
    full_season: bool = False,
) -> int:
    """Ingere les statistiques joueur, un appel par match.

    Args:
        days_back: profondeur en jours (ignore si full_season=True).
        full_season: si vrai, couvre tous les matchs termines de la base.
    """
    events = await _get_events_to_sync(session, None if full_season else days_back)
    logger.info(
        "Statistiques joueur : %d matchs a traiter (full_season=%s)",
        len(events), full_season,
    )
    if not events:
        return 0

    total = 0
    erreurs = 0
    for i, (event_api_id, home_id, away_id) in enumerate(events):
        try:
            total += await sync_player_stats_for_event(
                session, client, event_api_id, home_id, away_id
            )
        except Exception as exc:
            erreurs += 1
            logger.warning("Echec statistiques match %s : %s", event_api_id, exc)
        if i % 50 == 49:
            logger.info(
                "  Progression : %d/%d matchs, %d lignes", i + 1, len(events), total
            )

    logger.info(
        "Statistiques joueur : %d lignes sur %d matchs (%d erreurs)",
        total, len(events), erreurs,
    )
    return total
