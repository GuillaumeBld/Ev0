import pytest
from unittest.mock import AsyncMock, MagicMock
from app.services.sub_stats import get_player_sub_stats, SubStats

@pytest.mark.asyncio
async def test_returns_sub_stats_from_rows():
    """3 sorties sur 4 matchs → p_sub=0.75, avg=(60+55+70)/3."""
    mock_db = AsyncMock()
    mock_rows = [
        MagicMock(minutes_played=60),
        MagicMock(minutes_played=55),
        MagicMock(minutes_played=70),
        MagicMock(minutes_played=90),
    ]
    mock_result = MagicMock()
    mock_result.fetchall.return_value = mock_rows
    mock_db.execute = AsyncMock(return_value=mock_result)

    stats = await get_player_sub_stats("Mbappé", "FW", mock_db, n_matches=20)

    assert stats.p_sub == pytest.approx(0.75, abs=0.01)
    assert stats.avg_sub_time == pytest.approx(61.67, abs=0.1)

@pytest.mark.asyncio
async def test_fallback_when_no_data():
    """Aucune donnée → retourne les defaults de position."""
    from app.pricing.sub_constants import P_SUB_DEFAULT, T_SUB_DEFAULT
    mock_db = AsyncMock()
    mock_result = MagicMock()
    mock_result.fetchall.return_value = []
    mock_db.execute = AsyncMock(return_value=mock_result)

    stats = await get_player_sub_stats("Inconnu", "MF", mock_db, n_matches=20)

    assert stats.p_sub == P_SUB_DEFAULT["MF"]
    assert stats.avg_sub_time == T_SUB_DEFAULT
