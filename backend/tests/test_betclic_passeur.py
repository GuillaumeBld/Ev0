"""Betclic : marchés buteur / passeur, avec ou sans remplaçant.

Règle du 23/11/2026 : on veut la cote « ou son remplaçant » (avec sub) ; la
cote sans sub n'est qu'un second recours. Les trois réponses de
tests/fixtures/betclic_city_naples_*.bin.gz sont les vraies réponses de
Betclic pour Manchester City - Naples (24/11/2026), une par onglet du site :
défaut, SuperSub, Stats équipes et joueurs.
"""
import gzip
from pathlib import Path

import pytest

from app.ingestion.betclic_grpc_scraper import (
    BETCLIC_PROPS_TEMPLATE,
    BETCLIC_SUPERSUB_TEMPLATE,
    BetclicGrpcScraper,
    _classify_market,
    _classify_player_market,
    _parse_match_proto,
    encode_grpc_web_request,
)
from app.ingestion.scrape_result import AVEC_SUB, SANS_SUB

FIXTURES = Path(__file__).parent / "fixtures"


def _reponse(onglet: str) -> bytes:
    return gzip.decompress((FIXTURES / f"betclic_city_naples_{onglet}.bin.gz").read_bytes())


def _cotes(liste) -> dict[str, tuple[float, str]]:
    return {p.player_name: (p.odds, p.variant) for p in liste}


# ── Classement des marchés par leur nom ──────────────────────────────────────

@pytest.mark.parametrize("nom, attendu", [
    ("Buteur ou son remplaçant (t. rég)", ("goalscorer", AVEC_SUB)),
    ("Buteur ou son remplaçant (tps rég.)", ("goalscorer", AVEC_SUB)),
    ("Buteur (t. rég)", ("goalscorer", SANS_SUB)),
    ("Buteur (tps rég.)", ("goalscorer", SANS_SUB)),
    ("Joueur passeur décisif + son remplaçant (t. rég)", ("assist", AVEC_SUB)),
    ("Joueur passeur décisif (tps rég.)", ("assist", SANS_SUB)),
])
def test_marches_joueur_reconnus_avec_leur_variante(nom, attendu):
    assert _classify_player_market(nom) == attendu
    assert _classify_market(nom) == attendu[0]


@pytest.mark.parametrize("nom", [
    "Buteur + son remplaçant (t. rég)",  # 2 buts ou +
    "Buteur (t. rég) - Extra gains à chaque but du joueur",
    "Double chance buteur (t. rég)",
    "Joueur ou son remplaçant 1er buteur (t. rég)",
    "Joueur décisif (buteur ou passeur décisif dans le t. rég)",
    "Joueur décisif (buteur ou passeur décisif) + son remplaçant (t. rég)",
    "Buteur et passeur décisif",
    "Buteur et passeur décisif (titulaire et remplaçant cumulés - t. rég)",
    "Double chance - Passeur décisif",
    "Triple chance - Passeur décisif",
    "Passeur décisif (t. rég) - Extra gains à chaque passe décisive du joueur",
    # Régression 09/09/2026 : passeur ET résultat du match, cotes ~2x plus hautes.
    "Passeur décisif (tps rég.)",
])
def test_autres_paris_joueur_jamais_pris_pour_buteur_ou_passeur(nom):
    assert _classify_market(nom) is None, nom


# ── Lecture des vraies réponses ──────────────────────────────────────────────

def test_onglet_defaut_buteur_avec_remplacant_prioritaire():
    buteurs = _cotes(_parse_match_proto(_reponse("defaut"), "Manchester City", "Naples")["goalscorer"])
    # Haaland : 1,42 avec remplaçant (1,47 sans) -> c'est la cote avec sub qui reste.
    assert buteurs["Erling Haaland"] == (1.42, AVEC_SUB)
    assert buteurs["Rayan Cherki"] == (2.4, AVEC_SUB)
    # Un joueur absent du marché avec remplaçant garde sa cote sans sub, étiquetée.
    assert {v for _, v in buteurs.values()} == {AVEC_SUB, SANS_SUB}
    assert sum(1 for _, v in buteurs.values() if v == SANS_SUB) < 10


def test_onglet_supersub_passeur_avec_remplacant():
    lu = _parse_match_proto(_reponse("supersub"), "Manchester City", "Naples")
    passeurs = _cotes(lu["assist"])
    # Colonne « 1 fois ou + » uniquement (Cherki 2,20 ; « 2 fois ou + » = 7,50).
    assert passeurs["Rayan Cherki"] == (2.2, AVEC_SUB)
    assert passeurs["Kevin De Bruyne"] == (4.75, AVEC_SUB)
    assert {v for _, v in passeurs.values()} == {AVEC_SUB}


def test_buteur_et_resultat_du_match_ne_remplace_jamais_le_buteur():
    """L'onglet SuperSub contient DEUX marchés nommés « Buteur ou son
    remplaçant » : le vrai (Haaland 1,42) et « & son équipe gagne » (1,57,
    Højlund 14,50). Le second ne doit jamais être lu."""
    buteurs = _cotes(_parse_match_proto(_reponse("supersub"), "Manchester City", "Naples")["goalscorer"])
    assert buteurs["Erling Haaland"] == (1.42, AVEC_SUB)
    assert buteurs["Rasmus Højlund"] == (3.9, AVEC_SUB)


def test_onglet_stats_passeur_sans_remplacant_et_pas_le_passeur_resultat():
    passeurs = _cotes(_parse_match_proto(_reponse("props"), "Manchester City", "Naples")["assist"])
    assert passeurs["Rayan Cherki"] == (2.45, SANS_SUB)
    # « Passeur décisif (tps rég.) » = passeur ET son équipe gagne : De Bruyne
    # y est à 18,75. Sa vraie cote passeur est 5,00.
    assert passeurs["Kevin De Bruyne"] == (5.0, SANS_SUB)


# ── Enchaînement des appels ──────────────────────────────────────────────────

def _scraper(monkeypatch, reponses: dict[str | None, bytes]) -> tuple[BetclicGrpcScraper, list]:
    appels: list[str | None] = []
    corps = {encode_grpc_web_request(1, "fr", template=t) if t else encode_grpc_web_request(1, "fr"): t
             for t in (None, BETCLIC_SUPERSUB_TEMPLATE, BETCLIC_PROPS_TEMPLATE)}

    async def faux_appel(client, url, body, timeout):
        onglet = corps[body]
        appels.append(onglet)
        return reponses.get(onglet, b"")

    monkeypatch.setattr("app.ingestion.betclic_grpc_scraper._stream_first_grpc_frame", faux_appel)
    return BetclicGrpcScraper(client=None), appels


async def test_match_avec_supersub_deux_appels_tout_avec_sub(monkeypatch):
    scraper, appels = _scraper(monkeypatch, {
        None: _reponse("defaut"),
        BETCLIC_SUPERSUB_TEMPLATE: _reponse("supersub"),
        BETCLIC_PROPS_TEMPLATE: _reponse("props"),
    })
    r = await scraper.fetch_match_odds(1, "Manchester City", "Naples", "champions_league")
    # Le passeur avec sub existe : l'onglet Stats (sans sub) n'est pas demandé.
    assert appels == [None, BETCLIC_SUPERSUB_TEMPLATE]
    assert _cotes(r.assist)["Rayan Cherki"] == (2.2, AVEC_SUB)
    assert _cotes(r.goalscorer)["Erling Haaland"] == (1.42, AVEC_SUB)
    assert r.h2h is not None


async def test_match_sans_supersub_second_recours_sans_sub(monkeypatch):
    """Petit championnat : pas d'onglet SuperSub -> passeur sans sub, étiqueté."""
    scraper, appels = _scraper(monkeypatch, {
        None: _reponse("defaut"),
        BETCLIC_PROPS_TEMPLATE: _reponse("props"),
    })
    r = await scraper.fetch_match_odds(1, "Manchester City", "Naples", "champions_league")
    assert appels == [None, BETCLIC_SUPERSUB_TEMPLATE, BETCLIC_PROPS_TEMPLATE]
    assert _cotes(r.assist)["Rayan Cherki"] == (2.45, SANS_SUB)


def test_props_template_adds_field3():
    base = encode_grpc_web_request(123)
    with_tmpl = encode_grpc_web_request(123, template=BETCLIC_PROPS_TEMPLATE)
    assert len(with_tmpl) > len(base)
    # field 3 (0x1a) carrying the template bytes must be present
    assert b"\x1a" + bytes([len(BETCLIC_PROPS_TEMPLATE)]) + BETCLIC_PROPS_TEMPLATE.encode() in with_tmpl
