"""Settlement avec-sub : chaîne de remplacement transitive + règlement des 4 marchés."""

import pytest

from app.evaluation.settlement import FixtureEvents, replacement_chain, settle


def _events(goals=(), assists=(), subs=()):
    return FixtureEvents(goals=list(goals), assists=list(assists), subs=list(subs))


class TestReplacementChain:
    def test_joueur_jamais_remplace(self):
        assert replacement_chain([("ramos", "dembele")], "mbappe") == set()

    def test_remplacement_simple(self):
        assert replacement_chain([("ramos", "dembele")], "dembele") == {"ramos"}

    def test_chaine_transitive(self):
        # dembele sort pour ramos, puis ramos sort pour barcola
        subs = [("ramos", "dembele"), ("barcola", "ramos")]
        assert replacement_chain(subs, "dembele") == {"ramos", "barcola"}

    def test_deux_subs_independantes_non_melangees(self):
        subs = [("ramos", "dembele"), ("zaire-emery", "vitinha")]
        assert replacement_chain(subs, "dembele") == {"ramos"}


class TestSettle:
    def test_goal_sec_le_joueur_marque(self):
        assert settle("goal", "mbappe", _events(goals=["mbappe"])) is True

    def test_goal_sec_le_remplacant_ne_compte_pas(self):
        ev = _events(goals=["ramos"], subs=[("ramos", "dembele")])
        assert settle("goal", "dembele", ev) is False

    def test_goal_with_sub_le_remplacant_compte(self):
        ev = _events(goals=["ramos"], subs=[("ramos", "dembele")])
        assert settle("goal_with_sub", "dembele", ev) is True

    def test_goal_with_sub_chaine_transitive(self):
        ev = _events(goals=["barcola"], subs=[("ramos", "dembele"), ("barcola", "ramos")])
        assert settle("goal_with_sub", "dembele", ev) is True

    def test_assist_with_sub(self):
        ev = _events(assists=["ramos"], subs=[("ramos", "dembele")])
        assert settle("assist_with_sub", "dembele", ev) is True
        assert settle("assist", "dembele", ev) is False

    def test_normalisation_des_noms(self):
        # settle normalise l'entrée — accents/majuscules indifférents
        assert settle("goal", "Mbappé", _events(goals=["mbappe"])) is True

    def test_marche_inconnu(self):
        with pytest.raises(ValueError, match="first_goal"):
            settle("first_goal", "mbappe", _events())
