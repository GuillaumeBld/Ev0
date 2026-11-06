def test_league_groups_disjoint():
    from app.ingestion.bzzoiro.constants import (
        INTL_CLUB_COMP_LEAGUE_IDS, SELECTION_STATS_LEAGUE_IDS,
    )
    assert INTL_CLUB_COMP_LEAGUE_IDS.isdisjoint(SELECTION_STATS_LEAGUE_IDS)
    assert 7 in INTL_CLUB_COMP_LEAGUE_IDS and 27 in SELECTION_STATS_LEAGUE_IDS
