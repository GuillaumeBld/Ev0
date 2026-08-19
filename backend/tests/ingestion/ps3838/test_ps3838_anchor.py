from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from app.ingestion.ps3838.anchor import match_event, norm_team
from app.ingestion.ps3838.client import Ps3838Event

KO = datetime(2026, 8, 19, 19, 0, tzinfo=UTC)


def _ev(eid, home, away, ko=KO):
    return Ps3838Event(eid, home, away, ko, "Spain - La Liga", {"home": 1.4, "draw": 5.0, "away": 8.0}, {"over_3.0": 1.8, "under_3.0": 2.0}, 3.0)


def _fx(home, away, ko=KO):
    return SimpleNamespace(id=1, home_team=home, away_team=away, kickoff_utc=ko)


def test_norm_folds_accents_and_strips_club_suffixes():
    assert norm_team("Atlético Madrid") == norm_team("Atletico Madrid")
    assert norm_team("Málaga CF") == norm_team("Malaga")
    assert "madrid" in norm_team("Real Madrid CF")


def test_exact_match_resolves():
    evs = [_ev(111, "Atletico Madrid", "Malaga")]
    assert match_event(_fx("Atlético Madrid", "Málaga CF"), evs).event_id == 111


def test_same_teams_different_day_does_not_resolve():
    evs = [_ev(111, "Atletico Madrid", "Malaga", KO + timedelta(days=1))]
    assert match_event(_fx("Atlético Madrid", "Málaga CF"), evs) is None


def test_two_hour_tolerance():
    evs = [_ev(111, "Atletico Madrid", "Malaga", KO + timedelta(hours=1, minutes=59))]
    assert match_event(_fx("Atlético Madrid", "Málaga CF"), evs).event_id == 111
    evs = [_ev(222, "Atletico Madrid", "Malaga", KO + timedelta(hours=2, minutes=1))]
    assert match_event(_fx("Atlético Madrid", "Málaga CF"), evs) is None


def test_reversed_teams_do_not_resolve():
    """Domicile et exterieur inverses : ce n'est pas le meme match."""
    evs = [_ev(111, "Malaga", "Atletico Madrid")]
    assert match_event(_fx("Atlético Madrid", "Málaga CF"), evs) is None


def test_ambiguous_candidates_do_not_resolve():
    """Deux evenements plausibles a la meme heure : on ne devine pas."""
    evs = [_ev(111, "Atletico Madrid", "Malaga"), _ev(222, "Atletico Madrid", "Malaga")]
    assert match_event(_fx("Atlético Madrid", "Málaga CF"), evs) is None


def test_partial_team_overlap_is_not_enough():
    """'Real Madrid' vs 'Real Sociedad' partagent un token : insuffisant."""
    evs = [_ev(111, "Real Sociedad", "Malaga")]
    assert match_event(_fx("Real Madrid", "Málaga CF"), evs) is None
