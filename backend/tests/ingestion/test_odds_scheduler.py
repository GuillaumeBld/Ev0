# backend/tests/ingestion/test_odds_scheduler.py
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from app.ingestion.odds_scheduler import (
    _build_canonical_alias_map,
    _build_canonical_names_by_id,
    _match_result_to_fixture,
    _normalize_team,
    _side_strength,
    _token_contained,
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


def _indexes(canonical_teams):
    """Build (alias_map, names_by_id) the way OddsScheduler.tick does."""
    return (
        _build_canonical_alias_map(canonical_teams),
        _build_canonical_names_by_id(canonical_teams),
    )


def _cand(fid: int, home: str, away: str, home_cid: int | None = None, away_cid: int | None = None):
    """A due-fixture candidate: (id, home_norm, home_cid, away_norm, away_cid)."""
    return (fid, _normalize_team(home), home_cid, _normalize_team(away), away_cid)


def _match(book_home, book_away, candidates, canonical_teams=()):
    alias_map, names_by_id = _indexes(canonical_teams)
    return _match_result_to_fixture(
        book_home, book_away, candidates, alias_map, names_by_id
    )


class TestCanonicalIdentityMatch:
    """Canonical-identity leg of the per-team matcher (name_fr/name_en/aliases)."""

    def test_lyon_matches_olympique_lyonnais_via_canonical_team(self):
        """The original bug case: Betclic 'Lyon' vs DB fixture 'Olympique Lyonnais'.

        canonical_teams has name_fr='Lyon', name_en='Olympique Lyonnais' for this club
        (see alembic/versions/029). The fixture's home_canonical_team_id points at that
        same canonical row (populated by sync_fixtures_from_bzz via bzz_team_id).
        """
        cts = [_ct(1, "Lyon", name_en="Olympique Lyonnais"), _ct(2, "Paris Saint-Germain")]
        candidates = [_cand(42, "Olympique Lyonnais", "Paris Saint-Germain", 1, 2)]

        fixture_id, reason, *_ = _match("Lyon", "Paris Saint-Germain", candidates, cts)
        assert fixture_id == 42
        assert reason == "identity"

    def test_matches_reversed_home_away_orientation(self):
        cts = [_ct(1, "Lyon", name_en="Olympique Lyonnais"), _ct(2, "Marseille")]
        # DB fixture: Lyon (home) vs Marseille (away); bookmaker reports it reversed
        candidates = [_cand(7, "Olympique Lyonnais", "Marseille", 1, 2)]

        fixture_id, reason, *_ = _match("Marseille", "Lyon", candidates, cts)
        assert fixture_id == 7
        assert reason == "identity"

    def test_no_match_when_team_not_resolvable(self):
        """One side can't be resolved and doesn't fuzzy-match → no match."""
        cts = [_ct(1, "Lyon", name_en="Olympique Lyonnais"), _ct(2, "Paris Saint-Germain")]
        candidates = [_cand(42, "Olympique Lyonnais", "Paris Saint-Germain", 1, 2)]

        fixture_id, reason, *_ = _match("Lyon", "Some Unknown Club FC", candidates, cts)
        assert fixture_id is None
        assert reason == "none"

    def test_no_match_when_pair_not_due(self):
        """Both teams resolve, but no due fixture pairs them together."""
        cts = [_ct(1, "Lyon", name_en="Olympique Lyonnais"), _ct(2, "Marseille")]
        candidates: list = []  # nothing due right now

        fixture_id, reason, *_ = _match("Lyon", "Marseille", candidates, cts)
        assert fixture_id is None
        assert reason == "none"

    def test_matches_via_canonical_name_when_fixture_cid_missing(self):
        """Fixture side has no canonical_id, but its name equals a canonical alias
        the bookmaker name also resolves to → still an identity match."""
        cts = [_ct(1, "Lyon", name_en="Olympique Lyonnais"), _ct(2, "Marseille")]
        # home_cid deliberately NULL; name still 'Olympique Lyonnais'
        candidates = [_cand(50, "Olympique Lyonnais", "Marseille", None, 2)]

        fixture_id, reason, *_ = _match("Lyon", "Marseille", candidates, cts)
        assert fixture_id == 50
        assert reason == "identity"


class TestFuzzyMatch:
    """Fuzzy leg — restricted to already-due fixtures, both sides required.

    Anti-false-positive: a match is accepted only when BOTH home and away match
    for exactly one candidate fixture. A single matching team, or several
    plausible fixtures, never produces a guess.
    """

    def test_sparta_prague_matches_ac_sparta_praha(self):
        """Spelling variant (bookmaker short name vs DB official name)."""
        candidates = [_cand(101, "AC Sparta Praha", "Slavia Praha")]
        fixture_id, reason, *_ = _match("Sparta Prague", "Slavia Prague", candidates)
        assert fixture_id == 101
        assert reason == "fuzzy"

    def test_single_team_match_is_not_enough(self):
        """Anti-false-positive: home matches well, away is unrelated → no match."""
        candidates = [_cand(101, "AC Sparta Praha", "Slavia Praha")]
        fixture_id, reason, *_ = _match("Sparta Prague", "Manchester City", candidates)
        assert fixture_id is None
        assert reason == "none"

    def test_ambiguous_multiple_candidates_is_skipped(self):
        """Two due fixtures both fuzzy-match → refuse to guess, skip.

        The 'B' reserve markers are guarded against token-containment (see
        TestTokenContainment), so both fixtures land in the fuzzy tier and
        compete there.
        """
        candidates = [
            _cand(101, "AC Sparta Praha", "Slavia Praha"),
            _cand(102, "Sparta Praga", "Slavia Praga"),
        ]
        fixture_id, reason, competing_fids, _detail = _match(
            "Sparta Prague", "Slavia Prague", candidates
        )
        assert fixture_id is None
        assert reason == "ambiguous_fuzzy"
        assert len(competing_fids) >= 2

    def test_saint_gilloise_matches_royale_union(self):
        candidates = [_cand(55, "Royale Union Saint-Gilloise", "Club Brugge")]
        fixture_id, _reason, *_ = _match("Saint-Gilloise", "Club Bruges", candidates)
        assert fixture_id == 55

    def test_nec_nimegue_matches_nec_nijmegen(self):
        candidates = [_cand(9, "NEC Nijmegen", "Ajax")]
        fixture_id, _reason, *_ = _match("NEC Nimègue", "Ajax", candidates)
        assert fixture_id == 9

    def test_completely_unrelated_names_no_match(self):
        candidates = [_cand(1, "Real Madrid", "Barcelona")]
        fixture_id, reason, *_ = _match("Liverpool", "Chelsea", candidates)
        assert fixture_id is None
        assert reason == "none"


class TestMixedMatch:
    """The gap this change closes: one side canonical-resolvable, the other only
    fuzzy — different means per side, which pair-homogeneous matching missed."""

    def test_sparta_prague_lyon_mixed_case(self):
        """Real prod case (fixture 1110493): Betclic 'Sparta Prague' vs 'Lyon' →
        DB 'AC Sparta Praha' (home_cid NULL) vs 'Olympique Lyonnais' (away_cid=Lyon).

        - away 'Lyon' matches 'Olympique Lyonnais' by CANONICAL identity (alias);
        - home 'Sparta Prague' matches 'AC Sparta Praha' only by FUZZY (~0.71).
        Both sides OK via different means → fixture resolves.
        """
        cts = [_ct(23, "Lyon", name_en="Olympique Lyonnais", aliases=["Lyon", "OL"])]
        candidates = [
            _cand(1110493, "AC Sparta Praha", "Olympique Lyonnais", None, 23),
        ]
        fixture_id, reason, *_ = _match("Sparta Prague", "Lyon", candidates, cts)
        assert fixture_id == 1110493
        # away 'Lyon' is a certain (canonical) anchor; home 'Sparta Prague' only
        # fuzzes (~0.71) but clears the anchor floor → resolves at the anchor
        # tier, which strictly outranks two-sided fuzzy.
        assert reason == "anchor"

    def test_mixed_case_reversed_orientation(self):
        """Same, but the bookmaker lists the teams in the opposite order."""
        cts = [_ct(23, "Lyon", name_en="Olympique Lyonnais", aliases=["Lyon"])]
        candidates = [
            _cand(1110493, "Olympique Lyonnais", "AC Sparta Praha", 23, None),
        ]
        fixture_id, reason, *_ = _match("Sparta Prague", "Lyon", candidates, cts)
        assert fixture_id == 1110493
        assert reason == "anchor"

    def test_mixed_case_still_requires_both_sides(self):
        """Canonical side matches but the other side neither resolves nor fuzzes
        → still no match (both-sides guard holds for mixed matching too)."""
        cts = [_ct(23, "Lyon", name_en="Olympique Lyonnais", aliases=["Lyon"])]
        candidates = [_cand(1110493, "Manchester City", "Olympique Lyonnais", None, 23)]
        fixture_id, reason, *_ = _match("Sparta Prague", "Lyon", candidates, cts)
        assert fixture_id is None
        assert reason == "none"

    def test_identity_preferred_over_coincidental_fuzzy_on_other_fixture(self):
        """An identity (canonical) match must win over a coincidental fuzzy match
        on a different due fixture — no false ambiguity."""
        cts = [
            _ct(23, "Lyon", name_en="Olympique Lyonnais", aliases=["Lyon"]),
            _ct(30, "Marseille", name_en="Olympique de Marseille"),
        ]
        candidates = [
            # Identity match for both sides (via canonical alias 'Lyon' + name)
            _cand(1, "Olympique Lyonnais", "Olympique de Marseille", 23, 30),
            # Coincidental fuzzy lookalike ('lyon'~'lyonnais'=0.67), not identity.
            _cand(2, "Lyonnais", "Marseille Olympique"),
        ]
        fixture_id, reason, competing_fids, _detail = _match(
            "Lyon", "Marseille", candidates, cts
        )
        assert fixture_id == 1
        assert reason == "identity"
        assert competing_fids == [1]  # identity tier decides; the lookalike not chosen


class TestTokenContainment:
    """Token-containment: the shorter name's tokens fully inside the longer's.

    Bridges abbreviated↔official names that fuzzy scoring misses
    (e.g. 'AGF' vs 'AGF Aarhus'), classed as a STRONG (near-identity) match.
    """

    def test_agf_contained_in_agf_aarhus(self):
        assert _token_contained("agf", "agf aarhus") is True
        assert _token_contained("agf aarhus", "agf") is True  # symmetric

    def test_numeric_extra_is_not_a_variant_marker(self):
        """'Mainz' → 'Mainz 05' is the same club, still contained."""
        assert _token_contained("mainz", "mainz 05") is True

    def test_reserve_marker_blocks_containment(self):
        """'Sparta Prague' ⊄ 'Sparta Prague B' — reserve side is a different team."""
        assert _token_contained("sparta prague", "sparta prague b") is False
        assert _token_contained("bayern", "bayern ii") is False

    def test_unrelated_names_not_contained(self):
        assert _token_contained("real madrid", "real betis") is False

    def test_side_strength_ranks_identity_token_fuzzy_none(self):
        """identity > token-containment > fuzzy > none (isolated, no canonical ids)."""
        def side(name):
            return (_normalize_team(name), None, {_normalize_team(name)})

        book = side("AGF Aarhus")
        s_identity = _side_strength(book, side("AGF Aarhus"), 0.65)  # exact
        s_token = _side_strength(book, side("AGF"), 0.65)            # {agf} ⊂ {agf,aarhus}
        s_none = _side_strength(book, side("Aarhus GF"), 0.65)       # ~0.63 < 0.65, no token
        s_fuzzy = _side_strength(
            side("Sparta Prague"), side("AC Sparta Praha"), 0.65      # ~0.71 fuzzy
        )
        assert s_identity > s_token > s_fuzzy > s_none == 0


class TestAgfMatch:
    """The AGF prod gap: home abbreviated 'AGF', away exact — solved by
    token-containment (AGF Aarhus) or anchoring (Aarhus GF)."""

    def test_agf_aarhus_matches_via_token_containment(self):
        """'AGF Aarhus' vs 'Sabah FK' → fixture 'AGF' vs 'Sabah FK'.
        home matches by token-containment, away exact → both-sides token tier."""
        candidates = [_cand(1111243, "AGF", "Sabah FK")]
        fixture_id, reason, *_ = _match("AGF Aarhus", "Sabah FK", candidates)
        assert fixture_id == 1111243
        assert reason == "token"

    def test_aarhus_gf_matches_via_anchor(self):
        """'Aarhus GF' vs 'Sabah FK' → fixture 'AGF' vs 'Sabah FK'.
        'Aarhus GF' is NOT token-contained in 'AGF' and only fuzzes ~0.5, so the
        away side 'Sabah FK' anchors the fixture and the home side clears the
        low floor (0.5 >= 0.30)."""
        candidates = [_cand(1111243, "AGF", "Sabah FK")]
        fixture_id, reason, competing_fids, detail = _match(
            "Aarhus GF", "Sabah FK", candidates
        )
        assert fixture_id == 1111243
        assert reason == "anchor"
        assert competing_fids == [1111243]
        assert detail == _normalize_team("Sabah FK")  # the certain (anchor) side


class TestCalendarAnchoring:
    """Anchoring tier guards — floor, unique anchor, and priority below identity."""

    def test_anchor_rejects_absurd_second_side(self):
        """'Real Madrid' vs 'Sabah FK' must NOT anchor onto AGF-Sabah: 'Sabah FK'
        anchors, but 'Real Madrid'~'AGF'≈0.14 is below the 0.30 floor → skip."""
        candidates = [_cand(1111243, "AGF", "Sabah FK")]
        fixture_id, reason, *_ = _match("Real Madrid", "Sabah FK", candidates)
        assert fixture_id is None
        assert reason == "none"

    def test_anchor_ambiguous_when_anchor_team_plays_two_due_fixtures(self):
        """If the certain (anchor) side matches TWO due fixtures, never guess."""
        candidates = [
            _cand(1, "AGF", "Sabah FK"),
            _cand(2, "Sabah FK", "Qarabag"),  # 'Sabah FK' also here (as home)
        ]
        fixture_id, reason, *_ = _match("Aarhus GF", "Sabah FK", candidates)
        assert fixture_id is None
        assert reason == "ambiguous_anchor"

    def test_identity_not_stolen_by_concurrent_anchor(self):
        """A genuine both-sides match must win; anchoring is never even reached
        while a higher tier (identity/token) has a candidate."""
        candidates = [
            # Real match — both sides exact → identity tier.
            _cand(1, "AGF Aarhus", "Sabah FK"),
            # Would be an anchor candidate ('Sabah FK' certain) if we got there.
            _cand(2, "Aarhus GF", "Sabah FK"),
        ]
        fixture_id, reason, *_ = _match("AGF Aarhus", "Sabah FK", candidates)
        assert fixture_id == 1
        assert reason in ("identity", "token")


def test_etoile_rouge_alias_resolves_to_db_name():
    """Known translation mismatch (not a spelling variant, fuzzy can't bridge it):
    Betclic/PMU 'Étoile Rouge' vs the DB's 'FK Crvena zvezda' (Red Star Belgrade)."""
    assert _normalize_team("Étoile Rouge") == _normalize_team("FK Crvena zvezda")
