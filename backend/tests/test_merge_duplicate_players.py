"""Tests de la décision de fusion des joueurs dupliqués (garde-fou noms)."""
from app.scripts.merge_duplicate_players import plan_merges


def test_merge_when_names_similar():
    b_rows = [{"api_id": 826643, "internal_id": 594, "name": "Kylian Mbappé"}]
    canonical = {594: "Kylian Mbappé"}
    merges, self_canon = plan_merges(b_rows, canonical)
    assert merges == [(826643, 594)]
    assert self_canon == []


def test_no_merge_on_lying_internal_id():
    # Cas réel : la ligne Rashford pointait internal_id=7880 (= Ronaldo)
    b_rows = [{"api_id": 750, "internal_id": 7880, "name": "Marcus Rashford"}]
    canonical = {7880: "Cristiano Ronaldo"}
    merges, self_canon = plan_merges(b_rows, canonical)
    assert merges == []
    assert self_canon == [750]


def test_self_canonical_when_canonical_missing():
    b_rows = [{"api_id": 999999, "internal_id": 12345, "name": "Joueur Inconnu"}]
    merges, self_canon = plan_merges(b_rows, {})
    assert merges == []
    assert self_canon == [999999]


def test_name_variants_still_merge():
    b_rows = [{"api_id": 800001, "internal_id": 769, "name": "Alex Baena"}]
    canonical = {769: "Alejandro Baena"}
    merges, _ = plan_merges(b_rows, canonical)
    assert merges == [(800001, 769)]
