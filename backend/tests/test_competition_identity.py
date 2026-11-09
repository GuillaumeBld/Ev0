"""Tests for the Bzzoiro <-> Transfermarkt competition identity map.

This map lets Task 8 (reconciliation) know that a Bzzoiro row with
`league_api_id=6` and a Transfermarkt row with `competition_code="FR1"`
designate the SAME real-world competition (Ligue 1), without confusing the
two distinct id spaces involved.
"""
from __future__ import annotations

from app.ingestion.bzzoiro.league_registry import LEAGUE_REGISTRY
from app.ingestion.competition_identity import (
    TM_CODE_TO_LEAGUE,
    same_competition,
    tm_code_to_league_api_id,
)


def test_ligue1_both_tm_codes_map_to_bzzoiro_6():
    assert tm_code_to_league_api_id("FR1") == 6
    assert tm_code_to_league_api_id("L1") == 6


def test_same_competition_true_for_ligue1():
    assert same_competition(6, "FR1") is True
    assert same_competition(6, "L1") is True


def test_same_competition_false_when_competition_differs():
    assert same_competition(6, "CL") is False


def test_champions_league_code_maps_to_7():
    assert tm_code_to_league_api_id("CL") == 7


def test_europa_and_conference_league_codes():
    assert tm_code_to_league_api_id("EL") == 8
    assert tm_code_to_league_api_id("ECL") == 83


def test_big5_domestic_leagues():
    assert tm_code_to_league_api_id("GB1") == 1
    assert tm_code_to_league_api_id("ES1") == 3
    assert tm_code_to_league_api_id("IT1") == 4


def test_unknown_code_returns_none_never_a_guess():
    assert tm_code_to_league_api_id("ZZZ") is None


def test_same_competition_false_for_unknown_code():
    assert same_competition(6, "ZZZ") is False


def test_edition_specific_world_cup_code_is_not_mapped():
    # TM's "WM" covers every World Cup edition a player appeared in, while
    # the registry's id 27 is specifically "Coupe du monde 2026" (one
    # edition). They are not the same competition identity, so no mapping.
    assert tm_code_to_league_api_id("WM") is None


def test_every_mapped_value_is_a_real_bzzoiro_internal_id():
    # Hard constraint: the dict's values must live in the Bzzoiro internal
    # id space (LEAGUE_REGISTRY keys), never some other id space.
    for code, league_id in TM_CODE_TO_LEAGUE.items():
        assert league_id in LEAGUE_REGISTRY, (
            f"TM code {code!r} maps to {league_id!r}, which is not a key of "
            "LEAGUE_REGISTRY (Bzzoiro internal id space)"
        )
