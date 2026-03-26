import importlib.util
import sys
from pathlib import Path
import pytest
from unittest.mock import MagicMock

# Import lineup_resolver directly (bypass app/ingestion/__init__.py which
# requires asyncpg/DB not available in the local venv).
_spec = importlib.util.spec_from_file_location(
    "app.ingestion.lineup_resolver",
    Path(__file__).parent.parent / "app" / "ingestion" / "lineup_resolver.py",
)
_mod = importlib.util.module_from_spec(_spec)
sys.modules["app.ingestion.lineup_resolver"] = _mod
_spec.loader.exec_module(_mod)

resolve_lineup = _mod.resolve_lineup
ResolvedLineup = _mod.ResolvedLineup
PRIORITY = _mod.PRIORITY


def _make_lineup(lineup_type: str, players=None):
    lu = MagicMock()
    lu.lineup_type = lineup_type
    lu.players = players or []
    lu.id = 1
    return lu


def test_priority_order():
    assert PRIORITY["official"] < PRIORITY["probable_manual"] < PRIORITY["last_known"]


@pytest.mark.asyncio
async def test_resolve_official_wins_over_manual():
    """official bat probable_manual."""
    official = _make_lineup("official")
    manual = _make_lineup("probable_manual")
    result = await resolve_lineup(
        fixture_id=1, team="psg", session=None,
        _overrides=[manual, official],
    )
    assert result is not None
    assert result.lineup_type == "official"


@pytest.mark.asyncio
async def test_resolve_manual_wins_over_last_known():
    """probable_manual bat last_known."""
    manual = _make_lineup("probable_manual")
    last = _make_lineup("last_known")
    result = await resolve_lineup(
        fixture_id=1, team="psg", session=None,
        _overrides=[last, manual],
    )
    assert result.lineup_type == "probable_manual"


@pytest.mark.asyncio
async def test_resolve_returns_none_when_empty():
    result = await resolve_lineup(
        fixture_id=1, team="xyz", session=None,
        _overrides=[],
    )
    assert result is None
