# backend/tests/ingestion/test_odds_scheduler.py
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from app.ingestion.odds_scheduler import (
    _build_canonical_alias_map,
    _match_via_canonical_alias,
    _match_via_fuzzy,
    _normalize_team,
    scrape_interval_seconds,
    should_scrape,
)


def _ko(minutes_from_now: int) -> datetime:
    return datetime.now(UTC) + timedelta(minutes=minutes_from_now)


def test_interval_far_from_ko():
    """More than 6h → 7200s (2h)."""
    assert scrape_interval_seconds(_ko(600)) == 7200


def test_interval_mid_range():
    """2h–6h → 1800s (30min)."""
    assert scrape_interval_seconds(_ko(240)) == 1800


def test_interval_close_to_ko():
    """5min–2h → 120s (2min)."""
    assert scrape_interval_seconds(_ko(30)) == 120


def test_should_not_scrape_within_5min():
    """Less than 5min before KO → stop."""
    assert should_scrape(_ko(3), last_scraped_at=None) is False


def test_should_not_scrape_past_ko():
    """After KO → stop."""
    assert should_scrape(_ko(-10), last_scraped_at=None) is False


def test_should_scrape_when_never_scraped():
    """Never scraped + in window → True."""
    assert should_scrape(_ko(60), last_scraped_at=None) is True


def test_should_not_scrape_when_recent():
    """Scraped 1min ago, interval=120s → False."""
    last = datetime.now(UTC) - timedelta(seconds=60)
    assert should_scrape(_ko(60), last_scraped_at=last) is False


def test_should_scrape_when_overdue():
    """Scraped 3min ago, interval=120s → True."""
    last = datetime.now(UTC) - timedelta(seconds=180)
    assert should_scrape(_ko(60), last_scraped_at=last) is True


# ---------------------------------------------------------------------------
# CORRECTION 1 — team-name reconciliation fallbacks
# ---------------------------------------------------------------------------


def _ct(id: int, name_fr: str, name_en: str | None = None, aliases: list[str] | None = None):
    """Lightweight CanonicalTeam stand-in (id, name_fr, name_en, aliases)."""
    return SimpleNamespace(id=id, name_fr=name_fr, name_en=name_en, aliases=aliases or [])


class TestCanonicalAliasMatch:
    """Tier 1 fallback: resolve a bookmaker name via CanonicalTeam (name_fr/name_en/aliases)."""

    def test_lyon_matches_olympique_lyonnais_via_canonical_team(self):
        """The exact bug report case: Betclic 'Lyon' vs DB fixture 'Olympique Lyonnais'.

        canonical_teams has name_fr='Lyon', name_en='Olympique Lyonnais' for this club
        (see alembic/versions/029). The fixture's home_canonical_team_id points at that
        same canonical row (populated by sync_fixtures_from_bzz via bzz_team_id).
        """
        canonical_teams = [
            _ct(1, "Lyon", name_en="Olympique Lyonnais"),
            _ct(2, "Paris Saint-Germain"),
        ]
        alias_map = _build_canonical_alias_map(canonical_teams)

        # Fixture 42: home canonical id 1 (Lyon/Olympique Lyonnais), away canonical id 2 (PSG)
        fixture_by_canonical_ids = {(1, 2): 42}

        fixture_id = _match_via_canonical_alias(
            "Lyon", "Paris Saint-Germain", alias_map, fixture_by_canonical_ids
        )
        assert fixture_id == 42

    def test_matches_reversed_home_away_orientation(self):
        canonical_teams = [_ct(1, "Lyon", name_en="Olympique Lyonnais"), _ct(2, "Marseille")]
        alias_map = _build_canonical_alias_map(canonical_teams)
        fixture_by_canonical_ids = {(1, 2): 7}  # DB fixture: Lyon (home) vs Marseille (away)

        # Bookmaker reports it the other way round
        fixture_id = _match_via_canonical_alias(
            "Marseille", "Lyon", alias_map, fixture_by_canonical_ids
        )
        assert fixture_id == 7

    def test_no_match_when_team_not_resolvable(self):
        """One of the two names can't be resolved to any canonical team → no match."""
        canonical_teams = [_ct(1, "Lyon", name_en="Olympique Lyonnais")]
        alias_map = _build_canonical_alias_map(canonical_teams)
        fixture_by_canonical_ids = {(1, 2): 42}

        fixture_id = _match_via_canonical_alias(
            "Lyon", "Some Unknown Club FC", alias_map, fixture_by_canonical_ids
        )
        assert fixture_id is None

    def test_no_match_when_canonical_pair_not_due(self):
        """Both teams resolve, but that (home, away) pair isn't among due fixtures."""
        canonical_teams = [_ct(1, "Lyon", name_en="Olympique Lyonnais"), _ct(2, "Marseille")]
        alias_map = _build_canonical_alias_map(canonical_teams)
        fixture_by_canonical_ids = {}  # nothing due right now

        fixture_id = _match_via_canonical_alias(
            "Lyon", "Marseille", alias_map, fixture_by_canonical_ids
        )
        assert fixture_id is None


class TestFuzzyMatch:
    """Tier 2 fallback: fuzzy name similarity restricted to already-due fixtures.

    Anti-false-positive guard: a match is only accepted when BOTH home and away
    similarity cross the threshold for exactly one candidate fixture. A single
    matching team, or several plausible fixtures, must never produce a guess.
    """

    def test_sparta_prague_matches_ac_sparta_praha(self):
        """Spelling variant (bookmaker short name vs DB official name)."""
        candidates = [
            (101, _normalize_team("AC Sparta Praha"), _normalize_team("Slavia Praha")),
        ]
        fixture_id, scored = _match_via_fuzzy("Sparta Prague", "Slavia Prague", candidates)
        assert fixture_id == 101
        assert len(scored) == 1

    def test_single_team_match_is_not_enough(self):
        """Anti-false-positive: home matches well, away is unrelated → no match at all."""
        candidates = [
            (101, _normalize_team("AC Sparta Praha"), _normalize_team("Slavia Praha")),
        ]
        fixture_id, scored = _match_via_fuzzy("Sparta Prague", "Manchester City", candidates)
        assert fixture_id is None
        assert scored == []

    def test_ambiguous_multiple_candidates_is_skipped(self):
        """Two due fixtures both cross the threshold → refuse to guess, skip."""
        candidates = [
            (101, _normalize_team("AC Sparta Praha"), _normalize_team("Slavia Praha")),
            (102, _normalize_team("Sparta Prague B"), _normalize_team("Slavia Prague B")),
        ]
        fixture_id, scored = _match_via_fuzzy("Sparta Prague", "Slavia Prague", candidates)
        assert fixture_id is None
        assert len(scored) >= 2

    def test_saint_gilloise_matches_royale_union(self):
        candidates = [
            (55, _normalize_team("Royale Union Saint-Gilloise"), _normalize_team("Club Brugge")),
        ]
        fixture_id, scored = _match_via_fuzzy("Saint-Gilloise", "Club Bruges", candidates)
        assert fixture_id == 55

    def test_nec_nimegue_matches_nec_nijmegen(self):
        candidates = [
            (9, _normalize_team("NEC Nijmegen"), _normalize_team("Ajax")),
        ]
        fixture_id, scored = _match_via_fuzzy("NEC Nimègue", "Ajax", candidates)
        assert fixture_id == 9

    def test_completely_unrelated_names_no_match(self):
        candidates = [
            (1, _normalize_team("Real Madrid"), _normalize_team("Barcelona")),
        ]
        fixture_id, scored = _match_via_fuzzy("Liverpool", "Chelsea", candidates)
        assert fixture_id is None
        assert scored == []


def test_etoile_rouge_alias_resolves_to_db_name():
    """Known translation mismatch (not a spelling variant, fuzzy can't bridge it):
    Betclic/PMU 'Étoile Rouge' vs the DB's 'FK Crvena zvezda' (Red Star Belgrade)."""
    assert _normalize_team("Étoile Rouge") == _normalize_team("FK Crvena zvezda")
