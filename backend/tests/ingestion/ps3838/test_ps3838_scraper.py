from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from app.ingestion.ps3838.client import Ps3838Event
from app.ingestion.ps3838.scraper import build_results

KO = datetime(2026, 8, 19, 19, 0, tzinfo=UTC)


def _fx(fid, eid, home="Atlético Madrid", away="Málaga CF"):
    return SimpleNamespace(
        id=fid, ps3838_event_id=eid, home_team=home, away_team=away,
        kickoff_utc=KO, league="la_liga",
    )


# Sentinel distinct de None : un appelant qui passe explicitement h2h=None
# (ou totals=None) doit obtenir un evenement SANS ce marche, pas la valeur
# par defaut — sinon test_event_without_h2h_is_skipped ne peut jamais
# construire le cas qu'il pretend tester.
_UNSET = object()


def _ev(eid, h2h=_UNSET, totals=_UNSET):
    return Ps3838Event(
        eid, "Atletico Madrid", "Malaga", KO, "Spain - La Liga",
        {"home": 1.36, "draw": 5.26, "away": 8.99} if h2h is _UNSET else h2h,
        {"over_3.0": 1.85, "under_3.0": 2.04} if totals is _UNSET else totals,
        3.0,
    )


def test_result_is_built_from_anchored_id_only():
    res = build_results([_fx(1, 111)], [_ev(111), _ev(222)])
    assert len(res) == 1
    r = res[0]
    assert r.fixture_id == 1
    assert r.bookmaker == "ps3838"
    assert r.h2h == {"home": 1.36, "draw": 5.26, "away": 8.99}
    assert r.totals == {"over_3.0": 1.85, "under_3.0": 2.04}
    assert r.btts is None


def test_unanchored_fixture_is_skipped():
    assert build_results([_fx(1, None)], [_ev(111)]) == []


def test_event_absent_from_feed_is_skipped_without_error():
    assert build_results([_fx(1, 999)], [_ev(111)]) == []


def test_event_without_h2h_is_skipped():
    assert build_results([_fx(1, 111)], [_ev(111, h2h=None)]) == []


def test_event_without_totals_is_skipped():
    assert build_results([_fx(1, 111)], [_ev(111, totals=None)]) == []


def test_kickoff_drift_beyond_two_hours_is_rejected():
    """Filet de securite : meme ancree, une fixture dont le coup d'envoi
    s'ecarte de plus de 2h de celui de l'evenement PS3838 est rejetee (une
    reattribution d'identifiant cote PS3838 ne doit pas passer inapercue)."""
    ev = _ev(111)
    fx = _fx(1, 111)
    fx.kickoff_utc = KO + timedelta(hours=2, minutes=1)
    assert build_results([fx], [ev]) == []


def test_kickoff_within_two_hours_is_kept():
    ev = _ev(111)
    fx = _fx(1, 111)
    fx.kickoff_utc = KO + timedelta(hours=1, minutes=59)
    assert len(build_results([fx], [ev])) == 1


def test_names_never_used_for_matching():
    """L'evenement porte des noms differents : seul l'identifiant compte."""
    ev = _ev(111)
    ev.home, ev.away = "Equipe Inconnue A", "Equipe Inconnue B"
    res = build_results([_fx(1, 111)], [ev])
    assert len(res) == 1 and res[0].fixture_id == 1
