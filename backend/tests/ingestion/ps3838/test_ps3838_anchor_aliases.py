"""Ancrage a 100 % : pliage des lettres non decomposables + alias canoniques.

Sept matchs a moins de 7 jours restaient non ancres en production alors que
l'evenement PS3838 existait, au bon horaire et dans la bonne competition.
Deux causes, une classe de test chacune.
"""
from datetime import UTC, datetime
from types import SimpleNamespace

from app.ingestion.ps3838.anchor import build_alias_map, match_event, norm_team
from app.ingestion.ps3838.client import Ps3838Event

KO = datetime(2026, 8, 25, 19, 0, tzinfo=UTC)


def _ev(eid, home, away, ko=KO):
    return Ps3838Event(eid, home, away, ko, "Test League",
                       {"home": 1.4, "draw": 5.0, "away": 8.0},
                       {"over_3.0": 1.8, "under_3.0": 2.0}, 3.0)


def _fx(home, away, ko=KO):
    return SimpleNamespace(id=1, home_team=home, away_team=away, kickoff_utc=ko)


def _ct(cid, name_fr, name_en=None, aliases=()):
    return SimpleNamespace(id=cid, name_fr=name_fr, name_en=name_en, aliases=list(aliases))


# ── Pliage des lettres qui ne se decomposent pas ──────────────────


def test_slashed_o_is_folded_not_dropped():
    """NFKD ne decompose pas 'o barre' : sans traitement, Bodo devient 'Bod'."""
    assert "bodo" in norm_team("Bodø/Glimt")
    assert norm_team("Bodø/Glimt") == norm_team("Bodo Glimt")


def test_other_non_decomposable_letters():
    assert norm_team("Malmö FF") == norm_team("Malmo FF")
    assert norm_team("Æ United") == norm_team("AE United")
    assert norm_team("Łódź") == norm_team("Lodz")


def test_accents_still_fold_as_before():
    assert norm_team("Atlético Madrid") == norm_team("Atletico Madrid")
    assert norm_team("Fenerbahçe") == norm_team("Fenerbahce")


def test_bodo_glimt_now_anchors():
    evs = [_ev(111, "Bodo Glimt", "NEC Nijmegen")]
    assert match_event(_fx("Bodø/Glimt", "NEC Nijmegen"), evs).event_id == 111


# ── Alias canoniques ──────────────────────────────────────────────


def test_alias_map_indexes_every_name_and_alias():
    m = build_alias_map([_ct(7, "Lyon", "Olympique Lyonnais", ["OL", "PSG-like"])])
    assert m[frozenset(norm_team("Lyon"))] == 7
    assert m[frozenset(norm_team("Olympique Lyonnais"))] == 7


def test_alias_too_short_to_tokenise_is_skipped_not_crashing():
    """'OL' fait deux lettres : le filtre de longueur le reduit a rien.
    Un tel alias ne peut pas servir de cle -- il est ignore, pas indexe sur
    l'ensemble vide (qui volerait la cle de toutes les equipes sans tokens)."""
    m = build_alias_map([_ct(7, "Lyon", "Olympique Lyonnais", ["OL"])])
    assert frozenset() not in m
    assert len([k for k in m if not k]) == 0


def test_first_writer_wins_on_conflicting_alias():
    """Un alias ne doit jamais voler la cle d'une equipe qui l'a deja prise."""
    m = build_alias_map([_ct(1, "Racing"), _ct(2, "Racing")])
    assert m[frozenset(norm_team("Racing"))] == 1


def test_short_form_anchors_through_alias():
    aliases = build_alias_map([
        _ct(1, "Toulouse"),
        _ct(2, "Lyon", "Olympique Lyonnais", ["OL"]),
    ])
    evs = [_ev(111, "Toulouse", "Lyon")]
    fx = _fx("Toulouse", "Olympique Lyonnais")
    assert match_event(fx, evs) is None, "sans alias, aucun rapprochement"
    assert match_event(fx, evs, aliases).event_id == 111


def test_abbreviation_anchors_through_alias():
    aliases = build_alias_map([
        _ct(1, "Inter Milan", "Internazionale", ["Inter", "FC Internazionale"]),
        _ct(2, "Monza"),
    ])
    evs = [_ev(111, "Internazionale", "Monza")]
    assert match_event(_fx("Inter", "Monza"), evs, aliases).event_id == 111


def test_alias_never_bridges_two_different_teams():
    """L'alias rapproche, il ne doit pas confondre."""
    aliases = build_alias_map([_ct(1, "Real Madrid"), _ct(2, "Real Sociedad")])
    evs = [_ev(111, "Real Sociedad", "Monza")]
    assert match_event(_fx("Real Madrid", "Monza"), evs, aliases) is None


def test_reserve_guard_still_holds_with_aliases():
    """Le garde-fou reserve/premiere equipe prime sur l'alias."""
    aliases = build_alias_map([_ct(1, "Lyon", "Olympique Lyonnais")])
    evs = [_ev(111, "Lyon II", "Monza")]
    assert match_event(_fx("Olympique Lyonnais", "Monza"), evs, aliases) is None


def test_no_alias_map_keeps_previous_behaviour():
    evs = [_ev(111, "Monza", "Inter")]
    assert match_event(_fx("Monza", "Inter"), evs) is not None
    assert match_event(_fx("Monza", "Internazionale"), evs) is None
