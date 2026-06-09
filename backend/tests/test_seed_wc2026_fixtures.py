# backend/tests/test_seed_wc2026_fixtures.py
"""Tests des fonctions utilitaires du seeder (logique pure, sans DB)."""
import itertools


def _normalize_ext_id(name: str) -> str:
    """Duplicate ici pour isoler le test de l'import script."""
    import re, unicodedata
    n = unicodedata.normalize("NFKD", name.lower().strip())
    n = "".join(c for c in n if not unicodedata.combining(c))
    n = re.sub(r"[^a-z0-9]+", "_", n)
    return n.strip("_")


def _generate_group_pairs(group_letter: str, nations: list[str]) -> list[dict]:
    sorted_nations = sorted(nations)
    all_pairs = list(itertools.combinations(sorted_nations, 2))
    round_map = {0: 1, 1: 1, 2: 2, 3: 2, 4: 3, 5: 3}
    result = []
    for idx, (home, away) in enumerate(all_pairs):
        round_num = round_map.get(idx, 1)
        ext_id = f"wc2026_group_{group_letter.lower()}_{_normalize_ext_id(home)}_vs_{_normalize_ext_id(away)}"
        result.append({"external_id": ext_id, "home_team": home, "away_team": away,
                       "round_num": round_num, "group_letter": group_letter})
    return result


def test_generate_group_pairs_count():
    pairs = _generate_group_pairs("A", ["France", "Maroc", "Espagne", "Portugal"])
    assert len(pairs) == 6  # C(4,2) = 6


def test_generate_group_pairs_round_assignment():
    pairs = _generate_group_pairs("X", ["A", "B", "C", "D"])
    rounds = [p["round_num"] for p in pairs]
    assert rounds.count(1) == 2
    assert rounds.count(2) == 2
    assert rounds.count(3) == 2


def test_generate_group_pairs_external_id_unique():
    pairs = _generate_group_pairs("B", ["France", "Brésil", "Argentine", "Allemagne"])
    ext_ids = [p["external_id"] for p in pairs]
    assert len(ext_ids) == len(set(ext_ids))


def test_normalize_ext_id():
    assert _normalize_ext_id("Côte d'Ivoire") == "cote_d_ivoire"
    assert _normalize_ext_id("Bosnia-Herzegovina") == "bosnia_herzegovina"
    assert _normalize_ext_id("USA") == "usa"
