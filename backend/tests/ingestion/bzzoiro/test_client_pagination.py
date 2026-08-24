"""get_all ne doit jamais tronquer en silence.

Le plafond max_pages a masque 78 % des joueurs sans le moindre signal : la
liste mondiale fait 2 349 pages alors que le plafond est a 500.
"""
from unittest.mock import AsyncMock

import pytest

from app.ingestion.bzzoiro.client import BzzoiroClient, PaginationTronqueeError


def _client(pages):
    c = BzzoiroClient.__new__(BzzoiroClient)
    c.get_page = AsyncMock(side_effect=pages)
    return c


async def test_pagination_complete():
    c = _client([
        {"count": 3, "results": [1, 2], "next": "http://x/api/p/?page=2"},
        {"count": 3, "results": [3], "next": None},
    ])
    assert await c.get_all("/api/p/") == [1, 2, 3]


async def test_troncature_par_le_plafond_leve_une_erreur():
    pages = [{"count": 100, "results": [i], "next": "http://x/api/p/?page=9"} for i in range(3)]
    c = _client(pages)
    with pytest.raises(PaginationTronqueeError) as exc:
        await c.get_all("/api/p/", max_pages=3)
    assert "3" in str(exc.value)


async def test_total_incoherent_leve_une_erreur():
    """L'API annonce 10 lignes, on n'en recoit que 2 : on ne s'en contente pas."""
    c = _client([{"count": 10, "results": [1, 2], "next": None}])
    with pytest.raises(PaginationTronqueeError) as exc:
        await c.get_all("/api/p/")
    assert "10" in str(exc.value) and "2" in str(exc.value)


async def test_absence_de_total_ne_bloque_pas():
    """Certains points d'acces ne rendent pas de champ count."""
    c = _client([{"results": [1, 2], "next": None}])
    assert await c.get_all("/api/p/") == [1, 2]
