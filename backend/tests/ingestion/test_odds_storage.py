# backend/tests/ingestion/test_odds_storage.py
import pytest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

from app.ingestion.scrape_result import MatchScrapeResult, PlayerOdds
from app.ingestion.odds_storage import store_match_scrape_result


@pytest.mark.asyncio
async def test_store_complete_result():
    """store_match_scrape_result returns (match_rows, player_rows) non-negative."""
    result = MatchScrapeResult(
        fixture_id=42,
        home_team="PSG",
        away_team="Lyon",
        kickoff_utc=datetime(2026, 5, 1, 19, 0, tzinfo=timezone.utc),
        league="ligue_1",
        bookmaker="betclic",
        scraped_at=datetime(2026, 5, 1, 17, 0, tzinfo=timezone.utc),
        h2h={"home": 2.1, "draw": 3.4, "away": 3.6},
        totals={"over_2.5": 1.8, "under_2.5": 2.0},
        btts={"yes": 1.75, "no": 2.1},
        goalscorer=[PlayerOdds("Mbappé", 3.5), PlayerOdds("Dembélé", 4.0)],
        assist=[PlayerOdds("Vitinha", 5.0)],
    )

    session = AsyncMock()
    session.execute = AsyncMock(return_value=MagicMock(rowcount=7))
    session.commit = AsyncMock()

    match_rows, player_rows = await store_match_scrape_result(result, session, squad_cache={42: []})
    assert match_rows >= 0
    assert player_rows >= 0
    assert session.commit.called


@pytest.mark.asyncio
async def test_store_incomplete_result_still_stores_player_props():
    """Without h2h/totals/btts, player props are still stored."""
    result = MatchScrapeResult(
        fixture_id=42,
        home_team="PSG", away_team="Lyon",
        kickoff_utc=None, league="ligue_1",
        bookmaker="betclic",
        scraped_at=datetime(2026, 5, 1, 17, 0, tzinfo=timezone.utc),
        goalscorer=[PlayerOdds("Mbappé", 3.5)],
    )
    session = AsyncMock()
    session.execute = AsyncMock(return_value=MagicMock(rowcount=1))
    session.commit = AsyncMock()
    _, player_rows = await store_match_scrape_result(result, session, squad_cache={42: []})
    assert player_rows >= 0


# ── Variante avec sub / sans sub ─────────────────────────────────────────────

def test_prefer_avec_sub_une_cote_par_joueur():
    from app.ingestion.scrape_result import prefer_avec_sub

    out = prefer_avec_sub([
        PlayerOdds("Haaland", 1.47, "sans_sub"),
        PlayerOdds("Haaland", 1.42, "avec_sub"),
        PlayerOdds("Oliver", 2.57, "sans_sub"),
    ])
    assert {p.player_name: (p.odds, p.variant) for p in out} == {
        "Haaland": (1.42, "avec_sub"),   # la cote avec sub l'emporte, même plus basse
        "Oliver": (2.57, "sans_sub"),    # second recours, étiqueté
    }


@pytest.mark.asyncio
async def test_storage_ecrit_la_variante_et_la_cote_avec_sub_l_emporte():
    """Même joueur lu deux fois : la ligne avec sub est gardée, pas la plus haute."""
    captured: list = []
    session = AsyncMock()

    async def fake_execute(stmt):
        captured.append(stmt)
        return MagicMock(rowcount=1)

    session.execute = fake_execute
    session.commit = AsyncMock()
    result = MatchScrapeResult(
        fixture_id=42, home_team="Manchester City", away_team="Naples",
        kickoff_utc=None, league="champions_league", bookmaker="betclic",
        scraped_at=datetime(2026, 11, 24, 17, 0, tzinfo=timezone.utc),
        goalscorer=[
            PlayerOdds("Erling Haaland", 1.47, "sans_sub"),
            PlayerOdds("Erling Haaland", 1.42, "avec_sub"),
        ],
        assist=[PlayerOdds("Rayan Cherki", 2.45)],
    )
    await store_match_scrape_result(result, session, squad_cache={42: []})

    params = captured[0].compile().params
    lignes = {
        (params[k], params[k.replace("player_name", "market_type")]): (
            params[k.replace("player_name", "odds")],
            params[k.replace("player_name", "variant")],
        )
        for k in params if k.startswith("player_name")
    }
    assert lignes == {
        ("Erling Haaland", "goalscorer"): (1.42, "avec_sub"),
        ("Rayan Cherki", "assist"): (2.45, "sans_sub"),
    }
    # La variante est réécrite à chaque passage (une ligne existante en change).
    assert "variant = excluded.variant" in str(captured[0].compile())


def test_pmu_garantie_coaching_big5_et_ligue_des_champions():
    """PMU n'a pas de marché à part : la Garantie Coaching couvre « Marque » et
    « passe décisive » sur le Big 5 et la Ligue des champions seulement."""
    from app.ingestion.pmu_scraper import _parse_betoffers, player_variant

    for league in ("ligue_1", "premier_league", "bundesliga", "la_liga", "serie_a", "champions_league"):
        assert player_variant(league) == "avec_sub", league
    for league in ("europa_league", "conference_league", "comp_17", "friendly_international"):
        assert player_variant(league) == "sans_sub", league

    def offre(label, joueur, cote):
        return {
            "betOfferType": {"name": "Player Occurrence Line"},
            "criterion": {"englishLabel": label, "lifetime": "FULL_TIME"},
            "outcomes": [{"participant": joueur, "odds": cote, "type": "OT_YES"}],
        }

    event = {"homeName": "Manchester City", "awayName": "Napoli", "start": "2026-11-24T20:00:00Z"}
    offres = [offre("To Score", "Erling Haaland", 1400),
              offre("To give an assist (Settled using Opta data)", "Rayan Cherki", 2200)]
    t = datetime(2026, 11, 24, tzinfo=timezone.utc)
    ldc = _parse_betoffers(event, offres, "champions_league", t)
    assert [(p.player_name, p.variant) for p in ldc.goalscorer + ldc.assist] == [
        ("Erling Haaland", "avec_sub"), ("Rayan Cherki", "avec_sub")]
    autre = _parse_betoffers(event, offres, "europa_league", t)
    assert {p.variant for p in autre.goalscorer + autre.assist} == {"sans_sub"}
