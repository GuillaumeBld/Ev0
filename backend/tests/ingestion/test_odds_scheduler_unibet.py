"""Planificateur et Unibet : accrochage par identifiant, reprise rapide.

Constat du 24/11/2026 : Unibet refuse au serveur la LISTE des matchs d'un
championnat environ 4 fois sur 10, jamais la lecture d'un match précis. Un
passage raté n'était retenté que 2 h plus tard. Désormais :
- l'identifiant Unibet d'un match est retenu au premier rapprochement, puis le
  match est relu par cet identifiant (plus de liste, plus de noms) ;
- un échec Unibet est retenté 5 minutes plus tard, Unibet seul.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from sqlalchemy.sql.dml import Insert

from app.ingestion import odds_scheduler
from app.ingestion.odds_scheduler import OddsScheduler
from app.ingestion.scrape_result import MatchScrapeResult
from app.models.canonical_teams import CanonicalTeam
from app.models.fixtures import Fixture
from app.models.odds_scrape_state import OddsScrapeState


def _fixture(fid, unibet_event_id=None, home="Paris Saint-Germain", away="Marseille"):
    return SimpleNamespace(
        id=fid, league="ligue_1", home_team=home, away_team=away,
        home_canonical_team_id=None, away_canonical_team_id=None,
        kickoff_utc=datetime.now(UTC) + timedelta(hours=30),
        status="scheduled", unibet_event_id=unibet_event_id,
    )


def _session(fixtures, states=()):
    async def _execute(stmt, *_a, **_k):
        if isinstance(stmt, Insert):
            return SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: []), rowcount=1)
        entity = stmt.column_descriptions[0]["entity"]
        rows = {Fixture: fixtures, OddsScrapeState: list(states), CanonicalTeam: []}.get(entity, [])
        return SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: rows))

    session = AsyncMock()
    session.execute = AsyncMock(side_effect=_execute)
    session.commit = AsyncMock()
    return session


def _unibet(fx, event_id, home=None, away=None, fixture_id=0):
    return MatchScrapeResult(
        fixture_id=fixture_id, home_team=home or fx.home_team, away_team=away or fx.away_team,
        kickoff_utc=fx.kickoff_utc, league="ligue_1", bookmaker="unibet",
        scraped_at=datetime.now(UTC), h2h={"home": 1.5, "draw": 4.0, "away": 6.0},
        book_event_id=event_id,
    )


async def _tick(scheduler, session, unibet, betclic=None, pmu=None):
    """Lance un passage ; `unibet` est la fonction qui remplace scrape_all_unibet."""
    betclic = betclic or AsyncMock(return_value=[])
    pmu = pmu or AsyncMock(return_value=[])
    store = AsyncMock(return_value=(0, 0))
    ps3838 = AsyncMock(return_value=[])
    _tick.ps3838 = ps3838
    with (
        patch("app.ingestion.betclic_grpc_scraper.scrape_betclic_leagues", new=betclic),
        patch("app.ingestion.unibet_lvs_scraper.scrape_all_unibet", new=unibet),
        patch("app.ingestion.pmu_scraper.scrape_all_pmu", new=pmu),
        patch("app.ingestion.ps3838.scraper.scrape_ps3838", new=ps3838),
        patch("app.ingestion.odds_storage.store_match_scrape_result", new=store),
    ):
        await scheduler.tick(session)
    return store, betclic


async def test_premier_passage_l_identifiant_unibet_est_retenu():
    fx = _fixture(1)
    unibet = AsyncMock(return_value=[_unibet(fx, 3377083, home="PSG", away="Marseille")])
    # « PSG » : alias connu du planificateur pour Paris Saint-Germain.
    store, _ = await _tick(OddsScheduler(), _session([fx]), unibet)

    appel = unibet.await_args
    assert appel.args[0] == ["ligue_1"]
    assert appel.kwargs["known_events"] == {}
    assert appel.kwargs["wanted_kickoffs"] == {"ligue_1": [fx.kickoff_utc]}
    assert fx.unibet_event_id == 3377083
    assert [c.args[0].fixture_id for c in store.await_args_list] == [1]


async def test_match_accroche_relu_par_identifiant_sans_liste_ni_noms():
    fx = _fixture(1, unibet_event_id=3377083)
    # Noms qui ne ressemblent à rien : seul l'identifiant compte.
    unibet = AsyncMock(return_value=[_unibet(fx, 3377083, home="Equipe X", away="Equipe Y", fixture_id=1)])
    store, _ = await _tick(OddsScheduler(), _session([fx]), unibet)

    appel = unibet.await_args
    assert appel.args[0] == []                                   # aucune liste demandée
    assert appel.kwargs["known_events"] == {3377083: (1, "ligue_1")}
    stored = [c.args[0] for c in store.await_args_list]
    assert [(r.bookmaker, r.fixture_id) for r in stored] == [("unibet", 1)]


async def test_liste_refusee_reprise_cinq_minutes_plus_tard_unibet_seul():
    fx = _fixture(1)
    scheduler = OddsScheduler()

    async def liste_refusee(leagues, *, known_events, wanted_kickoffs, report, **_):
        report.failed_leagues.update(leagues)
        return []

    await _tick(scheduler, _session([fx]), liste_refusee)
    assert 1 in scheduler._unibet_retry

    # Le match vient d'être passé : il n'est plus « à rafraîchir » avant 2 h.
    etat = SimpleNamespace(fixture_id=1, last_scraped_at=datetime.now(UTC))

    # Deux minutes après : trop tôt, rien n'est demandé.
    unibet = AsyncMock(return_value=[])
    await _tick(scheduler, _session([fx], [etat]), unibet)
    unibet.assert_not_awaited()

    # Six minutes après : Unibet est redemandé, et lui seul.
    scheduler._unibet_retry[1] = datetime.now(UTC) - timedelta(minutes=1)
    unibet = AsyncMock(return_value=[_unibet(fx, 3377083, home="PSG", away="Marseille")])
    store, betclic = await _tick(scheduler, _session([fx], [etat]), unibet)
    unibet.assert_awaited_once()
    betclic.assert_not_awaited()
    _tick.ps3838.assert_not_awaited()   # sinon il relirait tous les matchs accrochés
    assert fx.unibet_event_id == 3377083
    assert 1 not in scheduler._unibet_retry
    assert [c.args[0].bookmaker for c in store.await_args_list] == ["unibet"]


async def test_match_absent_chez_unibet_pas_de_reprise_sans_fin():
    """Liste lue, match pas proposé par Unibet : ce n'est pas une panne."""
    fx = _fixture(1)
    scheduler = OddsScheduler()
    await _tick(scheduler, _session([fx]), AsyncMock(return_value=[]))
    assert scheduler._unibet_retry == {}


async def test_match_disparu_l_accrochage_est_defait():
    fx = _fixture(1, unibet_event_id=3377083)
    scheduler = OddsScheduler()

    async def disparu(leagues, *, known_events, wanted_kickoffs, report, **_):
        report.gone_events.update(known_events)
        return []

    await _tick(scheduler, _session([fx]), disparu)
    assert fx.unibet_event_id is None       # sera recherché dans la liste au passage suivant
    assert 1 in scheduler._unibet_retry


async def test_identifiant_qui_designe_un_autre_jour_est_rejete():
    fx = _fixture(1, unibet_event_id=3377083)
    autre_jour = _unibet(fx, 3377083, fixture_id=1)
    autre_jour.kickoff_utc = fx.kickoff_utc + timedelta(days=7)
    store, _ = await _tick(OddsScheduler(), _session([fx]), AsyncMock(return_value=[autre_jour]))
    assert store.await_args_list == []
    assert fx.unibet_event_id is None


async def test_identifiant_deja_pris_par_un_autre_match_jamais_pose_deux_fois():
    a = _fixture(1, home="Paris Saint-Germain", away="Marseille")
    b = _fixture(2, home="Lyon", away="Nice")
    unibet = AsyncMock(return_value=[
        _unibet(a, 500, home="PSG", away="Marseille"),
        _unibet(b, 500, home="Lyon", away="Nice"),
    ])
    await _tick(OddsScheduler(), _session([a, b]), unibet)
    assert (a.unibet_event_id, b.unibet_event_id) == (500, None)


async def test_alerte_apres_trois_listes_refusees_d_affilee():
    fx = _fixture(1)
    scheduler = OddsScheduler()

    async def liste_refusee(leagues, *, known_events, wanted_kickoffs, report, **_):
        report.failed_leagues.update(leagues)
        return []

    with patch("app.alerts.alert_bg") as alerte:
        for _ in range(4):
            scheduler._unibet_retry.clear()
            await _tick(scheduler, _session([fx]), liste_refusee)
    assert alerte.call_count == 1
    assert "ligue_1" in alerte.call_args.args[0]
    assert alerte.call_args.args[1] == "incidents"
    assert odds_scheduler._UNIBET_ALERT_AFTER == 3


async def test_aller_et_retour_l_identifiant_n_est_pose_que_si_l_horaire_colle():
    """Seul le retour est attendu ; Unibet renvoie l'aller, 7 jours plus tôt."""
    retour = _fixture(1, home="Marseille", away="Paris Saint-Germain")
    aller = _unibet(retour, 700, home="PSG", away="Marseille")
    aller.kickoff_utc = retour.kickoff_utc - timedelta(days=7)
    await _tick(OddsScheduler(), _session([retour]), AsyncMock(return_value=[aller]))
    assert retour.unibet_event_id is None


async def test_les_cotes_d_un_autre_championnat_ne_sont_jamais_collees_au_match():
    """Même affiche « Sporting – Benfica » lue dans un autre championnat : pas
    candidate. Avec tout l'Atlas au calendrier, comparer les noms tous
    championnats confondus collait des cotes au mauvais match."""
    fx = _fixture(1, home="Sporting", away="Benfica")
    autre = _unibet(fx, 900, home="Sporting", away="Benfica")
    autre.league = "comp_2"          # le match attendu est en ligue_1 dans ce test
    store, _ = await _tick(OddsScheduler(), _session([fx]), AsyncMock(return_value=[autre]))
    assert store.await_args_list == []
    assert fx.unibet_event_id is None
