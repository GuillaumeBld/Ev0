"""recent_matches doit couvrir club + coupes + selection, pas seulement les
6 championnats domestiques — et exposer league_api_id pour que le front
affiche la competition de chaque match du journal.

Avant ce fix, le filtre d'events de `get_player` ne retenait que
[1,3,4,5,6,7] (5 championnats + C1) et `RecentMatch` ne portait pas
league_api_id : un match de Coupe du monde ou d'Europa League disparaissait
du journal, et le front ne pouvait rien afficher comme etiquette de
competition meme pour les matchs retenus.
"""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.models.bzzoiro import BzzPlayer, BzzPlayerMatchStat, BzzPlayerSeasonStat


def _player(api_id: int = 1, name: str = "Kylian Mbappe") -> BzzPlayer:
    return BzzPlayer(
        api_id=api_id, internal_id=api_id, name=name,
        short_name=name.split()[-1], position="F",
        nationality="French", height=180, jersey_number=10,
        market_value=50_000_000, current_team_api_id=100,
        current_team_name="Real Madrid",
    )


def _season_stat(player_api_id: int = 1) -> BzzPlayerSeasonStat:
    return BzzPlayerSeasonStat(
        player_api_id=player_api_id, season="2025-2026", league_api_id=42,
        matches_played=20, minutes_played=1800, starts=18,
        goals=8, goal_assist=4, total_shots=50, shots_on_target=25,
        key_pass=30, expected_goals=6.5, expected_assists=3.2,
        avg_rating=7.4,
        form_xg_5=1.8, form_rating_5=7.6, form_goals_5=3, form_assists_5=1,
        rating_trend=0.1,
    )


def _match_stat(event_api_id: int, team_api_id: int = 100) -> BzzPlayerMatchStat:
    return BzzPlayerMatchStat(
        player_api_id=1, event_api_id=event_api_id, team_api_id=team_api_id,
        is_home=True, minutes_played=90, rating=7.5, touches=40,
        goals=0, goal_assist=1, expected_goals=0.2, expected_assists=0.3,
        total_shots=1, shots_on_target=1, total_pass=30, accurate_pass=25,
        key_pass=1, total_long_balls=1, accurate_long_balls=1,
        total_cross=0, accurate_cross=0, duel_won=2, duel_lost=1,
        aerial_won=1, aerial_lost=0, total_tackle=1, won_tackle=1,
        total_clearance=0, interception=0, ball_recovery=2,
        yellow_card=0, red_card=0, fouls=0, was_fouled=1,
        dispossessed=0, possession_lost=1, saves=0, goals_conceded=0,
        shot_accuracy=1.0, xg_per_shot=0.2, finishing_delta=-0.2,
        xa_delta=0.7, pass_completion=0.833, long_ball_accuracy=1.0,
        cross_accuracy=0.0, duel_win_rate=0.67, aerial_win_rate=1.0,
        tackle_success_rate=1.0,
    )


@pytest.mark.asyncio
async def test_recent_matches_includes_competition_and_intl():
    """Un match de C1 (league_api_id=7) et un de Coupe du monde
    (league_api_id=27) apparaissent tous les deux dans recent_matches, chacun
    avec son league_api_id renseigne.
    """
    from app.api.players import get_player

    player = _player()
    season_stat = _season_stat()

    ms_ucl = _match_stat(event_api_id=200)
    ms_wc = _match_stat(event_api_id=201)

    dt_ucl = datetime(2025, 11, 4, 20, 0, tzinfo=UTC)
    dt_wc = datetime(2025, 6, 15, 18, 0, tzinfo=UTC)

    player_result = MagicMock()
    player_result.scalars.return_value.first.return_value = player

    season_result = MagicMock()
    season_result.scalars.return_value.all.return_value = [season_stat]

    recent_result = MagicMock()
    recent_result.all.return_value = [
        # (ms, event_date, home_api_id, away_api_id, home_name, away_name, league_api_id)
        (ms_ucl, dt_ucl, 100, 300, "Real Madrid", "Manchester City", 7),
        (ms_wc, dt_wc, 100, 400, "France", "Argentina", 27),
    ]
    crest_result = MagicMock()
    crest_result.all.return_value = []

    sel_result = MagicMock()
    sel_result.scalars.return_value.all.return_value = []

    mock_db = AsyncMock()
    mock_db.execute = AsyncMock(
        side_effect=[player_result, season_result, recent_result, crest_result, sel_result]
    )

    response = await get_player(player_api_id=1, session=mock_db, season="2025-2026")

    recent = response["recent_matches"]
    assert len(recent) == 2

    by_event = {m.event_api_id: m for m in recent}

    ucl_match = by_event[200]
    assert ucl_match.league_api_id == 7

    wc_match = by_event[201]
    assert wc_match.league_api_id == 27
