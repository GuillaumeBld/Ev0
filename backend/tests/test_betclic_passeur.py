"""Betclic pure passeur ("Joueur passeur décisif") via the player-props template.

The pure passeur market is absent from the default GetMatchWithNotification
response; it arrives only when the request carries field 3 = the props template
code. The classifier must accept the pure market and reject every combined /
boosted variant that also contains "passeur décisif".
"""
from app.ingestion.betclic_grpc_scraper import (
    BETCLIC_PROPS_TEMPLATE,
    _classify_market,
    encode_grpc_web_request,
)


def test_pure_passeur_classified_as_assist():
    assert _classify_market("Joueur passeur décisif (tps rég.)") == "assist"


def test_combined_and_boosted_passeur_variants_rejected():
    # None of these are a pure assist market — must NOT be "assist".
    for name in (
        "Joueur décisif (buteur ou passeur décisif dans le t. rég)",
        "Buteur et passeur décisif",
        "Double chance - Passeur décisif",
        "Triple chance - Passeur décisif",
        "Passeur décisif (t. rég) - Extra gains à chaque passe décisive du joueur",
        "Joueur passeur décisif + son remplaçant (t. rég)",
    ):
        assert _classify_market(name) != "assist", name


def test_goalscorer_still_classified():
    assert _classify_market("Buteur ou son remplaçant (t. rég)") == "goalscorer"


def test_props_template_adds_field3():
    base = encode_grpc_web_request(123)
    with_tmpl = encode_grpc_web_request(123, template=BETCLIC_PROPS_TEMPLATE)
    assert len(with_tmpl) > len(base)
    # field 3 (0x1a) carrying the template bytes must be present
    assert b"\x1a" + bytes([len(BETCLIC_PROPS_TEMPLATE)]) + BETCLIC_PROPS_TEMPLATE.encode() in with_tmpl
