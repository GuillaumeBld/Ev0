import pytest
from app.ingestion.wc2026.formations import (
    FORMATIONS,
    parse_formation,
    validate_lineup_formation,
    default_minutes_for_role,
)


def test_all_formations_sum_to_10():
    for name, lines in FORMATIONS.items():
        assert sum(lines) == 10, f"{name}: sum={sum(lines)}, expected 10"


def test_parse_formation_433():
    assert parse_formation("4-3-3") == [4, 3, 3]


def test_parse_formation_4231():
    assert parse_formation("4-2-3-1") == [4, 2, 3, 1]


def test_parse_formation_352():
    assert parse_formation("3-5-2") == [3, 5, 2]


def test_parse_formation_unknown_raises():
    with pytest.raises(ValueError, match="unknown formation"):
        parse_formation("9-1-0")


def test_validate_ok():
    players = [{"line_index": 0, "slot_index": 0}]  # GK
    for li, count in enumerate([4, 3, 3], start=1):
        for si in range(count):
            players.append({"line_index": li, "slot_index": si})
    validate_lineup_formation("4-3-3", players)  # must not raise


def test_validate_wrong_count_raises():
    players = [{"line_index": 0, "slot_index": 0}]  # GK only
    with pytest.raises(ValueError, match="expected 10 outfield"):
        validate_lineup_formation("4-3-3", players)


def test_default_minutes_starter():
    assert default_minutes_for_role("starter") == 85


def test_default_minutes_sub_planned():
    assert default_minutes_for_role("sub_planned") == 30


def test_default_minutes_sub_tactical():
    assert default_minutes_for_role("sub_tactical") == 12


def test_default_minutes_reserve():
    assert default_minutes_for_role("reserve") == 0
