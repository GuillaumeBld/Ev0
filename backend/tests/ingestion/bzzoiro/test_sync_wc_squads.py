"""Tests de robustesse de sync_wc_squads face aux 404 upstream (issue #16)."""
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from app.ingestion.bzzoiro.sync_wc_squads import sync_wc_squads


def _http_404(url: str) -> httpx.HTTPStatusError:
    request = httpx.Request("GET", url)
    response = httpx.Response(404, request=request)
    return httpx.HTTPStatusError("404 Not Found", request=request, response=response)


def _make_session() -> AsyncMock:
    session = AsyncMock()
    session.execute = AsyncMock()
    session.commit = AsyncMock()
    return session


@pytest.mark.asyncio
async def test_404_on_one_squad_does_not_abort_job():
    """Un 404 sur un effectif est ignoré ; les autres équipes sont traitées."""
    # Liste : 3 équipes sans joueurs embarqués → chacune nécessite un détail
    squads_list = [
        {"team": {"name": "France", "id": 156}},
        {"team": {"name": "Fantôme", "id": 1152}},   # 404 upstream
        {"team": {"name": "Spain", "id": 1429}},
    ]

    async def fake_get_page(path, params=None):
        if "/1152/" in path:
            raise _http_404(f"https://sports.bzzoiro.com{path}")
        team = "France" if "/156/" in path else "Spain"
        return {"players": [{"name": f"Joueur {team}", "position": "F"}]}

    client = MagicMock()
    client.get_all = AsyncMock(return_value=squads_list)
    client.get_page = AsyncMock(side_effect=fake_get_page)

    session = _make_session()
    total = await sync_wc_squads(session, client)

    # France + Spain traités (1 joueur chacun), Fantôme ignoré → 2 upserts
    assert total == 2
    assert client.get_page.await_count == 3  # les 3 détails ont été tentés
    session.commit.assert_awaited()  # le job va au bout et commit


@pytest.mark.asyncio
async def test_generic_network_error_is_skipped_too():
    squads_list = [
        {"team": {"name": "Timeout FC", "id": 999}},
        {"team": {"name": "Spain", "id": 1429}},
    ]

    async def fake_get_page(path, params=None):
        if "/999/" in path:
            raise httpx.ConnectTimeout("timeout")
        return {"players": [{"name": "Joueur Spain", "position": "F"}]}

    client = MagicMock()
    client.get_all = AsyncMock(return_value=squads_list)
    client.get_page = AsyncMock(side_effect=fake_get_page)

    total = await sync_wc_squads(_make_session(), client)
    assert total == 1  # seul Spain a réussi


@pytest.mark.asyncio
async def test_embedded_players_need_no_detail_fetch():
    """Si les joueurs sont déjà embarqués, aucun appel de détail."""
    squads_list = [{"team": {"name": "France", "id": 156},
                    "players": [{"name": "Mbappé", "position": "F"}]}]
    client = MagicMock()
    client.get_all = AsyncMock(return_value=squads_list)
    client.get_page = AsyncMock()

    total = await sync_wc_squads(_make_session(), client)
    assert total == 1
    client.get_page.assert_not_awaited()
