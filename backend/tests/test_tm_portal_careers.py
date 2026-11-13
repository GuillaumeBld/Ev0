"""Tests de l'import des carrières depuis le portail CSV (`portal_careers.py`)."""
from __future__ import annotations

import hashlib
from unittest.mock import AsyncMock, patch

import pytest

from app.ingestion.transfermarkt import portal_careers as pc

HEADER = (
    "player_id,player_name,season,competition_id,club_id,is_national_team,matches_total,"
    "matches_played,matches_injured_absent,minutes,goals,assists,shots_total,shots_on_goal,yellow_cards"
)


def _rows(*lines: str) -> list[dict[str, str]]:
    import csv
    import io

    return list(csv.DictReader(io.StringIO("\n".join([HEADER, *lines]))))


def test_list_league_codes_reads_download_links_once_each():
    html = '<a href="data/fr1_all.zip">x</a><a href="data/fr1_current.zip">y</a><a href="data/gb1_all.zip">z</a>'
    assert pc.list_league_codes(html) == ["fr1", "gb1"]


def test_verified_csv_rejects_wrong_or_missing_hash():
    raw = b"a,b\n1,2\n"
    good = hashlib.sha256(raw).hexdigest()
    assert pc.verified_csv(raw, good) == "a,b\n1,2\n"
    assert pc.verified_csv(raw, "0" * 64) is None
    assert pc.verified_csv(raw, None) is None


def test_aggregate_sums_clubs_counts_played_only_and_drops_unplayed():
    careers = pc.aggregate_career_rows(_rows(
        # même saison/compétition, deux clubs (transfert d'hiver) -> additionnés
        "7,X,2024,FR1,1,False,10,8,0,600,3,1,9,4,2",
        "7,X,2024,FR1,2,False,5,2,0,100,1,0,2,1,0",
        # convoqué sans jouer -> compétition absente de notre format
        "7,X,2024,EL,1,False,4,0,0,0,0,0,0,0,0",
        "7,X,2023,FS,99,True,3,3,0,200,0,0,0,0,0",
        "8,Y,2024,FR1,1,False,2,0,0,0,0,0,0,0,0",
    ))
    assert set(careers) == {7}  # le joueur 8 n'a aucun match joué
    fr1 = careers[7][(2024, "FR1")]
    assert (fr1["appearances"], fr1["goals"], fr1["assists"], fr1["minutes"]) == (10, 4, 1, 700)
    assert (fr1["total_shots"], fr1["shots_on_target"], fr1["yellow_card"]) == (11, 5, 2)
    assert (2024, "EL") not in careers[7]
    assert careers[7][(2023, "FS")]["_national"] == [True]


def test_season_label_prefers_our_base_then_competition_format():
    labels = pc.SeasonLabels(exact={("BRA1", 2025): "2026"}, calendar={"MLS1": True, "FR1": False})
    assert labels.label("BRA1", 2025) == "2026"
    assert labels.label("MLS1", 2026) == "2027"  # année civile : saison TM 2026 = « 2027 »
    assert labels.label("FR1", 2026) == "26/27"
    assert labels.label("NEW", 2009) == "09/10"  # compétition inconnue : format européen


@pytest.mark.asyncio
async def test_import_creates_full_career_or_refreshes_recent_seasons_only():
    careers = pc.aggregate_career_rows(_rows(
        "1,New,2026,FR1,1,False,5,5,0,450,1,0,1,1,0",
        "1,New,2020,FR2,1,False,5,5,0,450,0,0,0,0,0",
        "2,Old,2026,FR1,1,False,5,5,0,450,2,0,3,2,1",
        "2,Old,2020,FR2,1,False,5,5,0,450,9,9,0,0,0",
        "3,Unknown,2026,FR1,1,False,5,5,0,450,0,0,0,0,0",
    ))
    labels = pc.SeasonLabels(names={"FR1": "Ligue 1"})
    report = pc.PortalImportReport()
    session = AsyncMock()
    with_career = {200}
    with patch.object(pc, "_upsert_season", new=AsyncMock()) as upsert, \
         patch.object(pc, "_refresh_season", new=AsyncMock()) as refresh:
        await pc.import_portal_league(
            session, careers, tm_to_player={1: 100, 2: 200}, with_career=with_career,
            labels=labels, refresh_from_season=2025, report=report,
        )

    # joueur sans carrière -> toutes ses saisons, libellé + nom de compétition
    created = [c.args[1] for c in upsert.await_args_list]
    assert {(v["player_api_id"], v["season"], v["competition_code"]) for v in created} == {
        (100, "26/27", "FR1"), (100, "20/21", "FR2"),
    }
    fr1 = next(v for v in created if v["competition_code"] == "FR1")
    assert fr1["competition"] == "Ligue 1" and fr1["starts"] is None and fr1["red_card"] is None
    # joueur avec carrière -> seule la saison récente, l'ancienne n'est pas touchée
    refreshed = [c.args[1] for c in refresh.await_args_list]
    assert [(v["player_api_id"], v["season_start_year"]) for v in refreshed] == [(200, 2026)]
    assert 100 in with_career
    assert (report.careers_created, report.rows_created, report.players_refreshed, report.players_known) == (1, 2, 1, 2)
    session.commit.assert_awaited()
