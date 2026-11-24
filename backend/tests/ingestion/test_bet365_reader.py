"""Bet365 : lecture du « Pari personnalisé » (texte de la page, relevé du 24/11/2026)."""
import pytest

from app.ingestion.bet365_reader import (
    Bet365ParseError,
    parse_bet_builder_text,
    to_scrape_result,
)

# Extrait fidèle de la page Man City - Napoli (six premiers joueurs).
PAGE = """UEFA Champions League
Man City V Napoli
Bet Builder
Cotes dynamiques
Joueur décisif
REMPLAÇANT+
Joueur / 5 derniers
9
Erling Haaland
2
2
1
75
Floyd Samba
0
Dexter Oliver
N/A
47
Phil Foden
0
3
0
7
Iliman-Cheikh Ndiaye
0
0
0
10
Rayan Cherki
0
0
1
0
Buteur
1.40
1.85
2.34
2.34
2.53
2.53
Passeur
4.18
3.65
4.35
3.30
3.15
2.14
Décisif
1.24
1.45
1.78
1.61
1.56
1.40
Afficher moins
Joueur qui marque
REMPLAÇANT+
Un des deux joueurs
"""


def test_joueurs_et_cotes_dans_le_meme_ordre():
    m = parse_bet_builder_text(PAGE)
    assert m.players == ["Erling Haaland", "Floyd Samba", "Dexter Oliver", "Phil Foden",
                         "Iliman-Cheikh Ndiaye", "Rayan Cherki"]
    assert dict(zip(m.players, m.goal))["Erling Haaland"] == 1.40
    # 4,18 : la vraie cote passeur de Haaland (l'onglet Buteurs affichait 3,98).
    assert dict(zip(m.players, m.assist))["Erling Haaland"] == 4.18
    assert dict(zip(m.players, m.assist))["Rayan Cherki"] == 2.14
    assert m.avec_sub is True


def test_resultat_etiquete_avec_sub():
    r = to_scrape_result(parse_bet_builder_text(PAGE), 42, "Manchester City", "SSC Napoli", None, "champions_league")
    assert r.bookmaker == "bet365" and r.fixture_id == 42
    assert {p.variant for p in r.goalscorer + r.assist} == {"avec_sub"}
    assert len(r.goalscorer) == len(r.assist) == 6


def test_page_en_anglais():
    en = (PAGE.replace("Joueur décisif", "Score or Assist", 1)
              .replace("Joueur / 5 derniers", "Player / Last 5")
              .replace("\nButeur\n", "\nScore\n").replace("\nPasseur\n", "\nAssist\n")
              .replace("\nDécisif\n", "\nScore or Assist\n"))
    m = parse_bet_builder_text(en)
    assert len(m.players) == 6 and m.goal[0] == 1.40 and m.assist[5] == 2.14


def test_sans_la_mention_remplacant_la_cote_est_sans_sub():
    m = parse_bet_builder_text(PAGE.replace("Joueur décisif\nREMPLAÇANT+\n", "Joueur décisif\n", 1))
    assert m.avec_sub is False


def test_colonnes_decalees_rien_n_est_enregistre():
    """Une cote en moins : celle d'un joueur serait attribuée au suivant."""
    with pytest.raises(Bet365ParseError, match="décalées"):
        parse_bet_builder_text(PAGE.replace("2.53\n2.53\nPasseur", "2.53\nPasseur"))


def test_marche_absent():
    with pytest.raises(Bet365ParseError, match="introuvable"):
        parse_bet_builder_text("UEFA Champions League\nMan City V Napoli\nRésultat\n1.23\n")
