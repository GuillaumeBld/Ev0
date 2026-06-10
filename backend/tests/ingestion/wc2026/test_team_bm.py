from app.ingestion.wc2026.team_bm import TEAM_BM, WC2026_NATION_NAME_ALIASES


def test_team_bm_has_48_nations():
    assert len(TEAM_BM) == 48


def test_team_bm_values_positive():
    for nation, bm in TEAM_BM.items():
        assert bm > 0, f"BM for {nation} is {bm}"


def test_team_bm_spain_is_top():
    assert TEAM_BM["Spain"] == max(TEAM_BM.values())


def test_aliases_values_not_in_team_bm():
    for key in WC2026_NATION_NAME_ALIASES:
        assert key in TEAM_BM, f"Alias key {key!r} not in TEAM_BM"
