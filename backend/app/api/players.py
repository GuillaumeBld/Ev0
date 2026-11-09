"""Player stats API endpoints — powered by Bzzoiro tables."""

import csv
import io
import re
from collections import defaultdict
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import desc, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.ingestion.bzzoiro.constants import (
    EFFECTIFS_REGLEMENTAIRES,
    INTL_CLUB_COMP_LEAGUE_IDS,
    SELECTION_STATS_LEAGUE_IDS,
    TARGET_LEAGUE_API_ID_LIST,
    TARGET_LEAGUE_INTERNAL_ID_LIST,
)
from app.ingestion.bzzoiro.league_registry import LEAGUE_REGISTRY, TIER_SELECTION
from app.ingestion.competition_identity import tm_code_to_league_api_id
from app.models.bzzoiro import (
    BzzEvent,
    BzzLeague,
    BzzPlayer,
    BzzPlayerMatchStat,
    BzzPlayerSeasonStat,
    BzzTeam,
)
from app.models.player_career import PlayerCareerSeason
from app.models.selection_stats import PlayerSelectionSeasonStat
from app.pricing.career_blend import blended_rhythm
from app.services.season_service import current_season, season_end, season_start

router = APIRouter(prefix="/players", tags=["players"])

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

TARGET_INTERNAL_IDS = [1, 3, 4, 5, 6, 7]  # PL=1, LaLiga=3, SerieA=4, BL=5, L1=6, UCL=7

# Canonical Bzzoiro internal IDs (post-migration) used for all new data.
# Old SofaScore IDs kept for recent_matches query (old events still carry those IDs).
TARGET_LEAGUE_IDS = TARGET_LEAGUE_INTERNAL_ID_LIST  # PL=1, LL=3, SA=4, BL=5, L1=6, UCL=7
_ALL_LEAGUE_IDS = TARGET_LEAGUE_INTERNAL_ID_LIST  # canonical Bzzoiro IDs only: [1,3,4,5,6,7]

# Ligues du journal match par match de la fiche joueur (recent_matches) :
# les 6 championnats/UCL cibles, PLUS les autres competitions internationales
# de clubs (Europa, Conference, Super Coupe, Libertadores/Sudamericana) et les
# selections nationales ayant de vraies stats de match (Coupe du monde, Ligues
# des Nations). Le journal doit couvrir club + coupes + selection, pas
# seulement les 6 championnats domestiques.
RECENT_MATCHES_LEAGUE_IDS: frozenset[int] = (
    frozenset(_ALL_LEAGUE_IDS) | INTL_CLUB_COMP_LEAGUE_IDS | SELECTION_STATS_LEAGUE_IDS
)

CSV_FIELDS = [
    "name",
    "team",
    "position",
    "league",
    "games",
    "minutes",
    "goals",
    "assists",
    "xg",
    "xa",
    "shots",
    "key_passes",
    "xg_per_90",
    "xa_per_90",
]

# Valid sort_by keys → result dict field name
_SORT_FIELD_MAP: dict[str, str] = {
    "name": "name",
    "team": "team_name",
    "matches_played": "matches_played",
    "minutes_played": "minutes_played",
    "goals": "goals",
    "goal_assist": "goal_assist",
    "total_shots": "total_shots",
    "shots_on_target": "shots_on_target",
    "key_pass": "key_pass",
    "expected_goals": "expected_goals",
    "expected_assists": "expected_assists",
    "yellow_card": "yellow_card",
    "red_card": "red_card",
    "saves": "saves",
    "xg_per_90": "xg_per_90",
    "xa_per_90": "xa_per_90",
    "shots_per_90": "shots_per_90",
    "shots_on_target_per_90": "shots_on_target_per_90",
    "key_pass_per_90": "key_pass_per_90",
    "accurate_cross_per_90": "accurate_cross_per_90",
    "recoveries_per_90": "recoveries_per_90",
    "tackles_per_90": "tackles_per_90",
    "interceptions_per_90": "interceptions_per_90",
    "avg_rating": "avg_rating",
    "shot_accuracy": "shot_accuracy",
    "xg_per_shot": "xg_per_shot",
    "finishing_delta": "finishing_delta",
    "xa_delta": "xa_delta",
    "pass_completion": "pass_completion",
    "long_ball_accuracy": "long_ball_accuracy",
    "cross_accuracy": "cross_accuracy",
    "duel_win_rate": "duel_win_rate",
    "aerial_win_rate": "aerial_win_rate",
    "tackle_success_rate": "tackle_success_rate",
    "avg_minutes_per_match": "avg_minutes_per_match",
    "starts_pct": "starts_pct",
    "form_xg_5": "form_xg_5",
    "form_rating_5": "form_rating_5",
    "form_goals_5": "form_goals_5",
    "form_assists_5": "form_assists_5",
    "rating_trend": "rating_trend",
}

_STRING_SORT_FIELDS = {"name", "team_name"}


class PlayerSummary(BaseModel):
    """Player summary for list endpoint."""

    player_api_id: int
    name: str
    short_name: str | None
    position: str | None
    team_name: str | None
    nationality: str | None
    goals: int | None
    goal_assist: int | None
    xg_per_90: float | None
    xa_per_90: float | None
    avg_rating: float | None
    shots_on_target_per_90: float | None
    form_xg_5: float | None
    matches_played: int | None
    minutes_played: int | None
    season: str
    # Saison precedente, affichee sous la valeur courante. None si non demandee
    # ou si le joueur n'a pas joue cette saison-la.
    previous: dict[str, Any] | None = None
    # Identifiant API-Football du club (ecusson media.api-sports.io), resolu
    # via canonical_teams. None si le club n'a pas de club canonique rattache,
    # ou si ce club canonique n'a pas encore d'api_football_id — jamais un
    # ecusson invente.
    api_football_id: int | None = None
    # Ecusson footylogos.com (URL image complete, deja verifiee 200 par
    # app.ingestion.footylogos.resolve_crests) — meme club canonique que
    # api_football_id ci-dessus. None si non resolu, jamais une URL devinee.
    # Priorite cote front : crest_url -> api_football_id -> placeholder.
    crest_url: str | None = None


class RecentMatch(BaseModel):
    """Single match entry in player detail — all available stats."""

    event_api_id: int
    event_date: datetime | None
    # Identifiant de ligue (BzzEvent.league_api_id) : permet au front d'afficher
    # la competition de chaque match du journal (club, coupe, selection). None
    # si l'event n'a pas de ligue resolue (ne devrait pas arriver vu le filtre
    # de la requete qui l'exige).
    league_api_id: int | None = None
    opponent: str | None
    is_home: bool | None
    minutes_played: int | None
    rating: float | None
    touches: int | None
    goals: int | None
    goal_assist: int | None
    expected_goals: float | None
    expected_assists: float | None
    total_shots: int | None
    shots_on_target: int | None
    total_pass: int | None
    accurate_pass: int | None
    key_pass: int | None
    total_long_balls: int | None
    accurate_long_balls: int | None
    total_cross: int | None
    accurate_cross: int | None
    duel_won: int | None
    duel_lost: int | None
    aerial_won: int | None
    aerial_lost: int | None
    total_tackle: int | None
    won_tackle: int | None
    total_clearance: int | None
    interception: int | None
    ball_recovery: int | None
    yellow_card: int | None
    red_card: int | None
    fouls: int | None
    was_fouled: int | None
    dispossessed: int | None
    possession_lost: int | None
    saves: int | None
    goals_conceded: int | None
    shot_accuracy: float | None
    xg_per_shot: float | None
    finishing_delta: float | None
    xa_delta: float | None
    pass_completion: float | None
    long_ball_accuracy: float | None
    cross_accuracy: float | None
    duel_win_rate: float | None
    aerial_win_rate: float | None
    tackle_success_rate: float | None


class SeasonStatsOut(BaseModel):
    """Aggregated season stats across all competitions."""

    season: str
    league_api_id: int | None  # None when stats span multiple leagues
    matches_played: int | None
    minutes_played: int | None
    starts: int | None
    goals: int | None
    goal_assist: int | None
    total_shots: int | None
    shots_on_target: int | None
    key_pass: int | None
    expected_goals: float | None
    expected_assists: float | None
    xg_per_90: float | None
    xa_per_90: float | None
    shots_per_90: float | None
    shots_on_target_per_90: float | None
    key_pass_per_90: float | None
    avg_rating: float | None
    form_xg_5: float | None
    form_rating_5: float | None
    form_goals_5: int | None
    form_assists_5: int | None
    rating_trend: float | None
    shot_accuracy: float | None
    xg_per_shot: float | None
    finishing_delta: float | None
    xa_delta: float | None
    yellow_card: int | None
    red_card: int | None
    saves: int | None
    accurate_cross_per_90: float | None
    recoveries_per_90: float | None
    tackles_per_90: float | None
    interceptions_per_90: float | None
    long_ball_accuracy: float | None
    cross_accuracy: float | None
    pass_completion: float | None
    duel_win_rate: float | None
    aerial_win_rate: float | None
    tackle_success_rate: float | None
    avg_minutes_per_match: float | None
    starts_pct: float | None


class CareerCompetitionOut(BaseModel):
    """One (season, competition) row, reconciled between Bzzoiro
    (bzz_player_season_stats, authority) and Transfermarkt
    (player_career_seasons, complement) — see `_build_career_seasons`.

    `source` tells which side the displayed values came from:
    "bzzoiro" (Bzzoiro had the row, nothing to fill), "merged" (Bzzoiro had
    the row but at least one NULL field was filled from the Transfermarkt
    counterpart), or "transfermarkt" (Bzzoiro had no row at all for this
    (season, competition)). `expected_goals`/`avg_rating` are Bzzoiro-only
    fields: never fabricated when the entry is Transfermarkt-only.
    """

    competition: str | None
    competition_code: str | None
    appearances: int
    goals: int
    assists: int
    minutes: int
    source: str
    is_national: bool = False
    expected_goals: float | None = None
    avg_rating: float | None = None


class CareerSeasonOut(BaseModel):
    """One season of career, aggregated across competitions, with per-competition detail."""

    season: str
    season_start_year: int | None
    appearances: int
    goals: int
    assists: int
    minutes: int
    competitions: list[CareerCompetitionOut]


class BlendedRhythmOut(BaseModel):
    """Beta blended reference rhythm (Bzzoiro xG/xA + career goals/assists)."""

    goal_rate_per_90: float
    assist_rate_per_90: float
    seasons_used: int
    has_career: bool


class PlayerCareerIdentityOut(BaseModel):
    """Minimal player identity for the career endpoint."""

    player_api_id: int
    name: str


class PlayerCareerOut(BaseModel):
    """Response of GET /players/{player_api_id}/career."""

    player: PlayerCareerIdentityOut
    career: list[CareerSeasonOut]
    blended_rhythm: BlendedRhythmOut | None


class BreakdownRow(BaseModel):
    """One (season, competition) row aggregated from bzz_player_match_stats.

    Exists alongside SeasonStatsOut (which reads bzz_player_season_stats)
    because the maquette's Saison/Competition selectors need fields the
    season table doesn't carry: dispossessed, possession_lost, was_fouled,
    total_clearance, aerial_lost.
    """

    season: str
    league_api_id: int
    league_name: str
    kind: str
    matches_played: int | None
    minutes_played: int | None
    starts: int | None
    goals: int | None
    goal_assist: int | None
    expected_goals: float | None
    expected_assists: float | None
    total_shots: int | None
    shots_on_target: int | None
    key_pass: int | None
    total_pass: int | None
    accurate_pass: int | None
    total_cross: int | None
    accurate_cross: int | None
    total_long_balls: int | None
    accurate_long_balls: int | None
    duel_won: int | None
    duel_lost: int | None
    aerial_won: int | None
    aerial_lost: int | None
    total_tackle: int | None
    won_tackle: int | None
    interception: int | None
    ball_recovery: int | None
    total_clearance: int | None
    dispossessed: int | None
    possession_lost: int | None
    was_fouled: int | None
    fouls: int | None
    yellow_card: int | None
    red_card: int | None
    avg_rating: float | None


class PlayerBreakdownOut(BaseModel):
    """Response of GET /players/{player_api_id}/breakdown."""

    rows: list[BreakdownRow]


class MatchOddsRow(BaseModel):
    """Closing Betclic odds (buteur/passeur) for a single event."""

    event_api_id: int
    buteur: float | None
    passeur: float | None


class PlayerMatchOddsOut(BaseModel):
    """Response of GET /players/{player_api_id}/match-odds."""

    odds: list[MatchOddsRow]


class PlayerDetail(BaseModel):
    """Full player detail."""

    player_api_id: int
    name: str
    short_name: str | None
    position: str | None
    date_of_birth: Any | None
    nationality: str | None
    height: int | None
    jersey_number: int | None
    market_value: int | None
    team_name: str | None
    season_stats: SeasonStatsOut | None
    recent_matches: list[RecentMatch]
    # Voir PlayerSummary.api_football_id — meme resolution, meme garantie.
    api_football_id: int | None = None
    # Voir PlayerSummary.crest_url — meme resolution, meme garantie.
    crest_url: str | None = None
    # Stats de selection nationale (toutes competitions de selection
    # confondues), lues depuis player_selection_season_stats. None si le
    # joueur n'a aucune selection — la vue club ne doit jamais en inventer.
    selection_stats: SeasonStatsOut | None = None
    # Nom de la selection nationale (canonical_teams.name_fr), resolu via
    # national_team_api_id. None si pas de selection.
    national_team_name: str | None = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------



def _safe_div(num: Any, den: Any) -> float | None:
    if den is None or den == 0:
        return None
    if num is None:
        return None
    return num / den


def _merge_season_stats(rows: list[Any], season: str) -> dict[str, Any] | None:
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


def _compute_form_from_matches(recent_matches: list[RecentMatch], overall_avg_rating: float | None) -> dict[str, Any]:
    """Derive form stats from the 5 most recent matches (already sorted desc by date)."""
    last5 = recent_matches[:5]

    xg_vals = [m.expected_goals for m in last5 if m.expected_goals is not None]
    rating_vals = [m.rating for m in last5 if m.rating is not None]
    goals_vals = [m.goals for m in last5 if m.goals is not None]
    assist_vals = [m.goal_assist for m in last5 if m.goal_assist is not None]

    form_xg_5: float | None = sum(xg_vals) if xg_vals else None
    form_rating_5: float | None = sum(rating_vals) / len(rating_vals) if rating_vals else None
    form_goals_5: int | None = sum(goals_vals) if goals_vals else None
    form_assists_5: int | None = sum(assist_vals) if assist_vals else None

    rating_trend: float | None = None
    if form_rating_5 is not None and overall_avg_rating is not None:
        rating_trend = form_rating_5 - overall_avg_rating

    return {
        "form_xg_5": form_xg_5,
        "form_rating_5": form_rating_5,
        "form_goals_5": form_goals_5,
        "form_assists_5": form_assists_5,
        "rating_trend": rating_trend,
    }


def _python_sort(results: list[dict[str, Any]], sort_by: str, sort_order: str) -> None:
    """Sort results in-place by sort_by field. None values always go to the end."""
    field = _SORT_FIELD_MAP.get(sort_by, "xg_per_90")
    reverse = sort_order.lower() == "desc"

    if field in _STRING_SORT_FIELDS:
        results.sort(key=lambda r: (r.get(field) or "").lower(), reverse=reverse)
    else:
        # Tuple sort: (is_none, value) — None always last regardless of direction
        if reverse:
            results.sort(key=lambda r: (r.get(field) is None, -(r.get(field) or 0)))
        else:
            results.sort(key=lambda r: (r.get(field) is None, r.get(field) or 0))


# Champs rendus par _merge_season_stats. Sert a fabriquer une ligne vide
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


_SAISON_RE = re.compile(r"^(\d{4})-(\d{4})$")


def saison_precedente(season: str) -> str:
    """Saison precedant celle donnee : les deux annees reculent d'un cran."""
    m = _SAISON_RE.match(season)
    if not m or int(m.group(2)) != int(m.group(1)) + 1:
        raise ValueError(f"Format de saison invalide: {season!r}")
    debut = int(m.group(1))
    return f"{debut - 1}-{debut}"


def bloc_precedent(merged: dict[str, Any] | None) -> dict[str, Any] | None:
    """Valeurs de la saison precedente affichees sous celles de la saison courante.

    Ne porte que ce que la page montre : volumes en rappel, et statistiques
    ramenees au temps de jeu, seules a etre comparables entre deux saisons de
    longueurs differentes.
    """
    if not merged:
        return None
    return {
        "season": merged.get("season"),
        "matches_played": merged.get("matches_played"),
        "minutes_played": merged.get("minutes_played"),
        "goals": merged.get("goals"),
        "goal_assist": merged.get("goal_assist"),
        "xg_per_90": merged.get("xg_per_90"),
        "xa_per_90": merged.get("xa_per_90"),
        "avg_rating": merged.get("avg_rating"),
    }


async def _team_crest_map(
    session: AsyncSession, team_ids: list[int | None]
) -> dict[int, tuple[int | None, str | None]]:
    """Resout les ecussons (api_football_id + crest_url) d'un lot de clubs en
    un seul SELECT.

    canonical_teams.bzz_team_id partage le meme espace d'identifiants que
    BzzPlayer.current_team_api_id / loan_team_api_id. Un lookup par lot evite
    le N+1 (un SELECT par joueur) que ferait une resolution joueur par joueur.

    Rend `{bzz_team_id: (api_football_id, crest_url)}`. Un club absent de
    canonical_teams, ou present mais sans l'un ou l'autre champ, rend ce
    champ None via le tuple cote appelant : jamais un ecusson invente.
    """
    from app.models.canonical_teams import CanonicalTeam

    ids = {i for i in team_ids if i is not None}
    if not ids:
        return {}

    rows = (await session.execute(
        select(
            CanonicalTeam.bzz_team_id, CanonicalTeam.api_football_id, CanonicalTeam.crest_url
        ).where(CanonicalTeam.bzz_team_id.in_(ids))
    )).all()
    return {
        bzz_team_id: (api_football_id, crest_url)
        for bzz_team_id, api_football_id, crest_url in rows
    }


async def team_ids_for_league(
    session: AsyncSession, league_api_id: int, season: str
) -> list[int]:
    """Identifiants Bzzoiro des clubs engages dans ce championnat.

    Resolution par identifiant : le championnat est une colonne de
    canonical_teams depuis la migration 054. Il etait auparavant deduit en
    regroupant les joueurs par current_team_name, colonne fausse pour 37 %
    d'entre eux — d'ou des clubs andorrans dans les filtres.

    canonical_teams ne porte que l'engagement de la SAISON COURANTE. Pour une
    saison passee, les engages sont derives de bzz_events, qui couvre cinq
    saisons depuis le rattrapage du 21/08/2026. Cette derivation est
    approximative — la fenetre attrape les barrages, et le format des
    championnats a change (Ligue 1 est passee de 20 a 18 clubs) — mais elle
    rend l'historique consultable, ce qu'une liste vide ne fait pas.
    """
    from app.models.canonical_teams import CanonicalTeam

    result = await session.execute(
        select(CanonicalTeam.bzz_team_id).where(
            CanonicalTeam.league_api_id == league_api_id,
            CanonicalTeam.season == season,
            CanonicalTeam.bzz_team_id.is_not(None),
        )
    )
    engages = list(result.scalars().all())
    if engages:
        return engages

    # Repli reserve aux competitions au format connu. Pour une coupe, le
    # calendrier melange tours preliminaires et phase principale : la Ligue
    # des champions 2025-2026 rendait 141 clubs, dont des equipes amateurs,
    # et aucun seuil de matchs joues ne les separe proprement (71 clubs
    # encore a 8 matchs). Une liste vide est visible et se corrige ; une
    # liste de 141 clubs induit en erreur.
    if league_api_id not in EFFECTIFS_REGLEMENTAIRES:
        return []

    return await _team_ids_from_events(session, league_api_id, season)


async def _nommer_clubs(
    session: AsyncSession, ids: list[int]
) -> list[dict[str, Any]]:
    """Nomme des clubs a partir de leurs identifiants, tries par nom.

    canonical_teams fait autorite. Un club qui n'y figure pas — un relegue
    d'une saison ancienne, par exemple — est nomme d'apres ce que portent ses
    joueurs, et a defaut par son identifiant : mieux vaut une ligne nommee
    approximativement qu'un club absent de la liste.
    """
    from app.models.canonical_teams import CanonicalTeam

    noms: dict[int, str] = dict(
        (await session.execute(
            select(CanonicalTeam.bzz_team_id, CanonicalTeam.name_fr).where(
                CanonicalTeam.bzz_team_id.in_(ids)
            )
        )).all()
    )

    manquants = [i for i in ids if i not in noms]
    if manquants:
        restes = (await session.execute(
            select(
                BzzPlayer.current_team_api_id, BzzPlayer.current_team_name
            ).where(
                BzzPlayer.current_team_api_id.in_(manquants),
                BzzPlayer.current_team_name.is_not(None),
            ).distinct()
        )).all()
        for tid, nom in restes:
            noms.setdefault(tid, nom)

    return sorted(
        ({"api_id": i, "name": noms.get(i) or f"Club {i}"} for i in ids),
        key=lambda x: x["name"],
    )


async def _team_ids_from_events(
    session: AsyncSession, league_api_id: int, season: str
) -> list[int]:
    """Engages d'une saison passee, derives du calendrier.

    Repli utilise quand canonical_teams ne porte pas d'engagement pour la
    saison demandee. Les identifiants d'equipe de bzz_events relevent du meme
    espace que canonical_teams.bzz_team_id.
    """
    from app.models.bzzoiro import BzzEvent

    debut = season_start(season)
    fin = season_end(season)
    conditions = (
        BzzEvent.league_api_id == league_api_id,
        BzzEvent.event_date >= debut,
        BzzEvent.event_date < fin,
    )

    domicile = select(BzzEvent.home_team_api_id).where(
        *conditions, BzzEvent.home_team_api_id.is_not(None)
    )
    exterieur = select(BzzEvent.away_team_api_id).where(
        *conditions, BzzEvent.away_team_api_id.is_not(None)
    )

    result = await session.execute(domicile.union(exterieur))
    return [r[0] for r in result.all()]



# ---------------------------------------------------------------------------
# GET /players/leagues  — championnats disponibles
# ---------------------------------------------------------------------------


@router.get("/leagues", response_model=list[dict])
async def list_player_leagues(
    session: AsyncSession = Depends(get_db),
) -> list[dict[str, Any]]:
    """Return the 6 target leagues (hardcoded IDs)."""
    result = await session.execute(
        select(BzzLeague.api_id, BzzLeague.name)
        .where(BzzLeague.api_id.in_(TARGET_LEAGUE_IDS))
        .order_by(BzzLeague.name)
    )
    return [{"api_id": row[0], "name": row[1]} for row in result.all()]


# ---------------------------------------------------------------------------
# GET /players/seasons — saisons disponibles (selecteur de la page Joueurs)
# ---------------------------------------------------------------------------


@router.get("/seasons", response_model=list[dict])
async def list_player_seasons(
    session: AsyncSession = Depends(get_db),
) -> list[dict[str, Any]]:
    """Saisons ayant des statistiques, de la plus recente a la plus ancienne.

    La liste vient des donnees et non d'un litteral : rien a retoucher chaque
    ete. La saison en cours y figure toujours, meme si aucun match n'a encore
    ete joue — sans quoi le selecteur ne la proposerait pas en aout.
    """
    result = await session.execute(
        select(BzzPlayerSeasonStat.season)
        .where(BzzPlayerSeasonStat.league_api_id.in_(_ALL_LEAGUE_IDS))
        .distinct()
    )
    saisons = {s for s in result.scalars().all() if s}

    courante = await current_season(session)
    saisons.add(courante)

    return [
        {"season": s, "current": s == courante}
        for s in sorted(saisons, reverse=True)
    ]


# ---------------------------------------------------------------------------
# GET /players/teams  — équipes du championnat (ou toutes)
# ---------------------------------------------------------------------------


@router.get("/teams", response_model=list[dict])
async def list_player_teams(
    session: AsyncSession = Depends(get_db),
    league_api_id: int | None = Query(None, description="Filter by league api_id; -1 = Autres"),
    season: str | None = Query(None),
) -> list[dict[str, Any]]:
    """Return teams deduplicated by name, filtered by dominant league.

    league_api_id=None → all teams; league_api_id=-1 → non-Big5/UCL teams; 1-7 → specific league.
    """
    if season is None:
        season = await current_season(session)

    # Championnat demande : la liste vient directement du referentiel. C'est ce
    # qui garantit mecaniquement les 18 ou 20 clubs, sans dependre de ce que
    # les statistiques laissent deviner — un club sans aucun joueur en base
    # doit tout de meme apparaitre dans son championnat.
    # La structure ne suit PAS la saison demandee : les clubs et les effectifs
    # restent ceux de la saison en cours, ce sont les joueurs actifs a pricer.
    # Le parametre season ne concerne que les statistiques affichees.
    if league_api_id is not None:
        ids = await team_ids_for_league(
            session, league_api_id, await current_season(session)
        )
        if not ids:
            return []
        return await _nommer_clubs(session, ids)

    stmt = text("""
        WITH team_counts AS (
            SELECT
                COALESCE(bp.loan_team_api_id, bp.current_team_api_id) AS eff_team_api_id,
                COALESCE(bp.loan_team_name, bp.current_team_name)      AS eff_team_name,
                COUNT(*) AS cnt
            FROM bzz_players bp
            JOIN bzz_player_season_stats bpss ON bpss.player_api_id = bp.api_id
            WHERE bpss.season = :season
              AND COALESCE(bp.loan_team_api_id, bp.current_team_api_id) IS NOT NULL
              AND COALESCE(bp.loan_team_name, bp.current_team_name) IS NOT NULL
            GROUP BY eff_team_api_id, eff_team_name
        ),
        ranked AS (
            SELECT eff_team_api_id, eff_team_name,
                   ROW_NUMBER() OVER (PARTITION BY eff_team_name ORDER BY cnt DESC) AS rn
            FROM team_counts
        )
        SELECT eff_team_api_id, eff_team_name FROM ranked WHERE rn = 1
        ORDER BY eff_team_name
    """)
    result = await session.execute(stmt, {"season": season})
    rows = result.all()

    # Ne sert plus que le cas "toutes competitions" : le cas d'un championnat
    # donne est sorti plus haut, directement depuis le referentiel.
    return [{"api_id": api_id, "name": name} for api_id, name in rows]


# ---------------------------------------------------------------------------
# Legacy export endpoint — kept for backwards compatibility
# ---------------------------------------------------------------------------


@router.get("/export", summary="Export player stats as CSV")
async def export_players_csv(
    session: AsyncSession = Depends(get_db),
    league_api_id: int | None = Query(
        None,
        description="League API ID to export, or omit for all",
    ),
    season: str | None = Query(None),
) -> StreamingResponse:
    """Stream aggregated player stats as a UTF-8 CSV — one row per player."""
    if season is None:
        season = await current_season(session)
    # Fetch players with their season stats (all leagues), then aggregate per player
    player_id_subq = select(BzzPlayer.api_id).where(BzzPlayer.internal_id.is_not(None))
    if league_api_id:
        team_ids = await team_ids_for_league(session, league_api_id, season)
        if team_ids:
            eff_team = func.coalesce(
                BzzPlayer.loan_team_api_id, BzzPlayer.current_team_api_id
            )
            player_id_subq = player_id_subq.where(eff_team.in_(team_ids))

    players = (await session.execute(
        select(BzzPlayer).where(BzzPlayer.api_id.in_(player_id_subq))
    )).scalars().all()
    if not players:
        buf = io.StringIO()
        writer = csv.DictWriter(buf, fieldnames=CSV_FIELDS, extrasaction="ignore")
        writer.writeheader()
        buf.seek(0)
        filename = f"ev0_players_{datetime.now(tz=UTC).strftime('%Y%m%d_%H%M%S')}.csv"
        return StreamingResponse(
            iter([buf.getvalue()]),
            media_type="text/csv",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    stats_rows = (await session.execute(
        select(BzzPlayerSeasonStat)
        .where(BzzPlayerSeasonStat.player_api_id.in_(player_id_subq), BzzPlayerSeasonStat.season == season)
    )).scalars().all()

    stats_by_player: dict[int, list] = defaultdict(list)
    for s in stats_rows:
        stats_by_player[s.player_api_id].append(s)

    player_by_id = {p.api_id: p for p in players}

    rows = []
    for pid, player in player_by_id.items():
        merged = _merge_season_stats(stats_by_player.get(pid, []), season)
        if not merged:
            continue
        rows.append({
            "name": player.name,
            "team": player.loan_team_name or player.current_team_name or "",
            "position": player.position or "",
            "league": "",  # multi-league; no single league label
            "games": merged["matches_played"],
            "minutes": merged["minutes_played"],
            "goals": merged["goals"],
            "assists": merged["goal_assist"],
            "xg": merged["expected_goals"],
            "xa": merged["expected_assists"],
            "shots": merged["total_shots"],
            "key_passes": merged["key_pass"],
            "xg_per_90": merged["xg_per_90"],
            "xa_per_90": merged["xa_per_90"],
        })

    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=CSV_FIELDS, extrasaction="ignore")
    writer.writeheader()
    writer.writerows(rows)
    buf.seek(0)

    filename = f"ev0_players_{datetime.now(tz=UTC).strftime('%Y%m%d_%H%M%S')}.csv"
    return StreamingResponse(
        iter([buf.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# ---------------------------------------------------------------------------
# GET /players/  — list
# ---------------------------------------------------------------------------


@router.get("", response_model=list[PlayerSummary])
async def list_players(
    session: AsyncSession = Depends(get_db),
    league_api_id: int | None = Query(None, description="Filter by league API id"),
    team_api_id: int | None = Query(None, description="Filter by team API id"),
    position: str | None = Query(None, description="Filter by position: G/D/M/F"),
    q: str | None = Query(None, description="Filter by player name (case-insensitive substring)"),
    min_minutes: int = Query(0, description="Minimum minutes played"),
    season: str | None = Query(None),
    compare_previous: bool = Query(
        False,
        description="Joindre les stats de la saison precedente sous chaque valeur",
    ),
    sort_by: str = Query("xg_per_90"),
    sort_order: str = Query("desc"),
    limit: int = Query(50, le=500),
    offset: int = Query(0),
) -> list[dict[str, Any]]:
    """List players — one row per player, stats aggregated across all competitions."""
    if season is None:
        season = await current_season(session)

    # Step 1: build player API-ID subquery with all identity filters.
    # Using a subquery (not a Python list) avoids asyncpg's 32 767 parameter limit
    # when the unfiltered player set is large.
    player_id_subq = select(BzzPlayer.api_id).where(BzzPlayer.internal_id.is_not(None))

    restrict_stats_league: int | None = None
    if league_api_id is not None:
        if league_api_id in INTL_CLUB_COMP_LEAGUE_IDS:
            # Compétition internationale de clubs (LdC, Europa, Conference,
            # Super Coupe, Libertadores/Sudamericana) : aucun club n'a cet
            # identifiant comme league_api_id dans le référentiel, donc
            # team_ids_for_league rend toujours []. On résout les joueurs par
            # présence d'une ligne de stats DANS cette compétition, puis on
            # restreint les stats affichées à cette seule compétition (pas
            # l'agrégat toutes-compétitions).
            restrict_stats_league = league_api_id
            player_id_subq = player_id_subq.where(
                BzzPlayer.api_id.in_(
                    select(BzzPlayerSeasonStat.player_api_id).where(
                        BzzPlayerSeasonStat.league_api_id == league_api_id,
                        BzzPlayerSeasonStat.season == season,
                    )
                )
            )
        else:
            # Championnat domestique : effectif de la saison en cours, quelle
            # que soit la saison des stats.
            team_ids = await team_ids_for_league(
                session, league_api_id, await current_season(session)
            )
            if not team_ids:
                return []
            eff_team = func.coalesce(
                BzzPlayer.loan_team_api_id, BzzPlayer.current_team_api_id
            )
            player_id_subq = player_id_subq.where(eff_team.in_(team_ids))
    if team_api_id is not None:
        # Un joueur appartient a UN club : celui que porte l'effectif
        # Transfermarkt (`sync_squads`), seule autorite sur la composition.
        # L'ancien filtre retombait aussi sur le club de PRET, ce qui faisait
        # apparaitre le meme joueur dans DEUX effectifs des qu'une etiquette
        # trainait — c'est ainsi que Newcastle et Tottenham se melangeaient.
        player_id_subq = player_id_subq.where(
            BzzPlayer.current_team_api_id == team_api_id
        )
    if position is not None:
        player_id_subq = player_id_subq.where(BzzPlayer.position == position.upper())
    if q:
        # Atlas Explorer search box: server-side so it composes with real
        # limit/offset pagination instead of only matching the current page.
        player_id_subq = player_id_subq.where(BzzPlayer.name.ilike(f"%{q}%"))

    players = (await session.execute(
        select(BzzPlayer).where(BzzPlayer.api_id.in_(player_id_subq))
    )).scalars().all()
    if not players:
        return []

    # Step 2: fetch ALL season stat rows using the same subquery (no parameter list)
    stats_filter = [
        BzzPlayerSeasonStat.player_api_id.in_(player_id_subq),
        BzzPlayerSeasonStat.season == season,
    ]
    if restrict_stats_league is not None:
        # Compétition internationale de clubs : n'afficher que les stats de
        # cette compétition, pas l'agrégat toutes-compétitions.
        stats_filter.append(BzzPlayerSeasonStat.league_api_id == restrict_stats_league)
    all_stats = (await session.execute(
        select(BzzPlayerSeasonStat).where(*stats_filter)
    )).scalars().all()

    # Appele directement (tests), compare_previous porte l'objet Query, qui est
    # toujours vrai : on teste donc l'identite, pas la veracite.
    comparer = compare_previous is True
    saison_prec = saison_precedente(season) if comparer else None
    stats_prec_by_player: dict[int, list] = defaultdict(list)
    if saison_prec:
        stats_prec_filter = [
            BzzPlayerSeasonStat.player_api_id.in_(player_id_subq),
            BzzPlayerSeasonStat.season == saison_prec,
        ]
        if restrict_stats_league is not None:
            stats_prec_filter.append(
                BzzPlayerSeasonStat.league_api_id == restrict_stats_league
            )
        for row in (await session.execute(
            select(BzzPlayerSeasonStat).where(*stats_prec_filter)
        )).scalars().all():
            stats_prec_by_player[row.player_api_id].append(row)

    # Step 3: group by player
    stats_by_player: dict[int, list] = defaultdict(list)
    for s in all_stats:
        stats_by_player[s.player_api_id].append(s)

    # Deduplicate by internal_id — same real player may have multiple api_ids
    # (sync_players.py uses api_id=row.get("api_id") or row.get("id"))
    _by_internal: dict[int, list] = defaultdict(list)
    for _p in players:
        if _p.internal_id is not None:
            _by_internal[_p.internal_id].append(_p)
    player_by_id: dict[int, Any] = {}
    for _group in _by_internal.values():
        if len(_group) == 1:
            _best = _group[0]
        else:
            _best = max(_group, key=lambda _p: len(stats_by_player.get(_p.api_id, [])))
        player_by_id[_best.api_id] = _best

    # Step 4: aggregate and build output — one dict per player
    # Ecussons : meme club effectif que team_name (pret si present, sinon club
    # actuel), resolus en un seul lot plutot qu'un SELECT par joueur.
    crest_map = await _team_crest_map(
        session,
        [p.loan_team_api_id or p.current_team_api_id for p in player_by_id.values()],
    )

    results: list[dict[str, Any]] = []
    for pid, player in player_by_id.items():
        rows = stats_by_player.get(pid)
        rows_prec = stats_prec_by_player.get(pid)

        # Un joueur sans stats de la saison courante reste affiche dans deux
        # cas, ou son absence serait une perte d'information :
        #   - en comparaison de saisons : ses colonnes montrent des tirets ;
        #   - quand un club precis est demande : on regarde alors un EFFECTIF,
        #     et un effectif se lit en entier. Debut septembre, apres deux
        #     journees, filtrer sur les stats vidait la liste de ses blesses,
        #     de ses gardiens remplacants et de ses recrues sans debut — a
        #     Newcastle, 4 joueurs sur 25 disparaissaient, dont Nick Pope.
        #     "Pope est dans l'effectif et n'a pas joue" vaut mieux que "Pope
        #     n'existe pas".
        # La vue par championnat, elle, reste filtree : y faire remonter des
        # milliers de joueurs sans stats n'aide aucun classement.
        garder_sans_stats = bool(comparer and rows_prec) or team_api_id is not None
        if not rows and not garder_sans_stats:
            continue

        merged = _merge_season_stats(rows, season) if rows else None
        if not merged and not garder_sans_stats:
            continue
        if merged is None:
            merged = {k: None for k in _CHAMPS_VIDES}
            merged["season"] = season

        if min_minutes > 0 and (merged.get("minutes_played") or 0) < min_minutes:
            continue

        eff_team_id = player.loan_team_api_id or player.current_team_api_id
        eff_api_football_id, eff_crest_url = crest_map.get(eff_team_id) or (None, None)

        results.append({
            "player_api_id": player.api_id,
            "name": player.name,
            "short_name": player.short_name,
            "position": player.position,
            "team_name": player.loan_team_name or player.current_team_name,
            "api_football_id": eff_api_football_id,
            "crest_url": eff_crest_url,
            "nationality": player.nationality,
            "goals": merged["goals"],
            "goal_assist": merged["goal_assist"],
            "xg_per_90": merged["xg_per_90"],
            "xa_per_90": merged["xa_per_90"],
            "avg_rating": merged["avg_rating"],
            "shots_on_target_per_90": merged["shots_on_target_per_90"],
            "form_xg_5": merged["form_xg_5"],
            "matches_played": merged["matches_played"],
            "minutes_played": merged["minutes_played"],
            "season": merged["season"],
            "previous": bloc_precedent(
                _merge_season_stats(rows_prec, saison_prec) if rows_prec else None
            ) if comparer else None,
            # Extra numeric fields needed for sort but not in PlayerSummary schema
            "total_shots": merged["total_shots"],
            "shots_on_target": merged["shots_on_target"],
            "key_pass": merged["key_pass"],
            "expected_goals": merged["expected_goals"],
            "expected_assists": merged["expected_assists"],
            "yellow_card": merged["yellow_card"],
            "red_card": merged["red_card"],
            "saves": merged["saves"],
            "shots_per_90": merged["shots_per_90"],
            "key_pass_per_90": merged["key_pass_per_90"],
            "accurate_cross_per_90": merged["accurate_cross_per_90"],
            "recoveries_per_90": merged["recoveries_per_90"],
            "tackles_per_90": merged["tackles_per_90"],
            "interceptions_per_90": merged["interceptions_per_90"],
            "shot_accuracy": merged["shot_accuracy"],
            "xg_per_shot": merged["xg_per_shot"],
            "finishing_delta": merged["finishing_delta"],
            "xa_delta": merged["xa_delta"],
            "pass_completion": merged["pass_completion"],
            "long_ball_accuracy": merged["long_ball_accuracy"],
            "cross_accuracy": merged["cross_accuracy"],
            "duel_win_rate": merged["duel_win_rate"],
            "aerial_win_rate": merged["aerial_win_rate"],
            "tackle_success_rate": merged["tackle_success_rate"],
            "avg_minutes_per_match": merged["avg_minutes_per_match"],
            "starts_pct": merged["starts_pct"],
            "form_rating_5": merged["form_rating_5"],
            "form_goals_5": merged["form_goals_5"],
            "form_assists_5": merged["form_assists_5"],
            "rating_trend": merged["rating_trend"],
        })

    # Step 5: sort in Python, then paginate
    _python_sort(results, sort_by, sort_order)
    return results[offset: offset + limit]


# ---------------------------------------------------------------------------
# GET /players/selection  — joueurs d'une selection nationale
# ---------------------------------------------------------------------------
# Doit rester declare AVANT la route /{player_api_id} : sinon FastAPI
# matche "selection" comme une valeur de player_api_id.


@router.get("/selection", response_model=list[PlayerSummary])
async def list_selection_players(
    session: AsyncSession = Depends(get_db),
    national_team_api_id: int | None = Query(None, description="Filter by national team api_id"),
    league_api_id: int | None = Query(None, description="Filter by selection competition api_id"),
    position: str | None = Query(None, description="Filter by position: G/D/M/F"),
    q: str | None = Query(None, description="Filter by player name (case-insensitive substring)"),
    season: str | None = Query(None),
    sort_by: str = Query("avg_rating"),
    sort_order: str = Query("desc"),
    limit: int = Query(50, le=500),
    offset: int = Query(0),
) -> list[dict[str, Any]]:
    """Joueurs d'une selection nationale, stats lues depuis player_selection_season_stats.

    Quand national_team_api_id est fourni, ne rend que les joueurs de cette
    selection ; sinon tous les joueurs ayant des stats de selection. Quand
    league_api_id est omis, les stats agregent toutes les competitions de
    selection du joueur (Coupe du monde, Ligue des Nations, qualifications...).
    """
    conds = [PlayerSelectionSeasonStat.source == "bzzoiro"]
    if national_team_api_id is not None:
        conds.append(PlayerSelectionSeasonStat.national_team_api_id == national_team_api_id)
    if league_api_id is not None:
        conds.append(PlayerSelectionSeasonStat.league_api_id == league_api_id)
    if season is not None:
        conds.append(PlayerSelectionSeasonStat.season == season)

    sel_rows = (await session.execute(
        select(PlayerSelectionSeasonStat).where(*conds)
    )).scalars().all()
    if not sel_rows:
        return []

    by_player: dict[int, list] = defaultdict(list)
    for s in sel_rows:
        by_player[s.player_api_id].append(s)

    player_ids = list(by_player.keys())
    players = {p.api_id: p for p in (await session.execute(
        select(BzzPlayer).where(BzzPlayer.api_id.in_(player_ids))
    )).scalars().all()}

    crest_map = await _team_crest_map(
        session, [p.current_team_api_id for p in players.values()]
    )

    results: list[dict[str, Any]] = []
    for pid, rows in by_player.items():
        player = players.get(pid)
        if player is None:
            continue
        if position is not None and (player.position or "").upper() != position.upper():
            continue
        if q and q.lower() not in (player.name or "").lower():
            continue
        merged = _merge_season_stats(rows, rows[0].season)
        if merged is None:
            continue
        api_football_id, crest_url = crest_map.get(player.current_team_api_id) or (None, None)
        results.append({
            "player_api_id": player.api_id,
            "name": player.name,
            "short_name": player.short_name,
            "position": player.position,
            "team_name": player.current_team_name,
            "api_football_id": api_football_id,
            "crest_url": crest_url,
            "nationality": player.nationality,
            "goals": merged["goals"],
            "goal_assist": merged["goal_assist"],
            "xg_per_90": merged["xg_per_90"],
            "xa_per_90": merged["xa_per_90"],
            "avg_rating": merged["avg_rating"],
            "shots_on_target_per_90": merged["shots_on_target_per_90"],
            "form_xg_5": merged["form_xg_5"],
            "matches_played": merged["matches_played"],
            "minutes_played": merged["minutes_played"],
            "season": merged["season"],
            "previous": None,
            # Champs numeriques necessaires au tri (_python_sort), absents du
            # schema PlayerSummary — meme convention que list_players.
            "total_shots": merged["total_shots"],
            "shots_on_target": merged["shots_on_target"],
            "key_pass": merged["key_pass"],
            "expected_goals": merged["expected_goals"],
            "expected_assists": merged["expected_assists"],
            "yellow_card": merged["yellow_card"],
            "red_card": merged["red_card"],
            "saves": merged["saves"],
            "shots_per_90": merged["shots_per_90"],
            "key_pass_per_90": merged["key_pass_per_90"],
            "accurate_cross_per_90": merged["accurate_cross_per_90"],
            "recoveries_per_90": merged["recoveries_per_90"],
            "tackles_per_90": merged["tackles_per_90"],
            "interceptions_per_90": merged["interceptions_per_90"],
            "shot_accuracy": merged["shot_accuracy"],
            "xg_per_shot": merged["xg_per_shot"],
            "finishing_delta": merged["finishing_delta"],
            "xa_delta": merged["xa_delta"],
            "pass_completion": merged["pass_completion"],
            "long_ball_accuracy": merged["long_ball_accuracy"],
            "cross_accuracy": merged["cross_accuracy"],
            "duel_win_rate": merged["duel_win_rate"],
            "aerial_win_rate": merged["aerial_win_rate"],
            "tackle_success_rate": merged["tackle_success_rate"],
            "avg_minutes_per_match": merged["avg_minutes_per_match"],
            "starts_pct": merged["starts_pct"],
            "form_rating_5": merged["form_rating_5"],
            "form_goals_5": merged["form_goals_5"],
            "form_assists_5": merged["form_assists_5"],
            "rating_trend": merged["rating_trend"],
        })

    _python_sort(results, sort_by, sort_order)
    return results[offset: offset + limit]


# ---------------------------------------------------------------------------
# GET /players/{player_api_id}/breakdown  — stats par (saison x competition)
# ---------------------------------------------------------------------------
# Doit rester declare AVANT la route /{player_api_id} : sinon FastAPI
# matche "breakdown" comme une valeur de player_api_id (meme convention que
# /selection ci-dessus).
#
# Agrege bzz_player_match_stats (pas bzz_player_season_stats) : la maquette a
# besoin de champs absents de la table saison (dispossessed, possession_lost,
# was_fouled, total_clearance, aerial_lost). La saison est calculee en SQL
# depuis e.event_date (bascule au 1er aout, meme regle que season_service) et
# fait partie du GROUP BY : une ligne par (saison, ligue).


_BREAKDOWN_SQL = text("""
    SELECT
        CASE WHEN EXTRACT(MONTH FROM e.event_date) >= 8
             THEN EXTRACT(YEAR FROM e.event_date)::int
             ELSE EXTRACT(YEAR FROM e.event_date)::int - 1
        END AS season_start_year,
        e.league_api_id AS league_api_id,
        l.name AS league_name,
        l.kind AS kind,
        COUNT(DISTINCT ms.event_api_id) AS matches_played,
        SUM(ms.minutes_played) AS minutes_played,
        COUNT(DISTINCT ms.event_api_id) FILTER (WHERE ms.minutes_played >= 60) AS starts,
        SUM(ms.goals) AS goals,
        SUM(ms.goal_assist) AS goal_assist,
        SUM(ms.expected_goals) AS expected_goals,
        SUM(ms.expected_assists) AS expected_assists,
        SUM(ms.total_shots) AS total_shots,
        SUM(ms.shots_on_target) AS shots_on_target,
        SUM(ms.key_pass) AS key_pass,
        SUM(ms.total_pass) AS total_pass,
        SUM(ms.accurate_pass) AS accurate_pass,
        SUM(ms.total_cross) AS total_cross,
        SUM(ms.accurate_cross) AS accurate_cross,
        SUM(ms.total_long_balls) AS total_long_balls,
        SUM(ms.accurate_long_balls) AS accurate_long_balls,
        SUM(ms.duel_won) AS duel_won,
        SUM(ms.duel_lost) AS duel_lost,
        SUM(ms.aerial_won) AS aerial_won,
        SUM(ms.aerial_lost) AS aerial_lost,
        SUM(ms.total_tackle) AS total_tackle,
        SUM(ms.won_tackle) AS won_tackle,
        SUM(ms.interception) AS interception,
        SUM(ms.ball_recovery) AS ball_recovery,
        SUM(ms.total_clearance) AS total_clearance,
        SUM(ms.dispossessed) AS dispossessed,
        SUM(ms.possession_lost) AS possession_lost,
        SUM(ms.was_fouled) AS was_fouled,
        SUM(ms.fouls) AS fouls,
        SUM(ms.yellow_card) AS yellow_card,
        SUM(ms.red_card) AS red_card,
        AVG(ms.rating) AS avg_rating
    FROM bzz_player_match_stats ms
    JOIN bzz_events e ON e.api_id = ms.event_api_id
    JOIN leagues l ON l.id = e.league_api_id
    WHERE ms.player_api_id = :player_api_id
      AND e.status = 'finished'
    GROUP BY
        CASE WHEN EXTRACT(MONTH FROM e.event_date) >= 8
             THEN EXTRACT(YEAR FROM e.event_date)::int
             ELSE EXTRACT(YEAR FROM e.event_date)::int - 1
        END,
        e.league_api_id, l.name, l.kind
    ORDER BY season_start_year DESC, l.name
""")


@router.get("/{player_api_id}/breakdown", response_model=PlayerBreakdownOut)
async def get_player_breakdown(
    player_api_id: int,
    session: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Totaux agreges par (saison, competition), pour les selecteurs de la fiche joueur.

    Pas de 404 sur joueur inconnu : un player_api_id sans aucune ligne de
    match rend simplement rows=[], comme pour une question qui porte sur des
    stats plutot que sur l'identite du joueur.
    """
    result = await session.execute(_BREAKDOWN_SQL, {"player_api_id": player_api_id})
    rows = result.mappings().all()

    out_rows: list[BreakdownRow] = []
    for row in rows:
        start_year = int(row["season_start_year"])
        out_rows.append(BreakdownRow(
            season=f"{start_year}-{start_year + 1}",
            league_api_id=row["league_api_id"],
            league_name=row["league_name"],
            kind=row["kind"],
            matches_played=row["matches_played"],
            minutes_played=row["minutes_played"],
            starts=row["starts"],
            goals=row["goals"],
            goal_assist=row["goal_assist"],
            expected_goals=row["expected_goals"],
            expected_assists=row["expected_assists"],
            total_shots=row["total_shots"],
            shots_on_target=row["shots_on_target"],
            key_pass=row["key_pass"],
            total_pass=row["total_pass"],
            accurate_pass=row["accurate_pass"],
            total_cross=row["total_cross"],
            accurate_cross=row["accurate_cross"],
            total_long_balls=row["total_long_balls"],
            accurate_long_balls=row["accurate_long_balls"],
            duel_won=row["duel_won"],
            duel_lost=row["duel_lost"],
            aerial_won=row["aerial_won"],
            aerial_lost=row["aerial_lost"],
            total_tackle=row["total_tackle"],
            won_tackle=row["won_tackle"],
            interception=row["interception"],
            ball_recovery=row["ball_recovery"],
            total_clearance=row["total_clearance"],
            dispossessed=row["dispossessed"],
            possession_lost=row["possession_lost"],
            was_fouled=row["was_fouled"],
            fouls=row["fouls"],
            yellow_card=row["yellow_card"],
            red_card=row["red_card"],
            avg_rating=row["avg_rating"],
        ))

    return {"rows": out_rows}


# ---------------------------------------------------------------------------
# GET /players/{player_api_id}/match-odds — cotes closing Betclic (buteur/passeur)
# ---------------------------------------------------------------------------
# Doit rester declare AVANT la route /{player_api_id} : sinon FastAPI
# matche "match-odds" comme une valeur de player_api_id (meme convention que
# /selection et /breakdown ci-dessus).
#
# Jointure verifiee : bzz_events -> fixtures (equipes + date de kickoff) ->
# player_odds_snapshots (bookmaker betclic, nom du joueur) -> bzz_player_match_stats
# (ne garder que les events ou ce joueur a effectivement une ligne de stats).
#
# Voulu : la fiche affiche un journal RETROSPECTIF (matchs joues), pas du
# pre-match. On ne rattache donc les cotes qu'aux events pour lesquels ce
# joueur a deja une ligne dans bzz_player_match_stats — ces lignes n'existent
# que pour les events "finished". Un match a venir n'a pas encore de stats et
# n'apparait donc jamais ici, ce qui est l'effet recherche.
#
# Le "closing" est la cote Betclic au scraped_at le plus recent par
# (fixture, market_type, player_name) : DISTINCT ON fait ce choix cote SQL.
# ATTENTION matching de nom : player_odds_snapshots.player_name vient du
# scraping Betclic et peut differer de BzzPlayer.name par un accent, un tiret
# ou une apostrophe ("Mbappe" vs "Mbappé") — un ILIKE exact sur le nom brut
# echoue alors SILENCIEUSEMENT (cotes absentes, pas d'erreur). On ne filtre
# donc plus par nom en SQL : le SQL ne fait que borner la recherche aux
# fixtures des matchs DEJA JOUES par ce joueur (sous-requete sur
# bzz_player_match_stats) pour garder une volumetrie raisonnable, puis le
# rattachement par nom se fait en Python via _names_match (meme fonction que
# l'auto-settlement des recommandations, qui gere deja ces cas).
# Un event sans fixture ou sans cote Betclic au nom correspondant est absent
# de la liste — le front affiche alors un tiret.

_MATCH_ODDS_SQL = text("""
    WITH closing AS (
        SELECT DISTINCT ON (o.fixture_id, o.market_type, o.player_name)
            o.fixture_id, o.market_type, o.player_name, o.odds
        FROM player_odds_snapshots o
        WHERE o.bookmaker = 'betclic'
          AND o.fixture_id IN (
              SELECT f.id
              FROM bzz_events e
              JOIN fixtures f
                  ON f.home_bzz_team_id = e.home_team_api_id
                 AND f.away_bzz_team_id = e.away_team_api_id
                 AND f.kickoff_utc::date = e.event_date::date
              JOIN bzz_player_match_stats ms
                  ON ms.event_api_id = e.api_id AND ms.player_api_id = :player_api_id
          )
        ORDER BY o.fixture_id, o.market_type, o.player_name, o.scraped_at DESC
    )
    SELECT
        e.api_id AS event_api_id,
        c.market_type,
        c.player_name,
        c.odds
    FROM bzz_events e
    JOIN fixtures f
        ON f.home_bzz_team_id = e.home_team_api_id
       AND f.away_bzz_team_id = e.away_team_api_id
       AND f.kickoff_utc::date = e.event_date::date
    JOIN closing c ON c.fixture_id = f.id
    JOIN bzz_player_match_stats ms
        ON ms.event_api_id = e.api_id AND ms.player_api_id = :player_api_id
    ORDER BY e.api_id
""")


@router.get("/{player_api_id}/match-odds", response_model=PlayerMatchOddsOut)
async def get_player_match_odds(
    player_api_id: int,
    session: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Cotes de closing Betclic (buteur/passeur) par match pour la fiche joueur.

    Pas de 404 sur joueur inconnu, meme convention que /breakdown : rend
    simplement odds=[].
    """
    from app.ingestion.auto_settle import _names_match

    nom = (await session.execute(
        select(BzzPlayer.name).where(BzzPlayer.api_id == player_api_id)
    )).scalar_one_or_none()
    if not nom:
        return {"odds": []}

    result = await session.execute(
        _MATCH_ODDS_SQL, {"player_api_id": player_api_id}
    )
    rows = result.mappings().all()

    # Pivot buteur/passeur par event_api_id, en ne gardant que les lignes de
    # cote dont le nom scrape correspond reellement a ce joueur (accents,
    # tirets, apostrophes neutralises par _names_match) — voir commentaire
    # ci-dessus sur pourquoi le filtre de nom se fait ici et pas en SQL.
    pivot: dict[int, dict[str, float | None]] = {}
    for row in rows:
        if not _names_match(row["player_name"], nom):
            continue
        entry = pivot.setdefault(row["event_api_id"], {"buteur": None, "passeur": None})
        if row["market_type"] == "goalscorer":
            entry["buteur"] = row["odds"]
        elif row["market_type"] == "assist":
            entry["passeur"] = row["odds"]

    return {
        "odds": [
            {
                "event_api_id": event_api_id,
                "buteur": entry["buteur"],
                "passeur": entry["passeur"],
            }
            for event_api_id, entry in sorted(pivot.items())
        ]
    }


# ---------------------------------------------------------------------------
# GET /players/{player_api_id}  — detail
# ---------------------------------------------------------------------------


@router.get("/{player_api_id}", response_model=PlayerDetail)
async def get_player(
    player_api_id: int,
    session: AsyncSession = Depends(get_db),
    season: str | None = Query(None),
) -> dict[str, Any]:
    """Get a single player with season stats and recent matches."""
    if season is None:
        season = await current_season(session)

    # 1. Fetch player
    player_result = await session.execute(
        select(BzzPlayer).where(BzzPlayer.api_id == player_api_id)
    )
    player = player_result.scalars().first()
    if player is None:
        raise HTTPException(status_code=404, detail="Player not found")

    # 2. Season stats — aggregate ALL league rows (prevents partial data from first() pick)
    season_stat_rows = (await session.execute(
        select(BzzPlayerSeasonStat).where(
            BzzPlayerSeasonStat.player_api_id == player_api_id,
            BzzPlayerSeasonStat.season == season,
        )
    )).scalars().all()

    merged = _merge_season_stats(season_stat_rows, season)

    # 3. Recent matches (all available) — join to BzzEvent for date and teams
    home_team_alias = BzzTeam.__table__.alias("ht")
    away_team_alias = BzzTeam.__table__.alias("at_")

    recent_result = await session.execute(
        select(
            BzzPlayerMatchStat,
            BzzEvent.event_date,
            BzzEvent.home_team_api_id,
            BzzEvent.away_team_api_id,
            home_team_alias.c.name.label("home_team_name"),
            away_team_alias.c.name.label("away_team_name"),
            BzzEvent.league_api_id,
        )
        .join(BzzEvent, BzzEvent.api_id == BzzPlayerMatchStat.event_api_id)
        .outerjoin(home_team_alias, home_team_alias.c.api_id == BzzEvent.home_team_api_id)
        .outerjoin(away_team_alias, away_team_alias.c.api_id == BzzEvent.away_team_api_id)
        .where(
            BzzPlayerMatchStat.player_api_id == player_api_id,
            BzzEvent.league_api_id.in_(RECENT_MATCHES_LEAGUE_IDS),
        )
        .order_by(desc(BzzEvent.event_date))
    )
    recent_rows = recent_result.all()

    recent_matches: list[RecentMatch] = []
    for ms, event_date, home_api_id, away_api_id, home_name, away_name, league_api_id in recent_rows:
        # Use team_api_id from the stat row; fall back to the player's current team
        player_team_id = ms.team_api_id or player.current_team_api_id
        if player_team_id is not None:
            if player_team_id == home_api_id:
                is_home: bool | None = True
            elif player_team_id == away_api_id:
                is_home = False
            else:
                is_home = ms.is_home
        else:
            is_home = ms.is_home

        # Only show opponent when we can determine side; avoids showing player's own team
        if is_home is True:
            opponent: str | None = away_name
        elif is_home is False:
            opponent = home_name
        else:
            opponent = None

        recent_matches.append(
            RecentMatch(
                event_api_id=ms.event_api_id,
                event_date=event_date,
                league_api_id=league_api_id,
                opponent=opponent,
                is_home=is_home,
                minutes_played=ms.minutes_played,
                rating=ms.rating,
                touches=ms.touches,
                goals=ms.goals,
                goal_assist=ms.goal_assist,
                expected_goals=ms.expected_goals,
                expected_assists=ms.expected_assists,
                total_shots=ms.total_shots,
                shots_on_target=ms.shots_on_target,
                total_pass=ms.total_pass,
                accurate_pass=ms.accurate_pass,
                key_pass=ms.key_pass,
                total_long_balls=ms.total_long_balls,
                accurate_long_balls=ms.accurate_long_balls,
                total_cross=ms.total_cross,
                accurate_cross=ms.accurate_cross,
                duel_won=ms.duel_won,
                duel_lost=ms.duel_lost,
                aerial_won=ms.aerial_won,
                aerial_lost=ms.aerial_lost,
                total_tackle=ms.total_tackle,
                won_tackle=ms.won_tackle,
                total_clearance=ms.total_clearance,
                interception=ms.interception,
                ball_recovery=ms.ball_recovery,
                yellow_card=ms.yellow_card,
                red_card=ms.red_card,
                fouls=ms.fouls,
                was_fouled=ms.was_fouled,
                dispossessed=ms.dispossessed,
                possession_lost=ms.possession_lost,
                saves=ms.saves,
                goals_conceded=ms.goals_conceded,
                shot_accuracy=ms.shot_accuracy,
                xg_per_shot=ms.xg_per_shot,
                finishing_delta=ms.finishing_delta,
                xa_delta=ms.xa_delta,
                pass_completion=ms.pass_completion,
                long_ball_accuracy=ms.long_ball_accuracy,
                cross_accuracy=ms.cross_accuracy,
                duel_win_rate=ms.duel_win_rate,
                aerial_win_rate=ms.aerial_win_rate,
                tackle_success_rate=ms.tackle_success_rate,
            )
        )

    # 4. Build SeasonStatsOut — override form stats with values derived from actual recent matches
    season_stats_out: SeasonStatsOut | None = None
    if merged:
        form = _compute_form_from_matches(recent_matches, merged.get("avg_rating"))
        merged.update(form)
        season_stats_out = SeasonStatsOut(**merged)

    # Ecusson — resolu sur le meme club que team_name ci-dessous
    # (current_team_name, sans le pret : le detail n'affiche pas le club de
    # pret contrairement au resume de liste).
    crest_map = await _team_crest_map(session, [player.current_team_api_id])
    api_football_id, crest_url = crest_map.get(player.current_team_api_id) or (None, None)

    # Bloc selection — agrege depuis player_selection_season_stats (toutes
    # competitions de selection confondues : Coupe du monde, Ligue des
    # Nations, qualifications...). None si le joueur n'a aucune selection :
    # la vue club ne doit jamais inventer de stats nationales.
    from app.models.canonical_teams import CanonicalTeam

    sel_rows = (await session.execute(
        select(PlayerSelectionSeasonStat).where(
            PlayerSelectionSeasonStat.player_api_id == player_api_id,
            PlayerSelectionSeasonStat.source == "bzzoiro",
        )
    )).scalars().all()
    selection_stats_out: SeasonStatsOut | None = None
    national_team_name: str | None = None
    if sel_rows:
        merged_sel = _merge_season_stats(sel_rows, sel_rows[0].season)
        if merged_sel:
            selection_stats_out = SeasonStatsOut(**merged_sel)
        team_id = sel_rows[0].national_team_api_id
        national_team_name = (await session.execute(
            select(CanonicalTeam.name_fr).where(CanonicalTeam.bzz_team_id == team_id)
        )).scalars().first()

    return {
        "player_api_id": player.api_id,
        "name": player.name,
        "short_name": player.short_name,
        "position": player.position,
        "date_of_birth": player.date_of_birth,
        "nationality": player.nationality,
        "height": player.height,
        "jersey_number": player.jersey_number,
        "market_value": player.market_value,
        "team_name": player.current_team_name,
        "api_football_id": api_football_id,
        "crest_url": crest_url,
        "season_stats": season_stats_out,
        "recent_matches": recent_matches,
        "selection_stats": selection_stats_out,
        "national_team_name": national_team_name,
    }


# ---------------------------------------------------------------------------
# GET /players/{player_api_id}/career  — full career history + Beta blended rhythm
# ---------------------------------------------------------------------------


_UNKNOWN_COMPETITION_LABEL = "Autre"

# Bzzoiro's `season` string is always "NNNN-NNNN" (see
# app.services.season_service), unlike Transfermarkt's "NN/NN"-style
# `season` display string — the two can never be compared as raw strings.
# `season_start_year` (an explicit int on both sides once parsed) is the
# only safe merge key across the two sources.
_BZZ_SEASON_RE = re.compile(r"^(\d{4})-(\d{4})$")


def _bzz_season_start_year(season: str) -> int | None:
    """Parse Bzzoiro's "NNNN-NNNN" season string into its start year.

    Defensive: an unparsable season string (never seen in practice, but
    never trusted blindly either) returns None rather than raising —
    consistent with how a missing/unparsable TM `season_start_year` is
    already handled (bucket sorts last, grouped by raw string instead).
    """
    match = _BZZ_SEASON_RE.match(season or "")
    return int(match.group(1)) if match else None


def _season_bucket_key(season_start_year: int | None, season: str) -> Any:
    """Grouping key for one season, across both sources.

    Known `season_start_year` is the key (so a Bzzoiro "NNNN-NNNN" season
    string and a Transfermarkt "NN/NN" display string for the same real
    season — e.g. start year 2025 on both sides — land in the same
    bucket). Falls back to the raw season string when the year truly isn't
    known on either side — same degraded behaviour as before Task 8.
    """
    return season_start_year if season_start_year is not None else ("raw", season)


def _competition_identity(
    *, league_api_id: int | None = None, competition_code: str | None = None
) -> tuple[str, Any]:
    """Stable identity for one (season, competition) merge bucket.

    Bzzoiro rows pass `league_api_id` directly — already the
    LEAGUE_REGISTRY/internal id space (see
    app.ingestion.competition_identity's module docstring). Transfermarkt
    rows pass `competition_code`, mapped onto that same id space via
    `tm_code_to_league_api_id` (Task 7) when the mapping is known, so a
    Bzzoiro row and a TM row for the same real competition resolve to the
    SAME key. An unmapped TM code (e.g. "EM", edition-ambiguous — see
    competition_identity.py) gets its own "tm" namespace: it never silently
    merges with an unrelated competition.
    """
    if league_api_id is not None:
        return ("league", league_api_id)
    mapped = tm_code_to_league_api_id(competition_code) if competition_code else None
    if mapped is not None:
        return ("league", mapped)
    return ("tm", competition_code)


def _build_career_seasons(
    tm_rows: list[PlayerCareerSeason],
    bzz_rows: list[BzzPlayerSeasonStat],
) -> list[CareerSeasonOut]:
    """Reconcile Bzzoiro (authority) and Transfermarkt (complement) career
    rows into one entry per (season, competition) — never two.

    Rule (Task 8): if a Bzzoiro row and a TM row land on the same
    (season, competition) key, the Bzzoiro values win; any Bzzoiro field
    that is NULL gets filled from the TM counterpart (`source="merged"`
    when that happens, `source="bzzoiro"` when Bzzoiro already had
    everything). A TM row with no Bzzoiro counterpart becomes a
    `source="transfermarkt"` entry — its xG/notes fields (Bzzoiro-only)
    stay None, never fabricated. A Bzzoiro row with no TM counterpart
    becomes a `source="bzzoiro"` entry.

    Sorted by season_start_year descending; seasons with no
    season_start_year sort last, never first (unchanged from before).
    """
    # key -> merged per-competition fields (not yet a CareerCompetitionOut:
    # built once per key is final after both passes).
    competitions: dict[tuple[Any, tuple[str, Any]], dict[str, Any]] = {}
    season_labels: dict[Any, str] = {}
    season_start_years: dict[Any, int | None] = {}

    # Pass 1 — seed every bucket from Bzzoiro (authority).
    for row in bzz_rows:
        season_start_year = _bzz_season_start_year(row.season)
        bucket = _season_bucket_key(season_start_year, row.season)
        season_labels.setdefault(bucket, row.season)
        season_start_years[bucket] = season_start_year

        identity = _competition_identity(league_api_id=row.league_api_id)
        key = (bucket, identity)
        league_meta = (
            LEAGUE_REGISTRY.get(row.league_api_id) if row.league_api_id is not None else None
        )
        competitions[key] = {
            "competition": league_meta.name if league_meta else None,
            "competition_code": None,
            "appearances": row.matches_played,
            "goals": row.goals,
            "assists": row.goal_assist,
            "minutes": row.minutes_played,
            "expected_goals": row.expected_goals,
            "avg_rating": row.avg_rating,
            "is_national": bool(league_meta and league_meta.tier == TIER_SELECTION),
            "source": "bzzoiro",
        }

    # Pass 2 — Transfermarkt fills NULL fields on an existing Bzzoiro bucket
    # (-> "merged"), or creates a TM-only entry when Bzzoiro has nothing here.
    for row in tm_rows:
        bucket = _season_bucket_key(row.season_start_year, row.season)
        season_labels.setdefault(bucket, row.season)
        season_start_years.setdefault(bucket, row.season_start_year)

        identity = _competition_identity(competition_code=row.competition_code)
        key = (bucket, identity)
        competition_label = row.competition
        if row.competition_code == "" and not competition_label:
            competition_label = _UNKNOWN_COMPETITION_LABEL

        existing = competitions.get(key)
        if existing is None:
            competitions[key] = {
                "competition": competition_label,
                "competition_code": row.competition_code,
                "appearances": row.appearances,
                "goals": row.goals,
                "assists": row.assists,
                "minutes": row.minutes,
                "expected_goals": None,
                "avg_rating": None,
                "is_national": row.is_national,
                "source": "transfermarkt",
            }
            continue

        filled = False
        for field, tm_value in (
            ("appearances", row.appearances),
            ("goals", row.goals),
            ("assists", row.assists),
            ("minutes", row.minutes),
        ):
            if existing.get(field) is None and tm_value is not None:
                existing[field] = tm_value
                filled = True
        if existing.get("competition") is None and competition_label:
            existing["competition"] = competition_label
            filled = True
        if existing.get("competition_code") is None and row.competition_code:
            existing["competition_code"] = row.competition_code
            filled = True
        if filled:
            existing["source"] = "merged"

    # Group the reconciled competition entries back into one row per season.
    seasons: dict[Any, dict[str, Any]] = {}
    for (bucket, _identity), entry in competitions.items():
        season_dict = seasons.setdefault(
            bucket,
            {
                "season": season_labels.get(bucket, ""),
                "season_start_year": season_start_years.get(bucket),
                "appearances": 0,
                "goals": 0,
                "assists": 0,
                "minutes": 0,
                "competitions": [],
            },
        )
        appearances = entry["appearances"] or 0
        goals = entry["goals"] or 0
        assists = entry["assists"] or 0
        minutes = entry["minutes"] or 0
        season_dict["appearances"] += appearances
        season_dict["goals"] += goals
        season_dict["assists"] += assists
        season_dict["minutes"] += minutes
        season_dict["competitions"].append(
            CareerCompetitionOut(
                competition=entry["competition"],
                competition_code=entry["competition_code"],
                appearances=appearances,
                goals=goals,
                assists=assists,
                minutes=minutes,
                source=entry["source"],
                is_national=entry["is_national"],
                expected_goals=entry["expected_goals"],
                avg_rating=entry["avg_rating"],
            )
        )

    out_seasons = [CareerSeasonOut(**season_dict) for season_dict in seasons.values()]
    out_seasons.sort(key=lambda s: (s.season_start_year is None, -(s.season_start_year or 0)))
    return out_seasons


@router.get("/{player_api_id}/career", response_model=PlayerCareerOut)
async def get_player_career(
    player_api_id: int,
    session: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Full career history (detail per season, toutes compétitions) + Beta blended rhythm.

    404 only when the player itself is unknown — consistent with GET
    /players/{player_api_id}. A player that exists but has no career rows
    (not yet backfilled, or entirely covered by Bzzoiro) is a normal state:
    career=[] and blended_rhythm=None, not a 404 — blended_rhythm() already
    returns None gracefully when there is no usable data.

    `career` is reconciled (Task 8) between Bzzoiro (bzz_player_season_stats,
    authority) and Transfermarkt (player_career_seasons, complement) — see
    `_build_career_seasons`. `blended_rhythm` below is computed separately
    from the database (not from this reconciled `career` list) and is
    untouched by Task 8.
    """
    player_result = await session.execute(
        select(BzzPlayer.api_id, BzzPlayer.name).where(BzzPlayer.api_id == player_api_id)
    )
    player_row = player_result.first()
    if player_row is None:
        raise HTTPException(status_code=404, detail="Player not found")

    career_rows = (await session.execute(
        select(PlayerCareerSeason).where(PlayerCareerSeason.player_api_id == player_api_id)
    )).scalars().all()

    bzz_season_rows = (await session.execute(
        select(BzzPlayerSeasonStat).where(BzzPlayerSeasonStat.player_api_id == player_api_id)
    )).scalars().all()

    career = _build_career_seasons(career_rows, bzz_season_rows)

    rhythm = await blended_rhythm(session, player_api_id)
    blended_rhythm_out = (
        BlendedRhythmOut(
            goal_rate_per_90=rhythm.goal_rate_per_90,
            assist_rate_per_90=rhythm.assist_rate_per_90,
            seasons_used=rhythm.seasons_used,
            has_career=rhythm.has_career,
        )
        if rhythm is not None
        else None
    )

    return {
        "player": {"player_api_id": player_row.api_id, "name": player_row.name},
        "career": career,
        "blended_rhythm": blended_rhythm_out,
    }
