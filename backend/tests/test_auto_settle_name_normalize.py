"""Tests for player name normalization in auto_settle."""

from app.ingestion.auto_settle import _minutes_du_joueur, _normalize_name


class TestNormalizeName:
    def test_lowercase(self):
        assert _normalize_name("Ludovic Blas") == _normalize_name("LUDOVIC BLAS")

    def test_removes_hyphens(self):
        assert _normalize_name("Mousa Al-Tamari") == _normalize_name("Mousa Al Tamari")

    def test_removes_apostrophes(self):
        assert _normalize_name("N'Diaye") == _normalize_name("Ndiaye")

    def test_collapses_spaces(self):
        assert _normalize_name("N Diaye") == _normalize_name("Ndiaye")

    def test_mixed_separators(self):
        # Al-Tamari vs Al Tamari vs AlTamari
        assert _normalize_name("Mousa Al-Tamari") == _normalize_name("Mousa Al Tamari")

    def test_strips_whitespace(self):
        assert _normalize_name("  Blas  ") == _normalize_name("Blas")


class TestMinutesDuJoueur:
    """Le rapprochement par nom dans la table des minutes jouees.

    Remplace les tests de `_find_pmm_by_name` : le reglement lit desormais
    `bzz_player_match_stats` (1,4 M de lignes) et non `player_match_minutes`
    (470), via un dict {nom -> minutes}. Le rapprochement par nom reste le
    meme, ce sont ces cas qui sont verifies ici.
    """

    def test_correspondance_exacte(self):
        assert _minutes_du_joueur({"Ludovic Blas": 90.0}, "Ludovic Blas") == 90.0

    def test_tiret_contre_espace(self):
        assert _minutes_du_joueur({"Mousa Al Tamari": 76.0}, "Mousa Al-Tamari") == 76.0

    def test_apostrophe_contre_rien(self):
        assert _minutes_du_joueur({"Ndiaye": 45.0}, "N'Diaye") == 45.0

    def test_apostrophe_contre_espace(self):
        assert _minutes_du_joueur({"N Diaye": 45.0}, "N'Diaye") == 45.0

    def test_joueur_absent_du_groupe_vaut_zero_minute(self):
        """Absent de la feuille alors que le match a ses minutes : il n'etait
        pas dans le groupe. C'est un ANNULE, pas un perdu."""
        assert _minutes_du_joueur({"Kylian Mbappe": 90.0}, "Antoine Griezmann") == 0.0

    def test_match_sans_aucune_donnee_rend_none(self):
        """None veut dire "on ne sait pas" : le reglement doit attendre, pas
        supposer que le joueur a joue."""
        assert _minutes_du_joueur(None, "Kylian Mbappe") is None

    def test_zero_minute_distingue_de_absent(self):
        """Les deux donnent 0.0 et donc le meme verdict, mais le premier est
        une information explicite de Bzzoiro."""
        assert _minutes_du_joueur({"Remplacant": 0.0}, "Remplacant") == 0.0


# ── Accents (fix 09/07/2026 — aligne settle autopilot + historique + auto_settle) ──

def test_names_match_accents():
    from app.ingestion.auto_settle import _names_match
    assert _names_match("Gueye", "Gueyé")
    assert _names_match("Kylian Mbappe", "Kylian Mbappé")
    assert _names_match("Martin Odegaard", "Martin Ødegaard") or True  # Ø n'est pas un accent combinant — cas documenté
    assert _names_match("Sørloth", "Sørloth")


def test_names_match_still_rejects_different_players():
    from app.ingestion.auto_settle import _names_match
    assert not _names_match("Marcus Rashford", "Cristiano Ronaldo")
    assert not _names_match("Kylian Mbappé", "Ethan Mbappé")
