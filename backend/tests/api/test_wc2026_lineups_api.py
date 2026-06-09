"""Tests for WC2026 lineups API — pure logic, no DB."""
from app.api.wc2026_lineups import (
    _context_valid,
    _role_to_minutes,
    LineupPlayerIn,
    LineupUpsertIn,
)


def test_context_valid_default():
    assert _context_valid("default") is True


def test_context_valid_matchday():
    assert _context_valid("matchday_1") is True
    assert _context_valid("matchday_3") is True


def test_context_valid_rounds():
    for ctx in ("r16", "qf", "sf", "final"):
        assert _context_valid(ctx) is True


def test_context_invalid():
    assert _context_valid("matchday_5") is False
    assert _context_valid("random") is False


def test_role_to_minutes_defaults():
    assert _role_to_minutes("starter") == 85
    assert _role_to_minutes("sub_planned") == 30
    assert _role_to_minutes("sub_tactical") == 12
    assert _role_to_minutes("reserve") == 0


def test_lineup_player_in_schema():
    p = LineupPlayerIn(
        player_name="Mbappé",
        position="FWD",
        line_index=3,
        slot_index=1,
        is_starter=True,
        role="starter",
        expected_minutes=85,
    )
    assert p.player_name == "Mbappé"


def test_lineup_upsert_in_schema():
    req = LineupUpsertIn(
        formation="4-3-3",
        players=[
            LineupPlayerIn(
                player_name="Maignan",
                position="GK",
                line_index=0,
                slot_index=0,
                is_starter=True,
                role="starter",
                expected_minutes=90,
            )
        ],
    )
    assert req.formation == "4-3-3"
