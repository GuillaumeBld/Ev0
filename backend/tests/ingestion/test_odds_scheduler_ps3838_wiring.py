# backend/tests/ingestion/test_odds_scheduler_ps3838_wiring.py
"""OddsScheduler.tick <-> PS3838 : deux corrections apportees au brief initial.

1. Les MatchScrapeResult produits par scrape_ps3838 portent deja le bon
   fixture_id (ancrage par identifiant, voir ps3838/anchor.py) : ils NE
   DOIVENT PAS passer par _match_result_to_fixture (rapprochement par noms),
   qui reecrirait fixture_id et pourrait le rattacher au mauvais match.
2. tick() a un retour anticipe `if not leagues_needed: return 0, []` : PS3838
   n'a pas besoin de ligues reconnues (il travaille par identifiant sur toute
   la categorie football), ce retour ne doit donc jamais l'empecher de tourner.

Session entierement mockee : session.execute est route vers des resultats en
conserve selon l'entite/instruction concernee (Fixture / OddsScrapeState /
CanonicalTeam en SELECT, upsert OddsScrapeState en INSERT) — pas de moteur
sqlite reel, car l'upsert final utilise `postgresql.insert(...).on_conflict_do_update`,
une construction specifique a Postgres que sqlite ne sait pas compiler (voir
tests/ingestion/test_odds_storage.py qui mocke la session pour la meme raison).
Les 3 scrapers existants (betclic/unibet/pmu) et scrape_ps3838 sont mockes au
niveau de leurs modules d'origine, exactement comme ils sont importes
localement dans tick().
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy.sql.dml import Insert

from app.ingestion.odds_scheduler import OddsScheduler
from app.ingestion.scrape_result import MatchScrapeResult
from app.models.canonical_teams import CanonicalTeam
from app.models.fixtures import Fixture
from app.models.odds_scrape_state import OddsScrapeState


def _ko(minutes_from_now: int) -> datetime:
    return datetime.now(UTC) + timedelta(minutes=minutes_from_now)


def _fixture(fid, league, home="PSG", away="Marseille"):
    return SimpleNamespace(
        id=fid,
        league=league,
        home_team=home,
        away_team=away,
        home_canonical_team_id=None,
        away_canonical_team_id=None,
        kickoff_utc=_ko(90),  # dans la fenetre "due", jamais scrape avant
        status="scheduled",
    )


def _router(fixtures, states, canonical_teams):
    """Fabrique un session.execute mocke qui route selon l'entite/instruction."""

    async def _execute(stmt, *_args, **_kwargs):
        if isinstance(stmt, Insert):
            # Upsert OddsScrapeState : on ne verifie pas son contenu ici.
            return SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: []), rowcount=1)
        entity = stmt.column_descriptions[0]["entity"]
        if entity is Fixture:
            rows = fixtures
        elif entity is OddsScrapeState:
            rows = states
        elif entity is CanonicalTeam:
            rows = canonical_teams
        else:
            rows = []
        return SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: rows))

    return _execute


def _mock_session(fixtures, states=(), canonical_teams=()):
    session = AsyncMock()
    session.execute = AsyncMock(side_effect=_router(fixtures, states, canonical_teams))
    session.commit = AsyncMock()
    return session


async def test_ps3838_result_bypasses_name_reconciliation():
    """Le resultat PS3838 doit etre stocke avec SON fixture_id (venant de
    l'ancrage), meme si un rapprochement par noms tourne par ailleurs pour
    betclic/unibet/pmu et que les noms de l'evenement PS3838 ne
    correspondraient a AUCUN candidat de ce rapprochement."""
    fx = _fixture(1, "ligue_1", home="Paris Saint-Germain", away="Marseille")

    # betclic renvoie un resultat dont les noms ne matchent RIEN parmi les
    # fixtures dues -> doit etre ignore par le rapprochement (comportement
    # existant, inchange).
    betclic_result = MatchScrapeResult(
        fixture_id=None, home_team="Equipe Inconnue A", away_team="Equipe Inconnue B",
        kickoff_utc=fx.kickoff_utc, league="ligue_1", bookmaker="betclic",
        scraped_at=datetime.now(UTC),
    )
    # PS3838 renvoie un resultat avec le BON fixture_id mais des noms qui, eux
    # non plus, ne matcheraient rien par rapprochement — la preuve que le
    # bypass fonctionne : seul l'identifiant compte.
    ps3838_result = MatchScrapeResult(
        fixture_id=1, home_team="Nom PS3838 A", away_team="Nom PS3838 B",
        kickoff_utc=fx.kickoff_utc, league="ligue_1", bookmaker="ps3838",
        scraped_at=datetime.now(UTC), h2h={"home": 1.5, "draw": 4.0, "away": 6.0},
        totals={"over_2.5": 1.9, "under_2.5": 1.9},
    )

    session = _mock_session([fx], states=[], canonical_teams=[])

    with (
        patch(
            "app.ingestion.betclic_grpc_scraper.scrape_betclic_leagues",
            new=AsyncMock(return_value=[betclic_result]),
        ),
        patch(
            "app.ingestion.unibet_lvs_scraper.scrape_all_unibet",
            new=AsyncMock(return_value=[]),
        ),
        patch(
            "app.ingestion.pmu_scraper.scrape_all_pmu",
            new=AsyncMock(return_value=[]),
        ),
        patch(
            "app.ingestion.ps3838.scraper.scrape_ps3838",
            new=AsyncMock(return_value=[ps3838_result]),
        ) as ps3838_mock,
        patch(
            "app.ingestion.odds_storage.store_match_scrape_result",
            new=AsyncMock(return_value=(0, 0)),
        ) as store_mock,
    ):
        due_count, stored_fixture_ids = await OddsScheduler().tick(session)

    # scrape_ps3838 recoit bien les fixtures dues (pas de filtrage arbitraire).
    assert ps3838_mock.await_args.args[1] == [1]

    # betclic (noms inconnus) n'a jamais ete stocke.
    stored_bookmakers = [call.args[0].bookmaker for call in store_mock.await_args_list]
    assert "betclic" not in stored_bookmakers

    # ps3838 a ete stocke UNE fois, avec fixture_id INCHANGE (=1, l'ancrage),
    # jamais ecrase par un rapprochement par noms.
    ps3838_calls = [call.args[0] for call in store_mock.await_args_list if call.args[0].bookmaker == "ps3838"]
    assert len(ps3838_calls) == 1
    assert ps3838_calls[0].fixture_id == 1
    assert ps3838_calls[0].home_team == "Nom PS3838 A"  # noms d'origine intacts

    assert due_count == 1
    assert stored_fixture_ids == [1]


async def test_ps3838_runs_even_when_no_recognized_league_is_due():
    """Une fixture dont la ligue n'est pas reconnue par _league_key ne doit
    pas empecher PS3838 de tourner : il n'a pas besoin de ligues, il lit toute
    la categorie football et travaille par identifiant."""
    fx = _fixture(2, "some_unrecognized_league")

    ps3838_result = MatchScrapeResult(
        fixture_id=2, home_team=fx.home_team, away_team=fx.away_team,
        kickoff_utc=fx.kickoff_utc, league="some_unrecognized_league", bookmaker="ps3838",
        scraped_at=datetime.now(UTC), h2h={"home": 1.5, "draw": 4.0, "away": 6.0},
        totals={"over_2.5": 1.9, "under_2.5": 1.9},
    )

    session = _mock_session([fx], states=[], canonical_teams=[])

    with (
        patch(
            "app.ingestion.betclic_grpc_scraper.scrape_betclic_leagues",
            new=AsyncMock(return_value=[]),
        ) as betclic_mock,
        patch(
            "app.ingestion.unibet_lvs_scraper.scrape_all_unibet",
            new=AsyncMock(return_value=[]),
        ) as unibet_mock,
        patch(
            "app.ingestion.pmu_scraper.scrape_all_pmu",
            new=AsyncMock(return_value=[]),
        ) as pmu_mock,
        patch(
            "app.ingestion.ps3838.scraper.scrape_ps3838",
            new=AsyncMock(return_value=[ps3838_result]),
        ),
        patch(
            "app.ingestion.odds_storage.store_match_scrape_result",
            new=AsyncMock(return_value=(0, 0)),
        ) as store_mock,
    ):
        due_count, stored_fixture_ids = await OddsScheduler().tick(session)

    # Avec la correction : le tick ne s'arrete PAS avant PS3838, malgre
    # l'absence de ligue reconnue parmi les fixtures dues.
    assert due_count == 1
    assert stored_fixture_ids == [2]
    assert store_mock.await_count == 1
    assert store_mock.await_args.args[0].bookmaker == "ps3838"

    # Les 3 scrapers par ligue n'ont pas ete appeles : rien a scraper pour eux.
    betclic_mock.assert_not_awaited()
    unibet_mock.assert_not_awaited()
    pmu_mock.assert_not_awaited()


if __name__ == "__main__":
    import sys

    sys.exit(pytest.main([__file__, "-v"]))
