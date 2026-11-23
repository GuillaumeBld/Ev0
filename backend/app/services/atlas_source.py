"""Source unique Atlas : fusion des stats saison, effectif d'un club, ecussons.

Partagee par la partie Joueurs (api/players.py) et le calculateur
(pricing/team_xg.py) pour que les deux lisent exactement la meme source.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy import ColumnElement, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.data.countries import country_iso
from app.models.bzzoiro import BzzPlayer


def _safe_div(num: Any, den: Any) -> float | None:
    if den is None or den == 0:
        return None
    if num is None:
        return None
    return num / den


def club_squad_filter(team_api_id: int) -> ColumnElement[bool]:
    """Effectif d'un club : Transfermarkt seule autorite, jamais le pret."""
    return BzzPlayer.current_team_api_id == team_api_id


@dataclass
class TeamBadge:
    name_fr: str | None
    crest_url: str | None
    api_football_id: int | None
    country_iso: str | None


async def team_badges(
    db: AsyncSession, bzz_team_ids: list[int | None]
) -> dict[int, TeamBadge]:
    """Ecussons, nom francais et pays d'un lot de clubs en un seul SELECT.

    Un club absent de canonical_teams est absent du resultat ; un champ non
    renseigne reste None : jamais de valeur inventee.
    """
    from app.models.canonical_teams import CanonicalTeam

    ids = {i for i in bzz_team_ids if i is not None}
    if not ids:
        return {}
    rows = (await db.execute(
        select(
            CanonicalTeam.bzz_team_id,
            CanonicalTeam.name_fr,
            CanonicalTeam.crest_url,
            CanonicalTeam.api_football_id,
            CanonicalTeam.country,
        ).where(CanonicalTeam.bzz_team_id.in_(ids))
    )).all()
    return {
        bid: TeamBadge(name_fr, crest, af_id, country_iso(country))
        for bid, name_fr, crest, af_id, country in rows
    }


# Champs rendus par merge_season_stats. Sert a fabriquer une ligne vide
# pour un joueur sans statistiques dans la saison demandee : en
# comparaison, il reste affiche avec des tirets.
_CHAMPS_VIDES: tuple[str, ...] = (
    "goals",
    "goal_assist",
    "xg_per_90",
    "xa_per_90",
    "avg_rating",
    "shots_on_target_per_90",
    "form_xg_5",
    "matches_played",
    "minutes_played",
    "season",
    "total_shots",
    "shots_on_target",
    "key_pass",
    "expected_goals",
    "expected_assists",
    "yellow_card",
    "red_card",
    "saves",
    "shots_per_90",
    "key_pass_per_90",
    "accurate_cross_per_90",
    "recoveries_per_90",
    "tackles_per_90",
    "interceptions_per_90",
    "shot_accuracy",
    "xg_per_shot",
    "finishing_delta",
    "xa_delta",
    "pass_completion",
    "long_ball_accuracy",
    "cross_accuracy",
    "duel_win_rate",
    "aerial_win_rate",
    "tackle_success_rate",
    "avg_minutes_per_match",
    "starts_pct",
    "form_rating_5",
    "form_goals_5",
    "form_assists_5",
    "rating_trend",
)


def merge_season_stats(rows: list[Any], season: str) -> dict[str, Any] | None:
    """Merge per-league BzzPlayerSeasonStat rows into a single combined season dict.

    Totals are summed across all leagues. Per-90 and efficiency ratios are
    recalculated from the aggregated totals so they remain mathematically
    consistent. Rating is weighted by matches played. Form stats come from
    the league with the most matches (best proxy for current form).
    """
    if not rows:
        return None

    def _sum(attr: str) -> int | float | None:
        vals = [getattr(r, attr) for r in rows if getattr(r, attr) is not None]
        return sum(vals) if vals else None

    # Integer totals
    matches_played = _sum("matches_played")
    minutes_played = _sum("minutes_played")
    starts = _sum("starts")
    goals = _sum("goals")
    goal_assist = _sum("goal_assist")
    expected_goals = _sum("expected_goals")
    expected_assists = _sum("expected_assists")
    total_shots = _sum("total_shots")
    shots_on_target = _sum("shots_on_target")
    key_pass = _sum("key_pass")
    total_cross = _sum("total_cross")
    accurate_cross = _sum("accurate_cross")
    total_pass = _sum("total_pass")
    accurate_pass = _sum("accurate_pass")
    total_long_balls = _sum("total_long_balls")
    accurate_long_balls = _sum("accurate_long_balls")
    duel_won = _sum("duel_won")
    duel_lost = _sum("duel_lost")
    aerial_won = _sum("aerial_won")
    aerial_lost = _sum("aerial_lost")
    total_tackle = _sum("total_tackle")
    won_tackle = _sum("won_tackle")
    interception = _sum("interception")
    ball_recovery = _sum("ball_recovery")
    yellow_card = _sum("yellow_card")
    red_card = _sum("red_card")
    saves = _sum("saves")

    # Per-90 — recalculated from aggregated totals
    mins_per_90 = minutes_played / 90 if minutes_played else None
    xg_per_90 = _safe_div(expected_goals, mins_per_90)
    xa_per_90 = _safe_div(expected_assists, mins_per_90)
    shots_per_90 = _safe_div(total_shots, mins_per_90)
    shots_on_target_per_90 = _safe_div(shots_on_target, mins_per_90)
    key_pass_per_90 = _safe_div(key_pass, mins_per_90)
    accurate_cross_per_90 = _safe_div(accurate_cross, mins_per_90)
    recoveries_per_90 = _safe_div(ball_recovery, mins_per_90)
    tackles_per_90 = _safe_div(total_tackle, mins_per_90)
    interceptions_per_90 = _safe_div(interception, mins_per_90)

    # Efficiency ratios — recalculated from aggregated totals
    duel_sum = ((duel_won or 0) + (duel_lost or 0)) if (duel_won is not None or duel_lost is not None) else None
    aerial_sum = ((aerial_won or 0) + (aerial_lost or 0)) if (aerial_won is not None or aerial_lost is not None) else None

    shot_accuracy = _safe_div(shots_on_target, total_shots)
    xg_per_shot = _safe_div(expected_goals, total_shots)
    finishing_delta = (goals - expected_goals) if goals is not None and expected_goals is not None else None
    xa_delta = (goal_assist - expected_assists) if goal_assist is not None and expected_assists is not None else None
    pass_completion = _safe_div(accurate_pass, total_pass)
    long_ball_accuracy = _safe_div(accurate_long_balls, total_long_balls)
    cross_accuracy = _safe_div(accurate_cross, total_cross)
    duel_win_rate = _safe_div(duel_won, duel_sum)
    aerial_win_rate = _safe_div(aerial_won, aerial_sum)
    tackle_success_rate = _safe_div(won_tackle, total_tackle)

    # Profile
    avg_minutes_per_match = _safe_div(minutes_played, matches_played)
    starts_pct = _safe_div(starts, matches_played)

    # avg_rating: weighted average by matches played per league row
    rating_pairs = [
        (r.avg_rating, r.matches_played or 0)
        for r in rows
        if r.avg_rating is not None and r.matches_played
    ]
    if rating_pairs:
        total_weighted = sum(rating * mp for rating, mp in rating_pairs)
        total_mp_w = sum(mp for _, mp in rating_pairs)
        avg_rating: float | None = total_weighted / total_mp_w if total_mp_w else None
    else:
        avg_rating = None

    # Form: use the row with the most matches as the best proxy for current form
    dominant_row = max(rows, key=lambda r: r.matches_played or 0)
    form_xg_5 = dominant_row.form_xg_5
    form_rating_5 = dominant_row.form_rating_5
    form_goals_5 = dominant_row.form_goals_5
    form_assists_5 = dominant_row.form_assists_5
    rating_trend = dominant_row.rating_trend

    # league_api_id: single value when player has stats in only one competition
    league_api_id: int | None = rows[0].league_api_id if len(rows) == 1 else None

    return {
        "season": season,
        "league_api_id": league_api_id,
        "matches_played": matches_played,
        "minutes_played": minutes_played,
        "starts": starts,
        "goals": goals,
        "goal_assist": goal_assist,
        "expected_goals": expected_goals,
        "expected_assists": expected_assists,
        "total_shots": total_shots,
        "shots_on_target": shots_on_target,
        "key_pass": key_pass,
        "total_cross": total_cross,
        "accurate_cross": accurate_cross,
        "total_pass": total_pass,
        "accurate_pass": accurate_pass,
        "total_long_balls": total_long_balls,
        "accurate_long_balls": accurate_long_balls,
        "duel_won": duel_won,
        "duel_lost": duel_lost,
        "aerial_won": aerial_won,
        "aerial_lost": aerial_lost,
        "total_tackle": total_tackle,
        "won_tackle": won_tackle,
        "interception": interception,
        "ball_recovery": ball_recovery,
        "yellow_card": yellow_card,
        "red_card": red_card,
        "saves": saves,
        "xg_per_90": xg_per_90,
        "xa_per_90": xa_per_90,
        "shots_per_90": shots_per_90,
        "shots_on_target_per_90": shots_on_target_per_90,
        "key_pass_per_90": key_pass_per_90,
        "accurate_cross_per_90": accurate_cross_per_90,
        "recoveries_per_90": recoveries_per_90,
        "tackles_per_90": tackles_per_90,
        "interceptions_per_90": interceptions_per_90,
        "shot_accuracy": shot_accuracy,
        "xg_per_shot": xg_per_shot,
        "finishing_delta": finishing_delta,
        "xa_delta": xa_delta,
        "pass_completion": pass_completion,
        "long_ball_accuracy": long_ball_accuracy,
        "cross_accuracy": cross_accuracy,
        "duel_win_rate": duel_win_rate,
        "aerial_win_rate": aerial_win_rate,
        "tackle_success_rate": tackle_success_rate,
        "avg_rating": avg_rating,
        "avg_minutes_per_match": avg_minutes_per_match,
        "starts_pct": starts_pct,
        "form_xg_5": form_xg_5,
        "form_rating_5": form_rating_5,
        "form_goals_5": form_goals_5,
        "form_assists_5": form_assists_5,
        "rating_trend": rating_trend,
    }
