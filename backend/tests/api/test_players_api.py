"""Tests for the Bzzoiro-powered players API endpoints."""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException


def _make_player(api_id: int = 1, name: str = "Player One", position: str = "F") -> MagicMock:
    p = MagicMock()
    p.api_id = api_id
    p.name = name
    p.short_name = name.split()[-1]
    p.position = position
    p.nationality = "French"
    p.date_of_birth = None
    p.height = 180
    p.jersey_number = 10
    p.market_value = 50_000_000
    p.current_team_api_id = 100
    return p


def _make_season_stat(player_api_id: int = 1) -> MagicMock:
    s = MagicMock()
    s.player_api_id = player_api_id
    s.season = "2025-2026"
    s.league_api_id = 42
    s.matches_played = 20
    s.minutes_played = 1800
    s.starts = 18
    s.goals = 8
    s.goal_assist = 4
    s.total_shots = 50
    s.shots_on_target = 25
    s.key_pass = 30
    s.expected_goals = 6.5
    s.expected_assists = 3.2
    s.xg_per_90 = 0.33
    s.xa_per_90 = 0.16
    s.shots_per_90 = 2.5
    s.shots_on_target_per_90 = 1.25
    s.key_pass_per_90 = 1.5
    s.avg_rating = 7.4
    s.form_xg_5 = 1.8
    s.form_rating_5 = 7.6
    s.form_goals_5 = 3
    s.form_assists_5 = 1
    s.rating_trend = 0.1
    s.shot_accuracy = 0.5
    s.xg_per_shot = 0.13
    s.finishing_delta = 0.05
    s.xa_delta = 0.02
    s.pass_completion = 0.82
    s.duel_win_rate = 0.55
    s.aerial_win_rate = 0.4
    s.tackle_success_rate = 0.6
    s.avg_minutes_per_match = 90.0
    s.starts_pct = 0.9
    return s


def _make_match_stat(
    player_api_id: int = 1,
    event_api_id: int = 456,
    is_home: bool = True,
    team_api_id: int = 100,
) -> MagicMock:
    ms = MagicMock()
    ms.player_api_id = player_api_id
    ms.event_api_id = event_api_id
    ms.team_api_id = team_api_id
    ms.is_home = is_home
    ms.minutes_played = 90
    ms.rating = 8.2
    ms.touches = 55
    ms.goals = 1
    ms.goal_assist = 0
    ms.expected_goals = 0.73
    ms.expected_assists = 0.15
    ms.total_shots = 4
    ms.shots_on_target = 3
    ms.total_pass = 42
    ms.accurate_pass = 36
    ms.key_pass = 2
    ms.total_long_balls = 3
    ms.accurate_long_balls = 2
    ms.total_cross = 1
    ms.accurate_cross = 0
    ms.duel_won = 5
    ms.duel_lost = 3
    ms.aerial_won = 2
    ms.aerial_lost = 1
    ms.total_tackle = 4
    ms.won_tackle = 3
    ms.total_clearance = 0
    ms.interception = 1
    ms.ball_recovery = 4
    ms.yellow_card = 0
    ms.red_card = 0
    ms.fouls = 1
    ms.was_fouled = 2
    ms.dispossessed = 1
    ms.possession_lost = 3
    ms.saves = 0
    ms.goals_conceded = 0
    ms.shot_accuracy = 0.75
    ms.xg_per_shot = 0.18
    ms.finishing_delta = 0.05
    ms.xa_delta = 0.02
    ms.pass_completion = 0.857
    ms.long_ball_accuracy = 0.67
    ms.cross_accuracy = 0.0
    ms.duel_win_rate = 0.625
    ms.aerial_win_rate = 0.67
    ms.tackle_success_rate = 0.75
    return ms




# ---------------------------------------------------------------------------
# Helpers sur modèles réels — l'API actuelle utilise scalars().all()/first()
# et recalcule les per-90 depuis les totaux ; les mocks historiques ne
# correspondent plus à ces formes.
# ---------------------------------------------------------------------------

from app.models.bzzoiro import BzzPlayer, BzzPlayerMatchStat, BzzPlayerSeasonStat


def _player(api_id: int = 1, name: str = "Player One", position: str = "F",
            team: str = "Real Madrid") -> BzzPlayer:
    return BzzPlayer(
        api_id=api_id, internal_id=api_id, name=name,
        short_name=name.split()[-1], position=position,
        nationality="French", height=180, jersey_number=10,
        market_value=50_000_000, current_team_api_id=100,
        current_team_name=team,
    )


def _season_stat(player_api_id: int = 1, expected_goals: float = 6.5,
                 avg_rating: float = 7.4) -> BzzPlayerSeasonStat:
    return BzzPlayerSeasonStat(
        player_api_id=player_api_id, season="2025-2026", league_api_id=42,
        matches_played=20, minutes_played=1800, starts=18,
        goals=8, goal_assist=4, total_shots=50, shots_on_target=25,
        key_pass=30, expected_goals=expected_goals, expected_assists=3.2,
        avg_rating=avg_rating,
        form_xg_5=1.8, form_rating_5=7.6, form_goals_5=3, form_assists_5=1,
        rating_trend=0.1,
    )


def _match_stat(event_api_id: int = 456, team_api_id: int = 100) -> BzzPlayerMatchStat:
    return BzzPlayerMatchStat(
        player_api_id=1, event_api_id=event_api_id, team_api_id=team_api_id,
        is_home=True, minutes_played=90, rating=8.2, touches=55,
        goals=1, goal_assist=0, expected_goals=0.73, expected_assists=0.15,
        total_shots=4, shots_on_target=3, total_pass=42, accurate_pass=36,
        key_pass=2, total_long_balls=3, accurate_long_balls=2,
        total_cross=1, accurate_cross=0, duel_won=5, duel_lost=3,
        aerial_won=2, aerial_lost=1, total_tackle=4, won_tackle=3,
        total_clearance=0, interception=1, ball_recovery=4,
        yellow_card=0, red_card=0, fouls=1, was_fouled=2,
        dispossessed=1, possession_lost=3, saves=0, goals_conceded=0,
        shot_accuracy=0.75, xg_per_shot=0.18, finishing_delta=0.05,
        xa_delta=0.02, pass_completion=0.857, long_ball_accuracy=0.67,
        cross_accuracy=0.0, duel_win_rate=0.625, aerial_win_rate=0.67,
        tackle_success_rate=0.75,
    )

# ---------------------------------------------------------------------------
# Test: list players — empty result
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_players_empty():
    from app.api.players import list_players

    mock_db = AsyncMock()
    result = MagicMock()
    result.all.return_value = []
    mock_db.execute = AsyncMock(return_value=result)

    response = await list_players(
        session=mock_db,
        league_api_id=None,
        team_api_id=None,
        position=None,
        min_minutes=0,
        season="2025-2026",
        sort_by="xg_per_90",
        sort_order="desc",
        limit=50,
        offset=0,
    )
    assert response == []


# ---------------------------------------------------------------------------
# Test: list players — 2 players with season stats
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_players_basic():
    from app.api.players import list_players

    p1 = _player(api_id=1, name="Alpha Player", team="Real Madrid")
    p2 = _player(api_id=2, name="Beta Player", position="M", team="Barcelona")
    s1 = _season_stat(player_api_id=1)                      # xg/90 = 6.5/20 = 0.325
    s2 = _season_stat(player_api_id=2, expected_goals=3.6)  # xg/90 = 0.18

    players_res = MagicMock()
    players_res.scalars.return_value.all.return_value = [p1, p2]
    stats_res = MagicMock()
    stats_res.scalars.return_value.all.return_value = [s1, s2]
    mock_db = AsyncMock()
    mock_db.execute = AsyncMock(side_effect=[players_res, stats_res])

    response = await list_players(
        session=mock_db,
        league_api_id=None,
        team_api_id=None,
        position=None,
        min_minutes=0,
        season="2025-2026",
        sort_by="xg_per_90",
        sort_order="desc",
        limit=50,
        offset=0,
    )

    assert len(response) == 2

    item0 = response[0]
    assert item0["player_api_id"] == 1
    assert item0["name"] == "Alpha Player"
    assert item0["position"] == "F"
    assert item0["team_name"] == "Real Madrid"
    assert item0["nationality"] == "French"
    assert item0["xg_per_90"] == pytest.approx(0.325)
    assert item0["matches_played"] == 20
    assert item0["minutes_played"] == 1800
    assert item0["season"] == "2025-2026"

    item1 = response[1]
    assert item1["player_api_id"] == 2
    assert item1["xg_per_90"] == pytest.approx(0.18)


# ---------------------------------------------------------------------------
# Test: get player — 404 when not found
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_player_not_found():
    from app.api.players import get_player

    mock_db = AsyncMock()
    result = MagicMock()
    result.scalars.return_value.first.return_value = None
    mock_db.execute = AsyncMock(return_value=result)

    with pytest.raises(HTTPException) as exc_info:
        await get_player(player_api_id=999, session=mock_db, season="2025-2026")

    assert exc_info.value.status_code == 404
    assert exc_info.value.detail == "Player not found"


# ---------------------------------------------------------------------------
# Test: get player — full detail
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_player_detail():
    from app.api.players import get_player

    player = _player(api_id=1, name="Kylian Mbappe")
    season_stat = _season_stat(player_api_id=1)

    ms1 = _match_stat(event_api_id=100)
    ms2 = _match_stat(event_api_id=101)

    dt1 = datetime(2025, 12, 15, 20, 45, tzinfo=UTC)
    dt2 = datetime(2025, 12, 8, 18, 0, tzinfo=UTC)

    # Trois execute : joueur (scalars.first), stats saison (scalars.all), matchs (all)
    player_result = MagicMock()
    player_result.scalars.return_value.first.return_value = player

    season_result = MagicMock()
    season_result.scalars.return_value.all.return_value = [season_stat]

    recent_result = MagicMock()
    recent_result.all.return_value = [
        (ms1, dt1, 100, 200, "Real Madrid", "Barcelona"),
        (ms2, dt2, 200, 100, "Atletico", "Real Madrid"),
    ]

    mock_db = AsyncMock()
    mock_db.execute = AsyncMock(
        side_effect=[player_result, season_result, recent_result]
    )

    response = await get_player(player_api_id=1, session=mock_db, season="2025-2026")

    assert response["player_api_id"] == 1
    assert response["name"] == "Kylian Mbappe"
    assert response["team_name"] == "Real Madrid"
    assert response["nationality"] == "French"
    assert response["height"] == 180

    # Season stats — per-90 recalculés depuis les totaux, forme depuis les matchs récents
    ss = response["season_stats"]
    assert ss is not None
    assert ss.xg_per_90 == pytest.approx(6.5 / 20)
    assert ss.matches_played == 20
    assert ss.form_xg_5 == pytest.approx(0.73 * 2)
    assert ss.form_goals_5 == 2
    assert ss.rating_trend == pytest.approx(8.2 - 7.4)

    # Recent matches
    recent = response["recent_matches"]
    assert len(recent) == 2

    m0 = recent[0]
    assert m0.event_api_id == 100
    assert m0.event_date == dt1
    assert m0.is_home is True
    assert m0.opponent == "Barcelona"
    assert m0.goals == 1
    assert m0.expected_goals == 0.73
    assert m0.rating == 8.2
    assert m0.shots_on_target == 3

    m1 = recent[1]
    assert m1.event_api_id == 101
    assert m1.is_home is False
    assert m1.opponent == "Atletico"


@pytest.mark.asyncio
async def test_get_player_detail_full_match_stats():
    """Recent match entries expose all BzzPlayerMatchStat fields."""
    from app.api.players import get_player

    player = _player(api_id=1, name="Test Player", team="Arsenal")
    season_stat = _season_stat(player_api_id=1)
    ms = _match_stat(event_api_id=777)

    dt = datetime(2026, 1, 10, 20, 0, tzinfo=UTC)

    player_result = MagicMock()
    player_result.scalars.return_value.first.return_value = player

    season_result = MagicMock()
    season_result.scalars.return_value.all.return_value = [season_stat]

    recent_result = MagicMock()
    recent_result.all.return_value = [
        (ms, dt, 100, 200, "Arsenal", "Chelsea"),
    ]

    mock_db = AsyncMock()
    mock_db.execute = AsyncMock(
        side_effect=[player_result, season_result, recent_result]
    )

    response = await get_player(player_api_id=1, session=mock_db, season="2025-2026")

    m = response["recent_matches"][0]
    assert m.event_api_id == 777
    assert m.touches == 55
    assert m.total_shots == 4
    assert m.total_pass == 42
    assert m.accurate_pass == 36
    assert m.duel_won == 5
    assert m.duel_lost == 3
    assert m.aerial_won == 2
    assert m.won_tackle == 3
    assert m.interception == 1
    assert m.ball_recovery == 4
    assert m.yellow_card == 0
    assert m.fouls == 1
    assert m.shot_accuracy == pytest.approx(0.75)
    assert m.xg_per_shot == pytest.approx(0.18)
    assert m.finishing_delta == pytest.approx(0.05)
    assert m.xa_delta == pytest.approx(0.02)
    assert m.pass_completion == pytest.approx(0.857)
    assert m.long_ball_accuracy == pytest.approx(0.67)
    assert m.cross_accuracy == pytest.approx(0.0)
    assert m.duel_win_rate == pytest.approx(0.625)
    assert m.aerial_win_rate == pytest.approx(0.67)
    assert m.tackle_success_rate == pytest.approx(0.75)


# ---------------------------------------------------------------------------
# Test: list_leagues returns target leagues
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_list_player_leagues():
    from app.api.players import list_player_leagues

    result = MagicMock()
    result.all.return_value = [(25, "Premier League"), (21, "Ligue 1")]

    mock_db = AsyncMock()
    mock_db.execute = AsyncMock(return_value=result)

    response = await list_player_leagues(session=mock_db)
    assert len(response) == 2
    assert response[0] == {"api_id": 25, "name": "Premier League"}
    assert response[1] == {"api_id": 21, "name": "Ligue 1"}


# ---------------------------------------------------------------------------
# Test: list_teams — all teams when no league filter
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_list_player_teams_no_filter(monkeypatch):
    """Sans championnat demande : toutes les equipes, via la requete derivee."""
    from app.api import players as players_mod

    async def fake_current_season(session):
        return "2025-2026"

    monkeypatch.setattr(players_mod, "current_season", fake_current_season)

    result = MagicMock()
    result.all.return_value = [(100, "Arsenal"), (200, "Chelsea")]
    mock_db = AsyncMock()
    mock_db.execute = AsyncMock(return_value=result)

    response = await players_mod.list_player_teams(session=mock_db, league_api_id=None, season=None)
    assert len(response) == 2
    assert response[0] == {"api_id": 100, "name": "Arsenal"}
    assert response[1] == {"api_id": 200, "name": "Chelsea"}
    # La saison résolue est bindée en paramètre de la CTE (plus de littéral en dur)
    assert mock_db.execute.call_args.args[1] == {"season": "2025-2026"}


# ---------------------------------------------------------------------------
# Test: list_teams — filtered by league (depuis le referentiel)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_list_player_teams_with_league(monkeypatch):
    """Championnat demande : la liste vient du referentiel, resolue par identifiant.

    C'est ce qui garantit les 18 ou 20 clubs — un club sans aucun joueur en
    base doit tout de meme apparaitre dans son championnat.
    """
    from app.api import players as players_mod

    vus = []

    async def fake_ids(session, league_api_id, season):
        vus.append((league_api_id, season))
        return [63, 77]

    async def fake_noms(session, ids):
        return [{"api_id": 63, "name": "Milan"}, {"api_id": 77, "name": "Inter Milan"}]

    monkeypatch.setattr(players_mod, "team_ids_for_league", fake_ids)
    monkeypatch.setattr(players_mod, "_nommer_clubs", fake_noms)

    response = await players_mod.list_player_teams(
        session=AsyncMock(), league_api_id=4, season="2026-2027"
    )
    assert response == [
        {"api_id": 63, "name": "Milan"},
        {"api_id": 77, "name": "Inter Milan"},
    ]
    assert vus == [(4, "2026-2027")]


@pytest.mark.asyncio
async def test_list_player_teams_league_sans_club(monkeypatch):
    """Aucun club resolu : liste vide, jamais la CTE derivee des noms."""
    from app.api import players as players_mod

    async def fake_ids(session, league_api_id, season):
        return []

    monkeypatch.setattr(players_mod, "team_ids_for_league", fake_ids)

    assert await players_mod.list_player_teams(
        session=AsyncMock(), league_api_id=99, season="2026-2027"
    ) == []


# ---------------------------------------------------------------------------
# Test: list_players — sort_by extended column (avg_rating)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_list_players_sort_by_avg_rating():
    from app.api.players import list_players

    p1 = _player(api_id=1, name="Alpha Player")
    p2 = _player(api_id=2, name="Beta Player")
    s1 = _season_stat(player_api_id=1, avg_rating=7.4)
    s2 = _season_stat(player_api_id=2, avg_rating=6.9)

    players_res = MagicMock()
    players_res.scalars.return_value.all.return_value = [p1, p2]
    stats_res = MagicMock()
    stats_res.scalars.return_value.all.return_value = [s1, s2]
    mock_db = AsyncMock()
    mock_db.execute = AsyncMock(side_effect=[players_res, stats_res])

    response = await list_players(
        session=mock_db,
        league_api_id=None,
        team_api_id=None,
        position=None,
        min_minutes=0,
        season="2025-2026",
        sort_by="avg_rating",
        sort_order="asc",
        limit=50,
        offset=0,
    )
    assert [r["player_api_id"] for r in response] == [2, 1]
