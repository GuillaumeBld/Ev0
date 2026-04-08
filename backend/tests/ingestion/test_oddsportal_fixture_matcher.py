"""Tests for OddsPortal fixture matcher."""

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.ingestion.oddsportal_fixture_matcher import (
    _pair_score,
    _token_sort_ratio,
    match_items_to_fixtures,
)
from app.ingestion.oddsportal_league_discoverer import OddsPortalMatchItem


_NOW = datetime(2026, 4, 12, 17, 0, tzinfo=timezone.utc)


def _make_item(home, away, league="ligue_1", offset_min=0, url=None):
    return OddsPortalMatchItem(
        home_raw=home,
        away_raw=away,
        kickoff_utc=_NOW + timedelta(minutes=offset_min),
        match_url=url or f"https://www.oddsportal.com/test/{home.lower().replace(' ', '-')}/",
        league=league,
    )


def _make_fixture(home, away, league="ligue_1", offset_min=0, fid=1):
    fix = MagicMock()
    fix.id = fid
    fix.home_team = home
    fix.away_team = away
    fix.league = league
    fix.kickoff_utc = _NOW + timedelta(minutes=offset_min)
    return fix


def _make_session(canonical_teams, fixtures):
    """Mock session retournant d'abord canonical_teams, puis fixtures."""
    call_count = 0

    async def _execute(query):
        nonlocal call_count
        call_count += 1
        result = MagicMock()
        if call_count == 1:
            result.scalars.return_value.all.return_value = canonical_teams
        else:
            result.scalars.return_value.all.return_value = fixtures
        return result

    session = AsyncMock()
    session.execute = AsyncMock(side_effect=_execute)
    session.commit = AsyncMock()
    return session


class TestTokenSortRatio:
    def test_identical(self):
        assert _token_sort_ratio("manchester-city", "manchester-city") == 100.0

    def test_token_order_independent(self):
        score = _token_sort_ratio("city-manchester", "manchester-city")
        assert score == 100.0

    def test_partial_match(self):
        assert _token_sort_ratio("manchester-city", "man-city") > 65.0

    def test_unrelated(self):
        assert _token_sort_ratio("psg", "lyon") < 50.0


class TestPairScore:
    def test_exact_via_normalize(self):
        # "Man City" → normalize → "manchester-city" (via TEAM_ALIASES)
        # fixture "Manchester City" → normalize → "manchester-city"
        item = _make_item("Man City", "Arsenal")
        fixture = _make_fixture("Manchester City", "Arsenal")
        assert _pair_score(item, fixture, {}) >= 75.0

    def test_low_score_wrong_fixture(self):
        item = _make_item("PSG", "Lyon")
        fixture = _make_fixture("Manchester City", "Arsenal")
        assert _pair_score(item, fixture, {}) < 50.0

    def test_alias_index_gives_100(self):
        # Simuler un CanonicalTeam connu : "psg" → CT dont name_fr="Paris Saint-Germain"
        ct = MagicMock()
        ct.name_fr = "Paris Saint-Germain"
        alias_index = {"psg": ct, "paris-saint-germain": ct}
        item = _make_item("PSG", "Arsenal")
        fixture = _make_fixture("Paris Saint-Germain", "Arsenal")
        score = _pair_score(item, fixture, alias_index)
        assert score == 100.0


class TestMatchItemsToFixtures:
    @pytest.mark.asyncio
    async def test_simple_match(self):
        fixture = _make_fixture("Manchester City", "Arsenal", league="premier_league", fid=42)
        session = _make_session(canonical_teams=[], fixtures=[fixture])
        item = _make_item("Man City", "Arsenal", league="premier_league",
                          url="https://op.com/man-city-arsenal/")
        results = await match_items_to_fixtures([item], session)
        assert len(results) == 1
        assert results[0] == (42, "https://op.com/man-city-arsenal/")

    @pytest.mark.asyncio
    async def test_below_threshold_rejected(self):
        fixture = _make_fixture("Manchester City", "Arsenal", fid=1)
        session = _make_session(canonical_teams=[], fixtures=[fixture])
        item = _make_item("Real Madrid", "Barcelona")
        results = await match_items_to_fixtures([item], session)
        assert results == []

    @pytest.mark.asyncio
    async def test_empty_input(self):
        session = _make_session(canonical_teams=[], fixtures=[])
        results = await match_items_to_fixtures([], session)
        assert results == []

    @pytest.mark.asyncio
    async def test_three_way_assignment(self):
        """3 items simultanés → assignation optimale."""
        fixtures = [
            _make_fixture("Manchester City", "Arsenal", league="premier_league", fid=1),
            _make_fixture("Liverpool", "Chelsea", league="premier_league", fid=2),
            _make_fixture("Tottenham", "Manchester United", league="premier_league", fid=3),
        ]
        session = _make_session(canonical_teams=[], fixtures=fixtures)
        items = [
            _make_item("Man City", "Arsenal", league="premier_league",
                       offset_min=0, url="https://op.com/1/"),
            _make_item("Liverpool", "Chelsea", league="premier_league",
                       offset_min=2, url="https://op.com/2/"),
            _make_item("Tottenham", "Man Utd", league="premier_league",
                       offset_min=3, url="https://op.com/3/"),
        ]
        results = await match_items_to_fixtures(items, session)
        matched = {fid: url for fid, url in results}
        assert matched.get(1) == "https://op.com/1/"
        assert matched.get(2) == "https://op.com/2/"
        assert matched.get(3) == "https://op.com/3/"

    @pytest.mark.asyncio
    async def test_no_candidates_skipped(self):
        """Item dont la league n'a aucune fixture correspondante → ignoré."""
        fixture = _make_fixture("Manchester City", "Arsenal", league="bundesliga", fid=1)
        session = _make_session(canonical_teams=[], fixtures=[fixture])
        item = _make_item("PSG", "Lyon", league="ligue_1")  # league différente
        results = await match_items_to_fixtures([item], session)
        assert results == []


class TestAliasLearning:
    @pytest.mark.asyncio
    async def test_new_alias_collected(self):
        """Un nom OddsPortal inconnu doit être ajouté aux alias de son CanonicalTeam."""
        ct = MagicMock()
        ct.id = 99
        ct.name_fr = "Paris Saint-Germain"
        ct.aliases = ["paris-saint-germain", "psg"]

        fixture = _make_fixture("Paris Saint-Germain", "Lyon", league="ligue_1", fid=10)
        session = _make_session(canonical_teams=[ct], fixtures=[fixture])

        # "Paris SG" n'est pas dans les aliases connus
        item = _make_item("Paris SG", "Lyon", league="ligue_1",
                          url="https://op.com/psg-lyon/")
        results = await match_items_to_fixtures([item], session)

        # Le match doit réussir (via fuzzy ou TEAM_ALIASES)
        assert len(results) == 1
        assert results[0][0] == 10

        # Vérifier que session.execute a été appelé pour l'UPDATE des alias
        # (au moins 3 appels : select CT, select Fixture, puis update CT et/ou insert poll_state)
        assert session.execute.call_count >= 3
