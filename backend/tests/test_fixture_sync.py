"""Tests for fixture sync via The Odds API."""
from app.ingestion.odds import SPORT_KEYS
from app.ingestion.fixture_matcher import normalize_team_name


class TestSportKeys:
    def test_covers_all_six_leagues(self):
        expected = {
            "ligue_1", "premier_league", "bundesliga",
            "la_liga", "serie_a", "champions_league",
        }
        assert set(SPORT_KEYS.keys()) == expected

    def test_bundesliga_key(self):
        assert SPORT_KEYS["bundesliga"] == "soccer_germany_bundesliga"

    def test_la_liga_key(self):
        assert SPORT_KEYS["la_liga"] == "soccer_spain_la_liga"

    def test_serie_a_key(self):
        assert SPORT_KEYS["serie_a"] == "soccer_italy_serie_a"


class TestTeamAliases:
    """Vérifie que les noms The Odds API normalisent vers les noms DB."""

    def test_athletic_bilbao_normalizes(self):
        # The Odds API retourne "Athletic Bilbao", DB a "Athletic Club"
        assert normalize_team_name("Athletic Bilbao") == normalize_team_name("Athletic Club")

    def test_inter_milan_normalizes(self):
        assert normalize_team_name("Inter Milan") == normalize_team_name("Inter")

    def test_ac_milan_normalizes(self):
        assert normalize_team_name("AC Milan") == normalize_team_name("Milan")

    def test_pisa_normalizes(self):
        # The Odds API: "Pisa" / DB peut avoir "AC Pisa 1909"
        assert normalize_team_name("AC Pisa 1909") == normalize_team_name("Pisa")

    def test_bayer_leverkusen_normalizes(self):
        assert normalize_team_name("Bayer 04 Leverkusen") == normalize_team_name("Bayer Leverkusen")
