"""Tests for the league registry (LEAGUE_REGISTRY, LeagueMeta).

Task 1 of the ~57-competition expansion. The registry is the single source
of truth for every competition we track, keyed by the Bzzoiro internal id
(espace A) — the same id space already used by EFFECTIFS_REGLEMENTAIRES,
TARGET_LEAGUE_INTERNAL_IDS and CanonicalTeam.league_api_id elsewhere in this
codebase. Three structural invariants matter more than any single value:

(a) all 57 ids from the validated registry data are present ;
(b) every LeagueMeta is correctly typed ;
(c) expected_count is not None if and only if format == "round_robin" —
    this is the invariant Task 2's strict segmentation guard depends on ;
(d) country is None for the UEFA club competitions (7/8/83/90) and for every
    national-team "selection" competition — they don't rank a club by country.
"""
from __future__ import annotations

from app.ingestion.bzzoiro.league_registry import LeagueMeta, LEAGUE_REGISTRY

_VALID_KINDS = {"domestic_1", "domestic_2", "uefa_club", "world", "selection"}
_VALID_FORMATS = {"round_robin", "groups", "knockout"}

_EXPECTED_IDS = {
    # Tier 1 — 1res divisions europeennes (20)
    1, 3, 4, 5, 6, 2, 10, 11, 14, 15, 13, 24, 25, 23, 54, 26, 84, 99, 22, 55,
    # Tier 2 — UEFA clubs (4)
    7, 8, 83, 90,
    # Tier 3 — 2es divisions europeennes (9)
    12, 38, 100, 94, 89, 88, 101, 102, 103,
    # Tier 4 — grands championnats mondiaux (15)
    9, 17, 18, 19, 20, 49, 50, 52, 53, 47, 80, 85, 32, 33, 104,
    # Selections (9)
    27, 58, 59, 62, 63, 64, 65, 66, 67,
}

_UEFA_CLUB_IDS = {7, 8, 83, 90}


def test_registry_has_exactly_57_entries():
    assert len(LEAGUE_REGISTRY) == 57


def test_registry_has_exactly_the_validated_ids():
    assert set(LEAGUE_REGISTRY.keys()) == _EXPECTED_IDS


def test_every_entry_is_a_leaguemeta_with_correct_types():
    for league_id, meta in LEAGUE_REGISTRY.items():
        assert isinstance(league_id, int)
        assert isinstance(meta, LeagueMeta)
        assert isinstance(meta.name, str) and meta.name
        assert meta.country is None or isinstance(meta.country, str)
        assert isinstance(meta.tier, (int, str))
        assert isinstance(meta.kind, str) and meta.kind in _VALID_KINDS
        assert isinstance(meta.format, str) and meta.format in _VALID_FORMATS
        assert meta.expected_count is None or isinstance(meta.expected_count, int)
        assert isinstance(meta.rank, int)


def test_expected_count_set_iff_format_is_round_robin():
    for league_id, meta in LEAGUE_REGISTRY.items():
        if meta.format == "round_robin":
            assert meta.expected_count is not None, (
                f"league {league_id} ({meta.name}): round_robin requires expected_count"
            )
        else:
            assert meta.expected_count is None, (
                f"league {league_id} ({meta.name}): {meta.format} must not carry expected_count"
            )


def test_country_is_none_for_uefa_club_competitions():
    for league_id in _UEFA_CLUB_IDS:
        meta = LEAGUE_REGISTRY[league_id]
        assert meta.country is None, f"league {league_id} ({meta.name}) must have country=None"
        assert meta.kind == "uefa_club"


def test_country_is_none_for_every_selection():
    selections = [m for m in LEAGUE_REGISTRY.values() if m.kind == "selection"]
    assert len(selections) == 9
    for meta in selections:
        assert meta.country is None, f"selection {meta.name} must have country=None"
        assert meta.tier == "selection"


def test_tier_is_selection_string_only_for_selection_kind():
    for meta in LEAGUE_REGISTRY.values():
        if meta.kind == "selection":
            assert meta.tier == "selection"
        else:
            assert isinstance(meta.tier, int)
            assert 1 <= meta.tier <= 4


def test_ranks_are_unique_and_cover_1_to_57():
    ranks = sorted(meta.rank for meta in LEAGUE_REGISTRY.values())
    assert ranks == list(range(1, 58))


def test_spot_check_known_values():
    # Valeurs transcrites du registre — ne pas inventer, verifier litteralement.
    premier_league = LEAGUE_REGISTRY[1]
    assert premier_league.name == "Premier League"
    assert premier_league.country == "England"
    assert premier_league.tier == 1
    assert premier_league.kind == "domestic_1"
    assert premier_league.format == "round_robin"
    assert premier_league.expected_count == 20
    assert premier_league.rank == 1

    champions_league = LEAGUE_REGISTRY[7]
    assert champions_league.name == "Champions League"
    assert champions_league.country is None
    assert champions_league.kind == "uefa_club"
    assert champions_league.format == "groups"
    assert champions_league.expected_count is None

    copa_libertadores = LEAGUE_REGISTRY[32]
    assert copa_libertadores.country is None
    assert copa_libertadores.kind == "world"
    assert copa_libertadores.format == "groups"

    world_cup = LEAGUE_REGISTRY[27]
    assert world_cup.name == "Coupe du monde 2026"
    assert world_cup.kind == "selection"
    assert world_cup.tier == "selection"
    assert world_cup.country is None
    assert world_cup.format == "groups"
    assert world_cup.expected_count is None
