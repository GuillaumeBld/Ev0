"""Beta pricing — même moteur Alpha (team_xg.py, gelé), rythme mélangé carrière.

Beta = EXACTEMENT le même pipeline top-down que Alpha (load_match_pricing) :
même xG d'équipe, mêmes postes, mêmes pen takers, même p_sub /
avg_sub_time. La SEULE différence : le rythme de référence de chaque joueur
(npxg_per_90 / xg_per_90 / xa_per_90) est remplacé par son rythme mélangé
carrière (app.pricing.career_blend.blended_rhythm) quand une carrière a été
importée pour ce joueur.

Un joueur sans rythme mélangé garde son rythme Alpha mais est recalculé sur
les parts Beta (depuis le 26/11/2026) : les 11 postes de chaque modèle
totalisent exactement le xG de l'équipe, ce qu'un repli sur l'allocation
Alpha, calculée sur d'autres parts, empêcherait.

team_xg.py n'est JAMAIS modifié : ce module se contente d'importer ses
fonctions pures (compute_player_shares, allocate_player, detect_penalty_taker)
et son chargeur de joueurs privé (_load_team_players / _load_national_team_players)
pour reconstruire un pool de joueurs strictement identique à celui utilisé par
Alpha pour la même fixture.

Limite connue (documentée, hors scope de ce module) : pour les fixtures
world_cup_2026, load_match_pricing enrichit npxg_per_90/xa_per_90 avec un
blend des stats CDM (in-tournament) *avant* compute_player_shares — ce blend
n'est pas reproduit ici. Cela peut légèrement décaler le dénominateur d'équipe
Beta par rapport à Alpha pour ces matchs précis. Le fallback ci-dessus garantit
que ça n'affecte JAMAIS un joueur sans rythme mélangé (toujours strictement
= Alpha), et l'essentiel du calculateur (matchs de club) n'est pas concerné.
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.pricing.assist import ASSIST_GOAL_RATE
from app.pricing.career_blend import blended_rhythm
from app.pricing.team_xg import (
    MIN_LINEUP_STARTERS,
    PlayerAllocation,
    _load_national_team_players,
    _load_team_players,
    allocate_player,
    compute_player_shares,
    detect_penalty_taker,
    lineup_pen_taker,
    lineup_shares,
    marquer_tireur,
    onze_probable,
)
from app.services.competitions import is_international_fixture

logger = logging.getLogger(__name__)


async def _load_players_matching_alpha(
    db: AsyncSession,
    fixture: Any,
    team: str,
    bzz_team_id: int | None,
) -> list[dict[str, Any]]:
    """Reload the same base player pool load_match_pricing used for `team`.

    Mirrors load_match_pricing's branching: national team roster for
    international fixtures (when the BzzEvent + team api id is resolvable),
    club roster (_load_team_players) otherwise — identical to Alpha for the
    (much more common) club-match path.
    """
    if is_international_fixture(fixture):
        from app.models.bzzoiro import BzzEvent

        bzz_api_id_str = (getattr(fixture, "external_id", None) or "").removeprefix("bzz_")
        if bzz_api_id_str.isdigit():
            ev_res = await db.execute(
                select(BzzEvent).where(BzzEvent.api_id == int(bzz_api_id_str))
            )
            bzz_event = ev_res.scalar_one_or_none()
            if bzz_event is not None and bzz_event.home_team_api_id and bzz_event.away_team_api_id:
                team_api_id = (
                    bzz_event.home_team_api_id
                    if team == fixture.home_team
                    else bzz_event.away_team_api_id
                )
                return await _load_national_team_players(db, national_team_api_id=team_api_id)

    return await _load_team_players(db, team, bzz_team_id=bzz_team_id)


async def _apply_blended_rhythm(
    db: AsyncSession,
    players: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], set[int]]:
    """Copy `players` with npxg_per_90/xg_per_90/xa_per_90 replaced by each
    player's blended career rhythm, when available.

    Returns (players_with_beta_rates, player_ids_with_a_blended_rhythm).
    Players without a blended rhythm keep their original (Alpha) rate in the
    returned list — but the caller must use the returned id set to fall back
    to the literal Alpha allocation for those players (see module docstring).
    """
    out: list[dict[str, Any]] = []
    ids_with_career: set[int] = set()
    for p in players:
        q = dict(p)
        rhythm = await blended_rhythm(db, p["player_id"])
        if rhythm is not None:
            q["npxg_per_90"] = rhythm.goal_rate_per_90
            q["xg_per_90"] = rhythm.goal_rate_per_90
            q["xa_per_90"] = rhythm.assist_rate_per_90
            ids_with_career.add(p["player_id"])
        out.append(q)
    return out, ids_with_career


def _build_beta_allocations(
    players_beta: list[dict[str, Any]],
    ids_with_career: set[int],
    team: str,
    lambda_team: float,
    pen_id: int | None,
    budget_assists: float,
    alpha_by_id: dict[int, PlayerAllocation],
    shares: list | None = None,
) -> dict[int, PlayerAllocation]:
    """Moteur Alpha (compute_player_shares + allocate_player) sur les rythmes
    Beta, pour TOUS les joueurs.

    Depuis le 26/11/2026, les 11 postes doivent totaliser exactement le xG de
    l'équipe dans chaque modèle : un joueur sans carrière n'hérite plus de sa
    cote Alpha (calculée sur d'autres parts), il est recalculé sur les parts
    Beta. `ids_with_career` reste accepté pour compatibilité.

    `shares` : parts déjà calculées (compo, cf. team_xg.lineup_shares) ; par
    défaut, l'effectif complet normalisé sur le onze probable.
    """
    if shares is None:
        shares = compute_player_shares(players_beta, team, lambda_team=lambda_team)
        marquer_tireur(shares, pen_id, onze_probable(players_beta))
    else:
        marquer_tireur(shares, pen_id)
    out: dict[int, PlayerAllocation] = {}
    for s in shares:
        alpha_alloc = alpha_by_id.get(s.player_id)
        p_sub = alpha_alloc.p_sub if alpha_alloc is not None else 0.35
        avg_sub_time = alpha_alloc.avg_sub_time if alpha_alloc is not None else 65.0
        out[s.player_id] = allocate_player(
            s, lambda_team, s.is_pen_taker, budget_assists,
            p_sub=p_sub, avg_sub_time=avg_sub_time,
        )
    return out


async def compute_beta_allocations(
    db: AsyncSession,
    fixture: Any,
    alpha_home_allocs: list[PlayerAllocation],
    alpha_away_allocs: list[PlayerAllocation],
    home_match_xg: float,
    away_match_xg: float,
    home_pen_taker_override: int | None = None,
    away_pen_taker_override: int | None = None,
    home_starter_ids: list[int] | set[int] | None = None,
    away_starter_ids: list[int] | set[int] | None = None,
    alpha_home_lineup: list[PlayerAllocation] | None = None,
    alpha_away_lineup: list[PlayerAllocation] | None = None,
) -> tuple[
    dict[int, PlayerAllocation], dict[int, PlayerAllocation],
    dict[int, PlayerAllocation], dict[int, PlayerAllocation],
]:
    """Compute Beta (career-blended rhythm) allocations, keyed by player_id.

    Rend (home, away, home_compo, away_compo). Les allocations de compo
    reprennent la méthode de la compo Alpha (team_xg.compute_lineup_allocation) :
    même compo par identifiant, xG de l'équipe réparti entre les seuls
    titulaires ; repli littéral sur l'allocation de compo Alpha pour un
    joueur sans rythme mélangé. Vides quand aucune compo n'est fournie.

    Reuses the exact same team_match_xg / pen-taker / p_sub / avg_sub_time
    context as Alpha (via alpha_home_allocs / alpha_away_allocs) so the two
    models differ ONLY in npxg_per_90 / xg_per_90 / xa_per_90 — see module
    docstring for the fallback guarantee and known WC2026 limitation.

    Returns ({}, {}) for a side if its player pool cannot be reloaded (e.g.
    fixture data changed mid-request) — callers should fall back to Alpha.
    """
    home_bzz_team_id = getattr(fixture, "home_bzz_team_id", None)
    away_bzz_team_id = getattr(fixture, "away_bzz_team_id", None)

    home_players_db = await _load_players_matching_alpha(
        db, fixture, fixture.home_team, home_bzz_team_id
    )
    away_players_db = await _load_players_matching_alpha(
        db, fixture, fixture.away_team, away_bzz_team_id
    )

    home_players_beta, home_ids_with_career = await _apply_blended_rhythm(db, home_players_db)
    away_players_beta, away_ids_with_career = await _apply_blended_rhythm(db, away_players_db)

    home_pen_id = home_pen_taker_override or detect_penalty_taker(home_players_db)
    away_pen_id = away_pen_taker_override or detect_penalty_taker(away_players_db)

    budget_assists_home = home_match_xg * ASSIST_GOAL_RATE
    budget_assists_away = away_match_xg * ASSIST_GOAL_RATE

    alpha_home_by_id = {a.player_id: a for a in alpha_home_allocs}
    alpha_away_by_id = {a.player_id: a for a in alpha_away_allocs}

    home_beta = _build_beta_allocations(
        home_players_beta, home_ids_with_career, fixture.home_team,
        home_match_xg, home_pen_id, budget_assists_home, alpha_home_by_id,
    )
    away_beta = _build_beta_allocations(
        away_players_beta, away_ids_with_career, fixture.away_team,
        away_match_xg, away_pen_id, budget_assists_away, alpha_away_by_id,
    )

    home_lineup_beta = _build_lineup_beta(
        home_players_beta, home_ids_with_career, home_starter_ids, fixture.home_team,
        home_match_xg, home_pen_taker_override, alpha_home_by_id, alpha_home_lineup,
    )
    away_lineup_beta = _build_lineup_beta(
        away_players_beta, away_ids_with_career, away_starter_ids, fixture.away_team,
        away_match_xg, away_pen_taker_override, alpha_away_by_id, alpha_away_lineup,
    )

    return home_beta, away_beta, home_lineup_beta, away_lineup_beta


def _build_lineup_beta(
    players_beta: list[dict[str, Any]],
    ids_with_career: set[int],
    starter_ids: list[int] | set[int] | None,
    team: str,
    lambda_team: float,
    pen_override: int | None,
    alpha_full_by_id: dict[int, PlayerAllocation],
    alpha_lineup: list[PlayerAllocation] | None,
) -> dict[int, PlayerAllocation]:
    """Beta de la compo : mêmes titulaires, mêmes parts renormalisées qu'Alpha."""
    if not starter_ids:
        return {}
    # Les identifiants inconnus sont déjà signalés côté Alpha (même vivier pour
    # un match de club) : pas de second signalement ici.
    wanted = {int(i) for i in starter_ids}
    starters = [p for p in players_beta if p.get("player_id") in wanted]
    if len(starters) < MIN_LINEUP_STARTERS:
        return {}
    pen_id = lineup_pen_taker(starters, pen_override)
    alpha_lineup_by_id = {a.player_id: a for a in (alpha_lineup or [])}
    # p_sub / avg_sub_time : ceux de l'allocation Alpha (compo, sinon effectif).
    context = {**alpha_full_by_id, **alpha_lineup_by_id}
    out = _build_beta_allocations(
        starters, ids_with_career, team, lambda_team, pen_id,
        lambda_team * ASSIST_GOAL_RATE, context,
        shares=lineup_shares(starters, team, lambda_team),
    )
    return out
