"""Cotes bookmakers reliées au joueur Atlas (tâche 8).

Le résolveur est pur : il prend le nom donné par le bookmaker et l'effectif
Atlas des deux équipes du match, et rend l'api_id du joueur — ou None
quand le nom est absent ou ambigu. Jamais de rapprochement approximatif.
"""
from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.player_identity import SquadPlayer, resolve_odds_player

# ── Effectifs de test ────────────────────────────────────────────────────────

PSG = [
    SquadPlayer(api_id=1, name="Kylian Mbappé"),
    SquadPlayer(api_id=2, name="Lucas Hernández"),
    SquadPlayer(api_id=3, name="Theo Hernández"),
    SquadPlayer(api_id=4, name="Ousmane Dembélé"),
]

# Man City – Napoli du 23/11 : formes réellement vues chez les bookmakers.
CITY_NAPOLI = [
    SquadPlayer(api_id=10, name="Phil Foden"),
    SquadPlayer(api_id=11, name="Jérémy Doku"),
    SquadPlayer(api_id=12, name="Rasmus Højlund"),
    SquadPlayer(api_id=13, name="Allan Elias"),
    SquadPlayer(api_id=14, name="Floyd Samba"),
    SquadPlayer(api_id=15, name="Tyrone Samba"),
    SquadPlayer(api_id=16, name="Dexter Oliver"),
]


# ── Cas du brief ─────────────────────────────────────────────────────────────

def test_nom_exact():
    assert resolve_odds_player("Kylian Mbappé", PSG) == 1


def test_initiale_plus_nom():
    assert resolve_odds_player("K. Mbappé", PSG) == 1


def test_initiale_departage_deux_homonymes():
    assert resolve_odds_player("T. Hernández", PSG) == 3
    assert resolve_odds_player("L. Hernandez", PSG) == 2


def test_nom_seul_ambigu_rend_none():
    assert resolve_odds_player("Hernández", PSG) is None


def test_sans_accent():
    assert resolve_odds_player("Kylian Mbappe", PSG) == 1
    assert resolve_odds_player("Mbappe", PSG) == 1


def test_nom_absent():
    assert resolve_odds_player("Lionel Messi", PSG) is None
    assert resolve_odds_player("", PSG) is None
    assert resolve_odds_player("Kylian Mbappé", []) is None


# ── Formes réelles Man City – Napoli ─────────────────────────────────────────

def test_forme_nom_virgule_prenom():
    assert resolve_odds_player("Samba, Floyd", CITY_NAPOLI) == 14
    assert resolve_odds_player("Oliver, Dexter", CITY_NAPOLI) == 16


def test_deux_samba():
    assert resolve_odds_player("Samba", CITY_NAPOLI) is None
    assert resolve_odds_player("F. Samba", CITY_NAPOLI) == 14
    assert resolve_odds_player("T. Samba", CITY_NAPOLI) == 15


def test_allan_trois_formes():
    assert resolve_odds_player("Allan", CITY_NAPOLI) == 13               # PMU
    assert resolve_odds_player("Allan Elias", CITY_NAPOLI) == 13         # Betclic
    assert resolve_odds_player("Allan Andrade Elias", CITY_NAPOLI) == 13  # Unibet


def test_lettre_scandinave():
    assert resolve_odds_player("Rasmus Hojlund", CITY_NAPOLI) == 12
    assert resolve_odds_player("Rasmus Højlund", CITY_NAPOLI) == 12


def test_prenom_long_et_court():
    assert resolve_odds_player("Philip Foden", CITY_NAPOLI) == 10
    assert resolve_odds_player("Phil Foden", CITY_NAPOLI) == 10


def test_accent_prenom():
    assert resolve_odds_player("Jeremy Doku", CITY_NAPOLI) == 11


def test_prenom_incompatible_rend_none():
    # Même nom de famille, prénom sans rapport : on ne devine pas.
    assert resolve_odds_player("Kevin Doku", CITY_NAPOLI) is None


def test_meme_nom_dans_les_deux_equipes_ambigu():
    squad = [SquadPlayer(1, "Mohamed Salah"), SquadPlayer(2, "Mohamed Salah")]
    assert resolve_odds_player("Mohamed Salah", squad) is None


# ── Noms Atlas d'un seul mot (Bzzoiro : Neto, Danilo, Marcos, Allan...) ─────

def test_nom_atlas_un_mot_jamais_pris_dans_un_nom_plus_long():
    squad = [SquadPlayer(30, "Neto"), SquadPlayer(31, "Cole Palmer")]
    assert resolve_odds_player("Pedro Neto", squad) is None
    assert resolve_odds_player("Neto", squad) == 30

    squad = [SquadPlayer(40, "Marcos"), SquadPlayer(41, "Cole Palmer")]
    assert resolve_odds_player("Marcos Senesi", squad) is None
    assert resolve_odds_player("Marcos", squad) == 40


def test_allan_atlas_un_mot():
    squad = [SquadPlayer(50, "Allan"), SquadPlayer(51, "Floyd Samba")]
    assert resolve_odds_player("Allan", squad) == 50          # PMU : identique
    # Même forme que « Marcos Senesi » : on ne peut pas trancher sans risque,
    # la cote reste non reliée (comptée, alertée) plutôt que mal reliée.
    assert resolve_odds_player("Allan Andrade Elias", squad) is None


# ── Noms composés, initiale finale, garde Ian/Christian ─────────────────────

def test_noms_de_famille_composes():
    squad = [
        SquadPlayer(60, "Kevin De Bruyne"),
        SquadPlayer(61, "Micky van de Ven"),
        SquadPlayer(62, "Giovanni Di Lorenzo"),
    ]
    assert resolve_odds_player("K. De Bruyne", squad) == 60
    assert resolve_odds_player("De Bruyne", squad) == 60
    assert resolve_odds_player("Micky Van De Ven", squad) == 61
    assert resolve_odds_player("Van de Ven", squad) == 61
    assert resolve_odds_player("Di Lorenzo, Giovanni", squad) == 62
    assert resolve_odds_player("G. Di Lorenzo", squad) == 62


def test_initiale_en_fin_de_nom():
    assert resolve_odds_player("MBAPPE K.", PSG) == 1
    assert resolve_odds_player("HERNANDEZ T.", PSG) == 3


def test_prefixe_pas_sous_chaine():
    squad = [SquadPlayer(70, "Christian Pulisic")]
    assert resolve_odds_player("Ian Pulisic", squad) is None


# ── Écriture des cotes : player_api_id renseigné ─────────────────────────────

def _scrape_result(names: list[str]):
    from app.ingestion.scrape_result import MatchScrapeResult, PlayerOdds

    return MatchScrapeResult(
        fixture_id=42,
        home_team="Manchester City", away_team="Napoli",
        kickoff_utc=None, league="champions_league",
        bookmaker="unibet",
        scraped_at=datetime(2026, 11, 23, 17, 0, tzinfo=UTC),
        goalscorer=[PlayerOdds(n, 3.0) for n in names],
    )


@pytest.mark.asyncio
async def test_storage_ecrit_player_api_id():
    from app.ingestion import odds_storage

    captured: list = []
    session = AsyncMock()

    async def fake_execute(stmt):
        captured.append(stmt)
        return MagicMock(rowcount=1)

    session.execute = fake_execute
    session.commit = AsyncMock()

    loader = AsyncMock(return_value=CITY_NAPOLI)
    with patch.object(odds_storage, "load_fixture_squad", loader):
        cache: dict = {}
        await odds_storage.store_match_scrape_result(
            _scrape_result(["Samba, Floyd", "Samba", "Philip Foden"]), session, squad_cache=cache,
        )
        # Deuxième bookmaker, même match : l'effectif n'est pas rechargé.
        await odds_storage.store_match_scrape_result(
            _scrape_result(["Phil Foden"]), session, squad_cache=cache,
        )

    assert loader.await_count == 1
    params = captured[0].compile().params
    by_name = {
        params[k]: params[k.replace("player_name", "player_api_id")]
        for k in params if k.startswith("player_name")
    }
    assert by_name == {"Samba, Floyd": 14, "Samba": None, "Philip Foden": 10}
    # Le nom du bookmaker reste stocké tel quel.
    assert "Samba, Floyd" in by_name


def test_player_row_porte_player_api_id():
    from app.ingestion.odds_storage import _player_row

    r = _scrape_result([])
    from app.ingestion.scrape_result import PlayerOdds

    row = _player_row(r, "goalscorer", PlayerOdds("F. Samba", 3.0, "avec_sub"), r.scraped_at, CITY_NAPOLI)
    assert row["player_api_id"] == 14
    assert row["player_name"] == "F. Samba"
    assert row["variant"] == "avec_sub"


def test_effectif_meme_source_que_le_calculateur():
    """Club = club_squad_filter ; international = national_team_api_id (comme le pricing)."""
    import inspect

    from app.services import player_identity

    src = inspect.getsource(player_identity.load_fixture_squad)
    assert "club_squad_filter" in src
    assert "national_team_api_id" in src
    assert "is_international_fixture" in src
    assert "loan_team_api_id" not in src


# ── Recos : appariement par player_api_id ────────────────────────────────────

def _alloc(player_id: int, name: str, team: str = "Manchester City"):
    return SimpleNamespace(
        player_id=player_id, player_name=name, team=team, position="FW",
        fair_odds_goal=2.0, prob_goal=0.5, lambda_total=0.69, has_form_goal=True,
        fair_odds_goal_supersub=2.0, p_goal_supersub=0.5,
        fair_odds_assist=4.0, prob_assist=0.25, lambda_assist=0.29, has_form_assist=True,
        fair_odds_assist_supersub=4.0, p_assist_supersub=0.25,
        matches_played=12, is_pen_taker=False,
    )


def _odds(name: str, api_id: int | None, odds: float = 3.0, bookmaker: str = "unibet",
          variant: str = "sans_sub"):
    o = MagicMock()
    o.player_name = name
    o.player_api_id = api_id
    o.market_type = "goalscorer"
    o.variant = variant
    o.odds = odds
    o.bookmaker = bookmaker
    o.scraped_at = datetime.now(UTC)
    return o


async def _run_process(odds_rows, allocs, existing_recs=None, reset_memo=True):
    from app.services import recommendation_service
    from app.services.recommendation_service import process_scraped_fixtures

    if reset_memo:
        recommendation_service._unresolved_alerted.clear()

    fixture = MagicMock()
    fixture.id = 42
    fixture.home_team = "Manchester City"
    fixture.away_team = "Napoli"
    fixture.kickoff_utc = datetime(2026, 11, 23, 20, 0, tzinfo=UTC)

    pricing = MagicMock()
    pricing.home_match_xg = 1.8
    pricing.away_match_xg = 1.0
    pricing.xg_source = "market_implied"
    pricing.home_players = allocs
    pricing.away_players = []

    session = AsyncMock()
    added: list = []
    session.add = lambda rec: added.append(rec)
    session.commit = AsyncMock()
    async def fake_execute(stmt):
        # Aiguillage par table lue, pas par ordre d'appel.
        from app.models.fixtures import Fixture
        from app.models.player_odds_snapshot import PlayerOddsSnapshot
        from app.models.recommendations import Recommendation

        res = MagicMock()
        try:
            entity = stmt.column_descriptions[0]["entity"]
        except Exception:
            entity = None
        rows = {
            Fixture: [fixture],
            Recommendation: existing_recs or [],
            PlayerOddsSnapshot: odds_rows,
        }.get(entity, [])
        res.scalars.return_value.all.return_value = rows
        return res

    session.execute = fake_execute
    alert = MagicMock()
    with patch(
        "app.services.recommendation_service.load_match_pricing",
        new=AsyncMock(return_value=pricing),
    ), patch("app.services.recommendation_service.alert_bg", alert), patch(
        "app.notifications.notify_value_movements", new=AsyncMock(),
    ):
        stats = await process_scraped_fixtures([42], session)
    return stats, added, alert


@pytest.mark.asyncio
async def test_reco_appariee_par_id_et_player_id_renseigne():
    allocs = [_alloc(14, "Floyd Samba"), _alloc(15, "Tyrone Samba")]
    rows = [_odds("Samba, Floyd", 14)]
    stats, added, alert = await _run_process(rows, allocs)

    player_recs = [r for r in added if r.market_type != "h2h"]
    assert player_recs, "une value sur Floyd Samba doit produire une reco"
    assert {r.player_id for r in player_recs} == {14}
    # Nom stocké = nom Atlas (allocation), stable quel que soit le bookmaker.
    assert {r.player_name for r in player_recs} == {"Floyd Samba"}
    alert.assert_not_called()


@pytest.mark.asyncio
async def test_meilleure_cote_regroupee_par_joueur_entre_bookmakers():
    allocs = [_alloc(10, "Phil Foden")]
    rows = [
        _odds("Philip Foden", 10, odds=3.0, bookmaker="unibet"),
        _odds("Phil Foden", 10, odds=3.4, bookmaker="betclic"),
    ]
    _, added, _ = await _run_process(rows, allocs)
    std = [r for r in added if r.market_type == "standard"]
    assert len(std) == 1
    assert std[0].best_odds == 3.4
    assert std[0].best_bookmaker == "betclic"


# ── Recos : cote avec sub / sans sub ─────────────────────────────────────────

def _alloc_sub(player_id: int, name: str):
    """Cote Ev0 sans sub 2,50 ; avec sub 2,00 (le remplaçant ajoute des chances)."""
    a = _alloc(player_id, name)
    a.fair_odds_goal, a.prob_goal = 2.5, 0.4
    a.fair_odds_goal_supersub, a.p_goal_supersub = 2.0, 0.5
    return a


@pytest.mark.asyncio
async def test_cote_avec_sub_jugee_contre_notre_cote_avec_sub_seulement():
    _, added, _ = await _run_process(
        [_odds("Phil Foden", 10, odds=2.4, bookmaker="betclic", variant="avec_sub")],
        [_alloc_sub(10, "Phil Foden")],
    )
    recs = [r for r in added if r.market_type != "h2h"]
    assert [r.market_type.value for r in recs] == ["supersub"]
    assert recs[0].fair_odds == 2.0


@pytest.mark.asyncio
async def test_cote_sans_sub_jamais_jugee_contre_notre_cote_avec_sub():
    """2,40 sans sub : sous notre 2,50 sans sub -> aucune reco. Avant, cette
    cote était aussi comparée à notre 2,00 avec sub : fausse value de 20 %."""
    _, added, _ = await _run_process(
        [_odds("Phil Foden", 10, odds=2.4, bookmaker="unibet", variant="sans_sub")],
        [_alloc_sub(10, "Phil Foden")],
    )
    assert not [r for r in added if r.market_type != "h2h"]


@pytest.mark.asyncio
async def test_cote_sans_sub_en_value_donne_une_reco_sans_sub():
    _, added, _ = await _run_process(
        [_odds("Phil Foden", 10, odds=3.0, bookmaker="unibet", variant="sans_sub")],
        [_alloc_sub(10, "Phil Foden")],
    )
    recs = [r for r in added if r.market_type != "h2h"]
    assert [r.market_type.value for r in recs] == ["standard"]
    assert recs[0].fair_odds == 2.5


@pytest.mark.asyncio
async def test_cote_sans_sub_plus_haute_ne_masque_pas_la_cote_avec_sub():
    _, added, _ = await _run_process(
        [
            _odds("Phil Foden", 10, odds=2.4, bookmaker="betclic", variant="avec_sub"),
            _odds("Philip Foden", 10, odds=3.0, bookmaker="unibet", variant="sans_sub"),
        ],
        [_alloc_sub(10, "Phil Foden")],
    )
    par_marche = {r.market_type.value: (r.best_odds, r.best_bookmaker)
                  for r in added if r.market_type != "h2h"}
    assert par_marche == {"supersub": (2.4, "betclic"), "standard": (3.0, "unibet")}


@pytest.mark.asyncio
async def test_match_sans_aucune_cote_de_book_n_est_pas_calcule():
    """Tout l'Atlas est au calendrier : un match qu'aucun book ne cote ne
    déclenche ni calcul de cotes ni reco."""
    from app.services import recommendation_service as rs

    pricing_mock = AsyncMock()
    with patch.object(rs, "load_match_pricing", pricing_mock):
        stats, added, _ = await _run_process([], [_alloc(10, "Phil Foden")])
    assert stats.get("sans_books") == 1
    assert added == []


@pytest.mark.asyncio
async def test_cote_non_reliee_pas_de_reco_et_alerte_au_dela_de_10pc():
    allocs = [_alloc(14, "Floyd Samba"), _alloc(15, "Tyrone Samba")]
    rows = [_odds("Samba", None), _odds("Samba, Floyd", 14)]
    _, added, alert = await _run_process(rows, allocs)

    assert all(r.player_id == 14 for r in added if r.market_type != "h2h")
    alert.assert_called_once()
    message, channel = alert.call_args.args
    assert channel == "incidents"
    assert "Samba" in message
    assert "Manchester City" in message


@pytest.mark.asyncio
async def test_vieille_cote_non_reliee_ne_declenche_pas_d_alerte():
    from datetime import timedelta

    allocs = [_alloc(14, "Floyd Samba")]
    stale = _odds("Samba", None)
    stale.scraped_at = datetime.now(UTC) - timedelta(days=3)
    _, _, alert = await _run_process([stale, _odds("Samba, Floyd", 14)], allocs)
    alert.assert_not_called()


@pytest.mark.asyncio
async def test_alerte_une_seule_fois_par_lot_de_noms():
    allocs = [_alloc(14, "Floyd Samba"), _alloc(15, "Tyrone Samba")]
    rows = [_odds("Samba", None), _odds("Samba, Floyd", 14)]
    _, _, alert1 = await _run_process(rows, allocs)
    # Passage suivant, même lot de noms, compteurs différents : pas de réalerte.
    rows2 = [_odds("Samba", None), _odds("Samba", None, bookmaker="pmu"), _odds("Samba, Floyd", 14)]
    _, _, alert2 = await _run_process(rows2, allocs, reset_memo=False)
    # Nouveau nom non relié : nouvelle alerte.
    rows3 = rows2 + [_odds("Inconnu X", None)]
    _, _, alert3 = await _run_process(rows3, allocs, reset_memo=False)
    assert alert1.call_count == 1
    assert alert2.call_count == 0
    assert alert3.call_count == 1


@pytest.mark.asyncio
async def test_chemin_api_n_alerte_pas():
    from app.services import recommendation_service as rs

    fixture_orm = MagicMock()
    fixture_orm.external_id = "ext1"
    pricing = MagicMock()
    pricing.home_match_xg, pricing.away_match_xg = 1.8, 1.0
    pricing.xg_source = "market_implied"
    pricing.home_players, pricing.away_players = [_alloc(14, "Floyd Samba")], []
    odds = {"ext1": [{"player_name": "Samba", "player_api_id": None, "odds": 3.0,
                      "market_type": "goalscorer", "bookmaker": "pmu"}]}
    alert = MagicMock()
    with patch.object(rs, "load_match_pricing", new=AsyncMock(return_value=pricing)), \
            patch.object(rs, "alert_bg", alert):
        await rs.generate_recommendations(
            [{"fixture_id": "ext1", "home_team": "A", "away_team": "B"}],
            odds, db=AsyncMock(), db_fixtures=[fixture_orm],
        )
    alert.assert_not_called()


def test_chemin_api_une_cote_par_joueur_avec_sub_d_abord():
    """Cherki passeur : Betclic 3,90 avec sub + Unibet 4,60 sans sub. Deux
    entrées pour le même joueur faisaient échouer l'enregistrement de tout le
    lot de recos (clé unique joueur x marché)."""
    from app.services.recommendation_service import _one_odd_per_player

    def e(nom, pid, cote, book, variante, marche="assist"):
        return {"player_name": nom, "player_api_id": pid, "market_type": marche,
                "variant": variante, "odds": cote, "bookmaker": book}

    out = _one_odd_per_player([
        e("Rayan Cherki", 7, 3.9, "betclic", "avec_sub"),
        e("Cherki, Rayan", 7, 4.6, "unibet", "sans_sub"),
        e("Rayan Cherki", 7, 4.1, "pmu", "avec_sub"),
        e("Phil Foden", 10, 3.3, "unibet", "sans_sub"),
        e("Rayan Cherki", 7, 2.4, "betclic", "avec_sub", marche="goalscorer"),
    ])
    lu = {(x["player_api_id"], x["market_type"]): (x["odds"], x["bookmaker"], x["variant"]) for x in out}
    assert lu == {
        (7, "assist"): (4.1, "pmu", "avec_sub"),          # meilleure cote AVEC sub, pas le 4,60 sans sub
        (10, "assist"): (3.3, "unibet", "sans_sub"),      # second recours
        (7, "goalscorer"): (2.4, "betclic", "avec_sub"),
    }
    assert len(out) == 3


@pytest.mark.asyncio
@pytest.mark.parametrize("variante, cote_ev0", [("avec_sub", 2.0), ("sans_sub", 2.5)])
async def test_chemin_api_cote_jugee_contre_la_meme_variante(variante, cote_ev0):
    from app.services import recommendation_service as rs

    fixture_orm = MagicMock()
    fixture_orm.external_id = "ext1"
    pricing = MagicMock()
    pricing.home_match_xg, pricing.away_match_xg = 1.8, 1.0
    pricing.xg_source = "market_implied"
    joueur = _alloc_sub(10, "Phil Foden")
    joueur.expected_minutes = 80.0
    pricing.home_players, pricing.away_players = [joueur], []
    odds = {"ext1": [{"player_name": "Phil Foden", "player_api_id": 10, "odds": 3.0,
                      "market_type": "goalscorer", "bookmaker": "betclic", "variant": variante}]}
    captured: list = []

    def fake_select(recs, cfg):
        captured.extend(recs)
        return SimpleNamespace(selected=recs)

    with patch.object(rs, "load_match_pricing", new=AsyncMock(return_value=pricing)), \
            patch.object(rs, "select_bets", fake_select), patch.object(rs, "alert_bg", MagicMock()):
        await rs.generate_recommendations(
            [{"fixture_id": "ext1", "home_team": "A", "away_team": "B"}],
            odds, db=AsyncMock(), db_fixtures=[fixture_orm],
        )
    assert [(r["fair_odds"], r["variant"]) for r in captured] == [(cote_ev0, variante)]


@pytest.mark.asyncio
async def test_backfill_player_api_ids():
    from app.ingestion import odds_storage

    rows = [
        SimpleNamespace(fixture_id=42, player_name="Samba, Floyd", player_api_id=None),
        SimpleNamespace(fixture_id=42, player_name="Samba", player_api_id=None),
        SimpleNamespace(fixture_id=42, player_name="Philip Foden", player_api_id=None),
    ]
    session = AsyncMock()
    res = MagicMock()
    res.scalars.return_value.all.return_value = rows
    session.execute = AsyncMock(return_value=res)
    session.commit = AsyncMock()
    loader = AsyncMock(return_value=CITY_NAPOLI)
    with patch.object(odds_storage, "load_fixture_squad", loader):
        linked = await odds_storage.backfill_player_api_ids(session, [42])

    assert linked == 2
    assert [r.player_api_id for r in rows] == [14, None, 10]
    assert loader.await_count == 1
    session.commit.assert_awaited_once()
    assert await odds_storage.backfill_player_api_ids(session, []) == 0


@pytest.mark.asyncio
async def test_pas_d_alerte_sous_10pc():
    allocs = [_alloc(i, f"Joueur {i}") for i in range(1, 12)]
    rows = [_odds(f"Joueur {i}", i, odds=1.5) for i in range(1, 11)] + [_odds("Inconnu", None)]
    _, _, alert = await _run_process(rows, allocs)
    alert.assert_not_called()


@pytest.mark.asyncio
async def test_reco_existante_sous_nom_bookmaker_est_retrouvee():
    """Ancienne reco stockée avec la graphie bookmaker : mise à jour, pas de doublon."""
    from app.models.recommendations import MarketType

    old = MagicMock()
    old.fixture_id = 42
    old.player_id = None
    old.player_name = "Jeremy Doku"
    old.market_type = MarketType.STANDARD
    old.bet_type = "goalscorer"
    old.status = "pending"
    old.best_odds = 2.5
    old.edge = 0.25
    old.alerted_odds = 2.5

    allocs = [_alloc(11, "Jérémy Doku")]
    rows = [_odds("Jeremy Doku", 11, odds=3.0)]
    stats, added, _ = await _run_process(rows, allocs, existing_recs=[old])

    assert not [r for r in added if r.market_type == MarketType.STANDARD]
    assert old.best_odds == 3.0
    assert old.player_id == 11
    assert old.status == "pending"


def _legacy_rec(name: str, status: str = "pending"):
    from app.models.recommendations import MarketType, Recommendation

    return Recommendation(
        fixture_id=42, player_id=None, player_name=name,
        market_type=MarketType.STANDARD, bet_type="goalscorer", status=status,
        best_odds=3.0, best_bookmaker="unibet", edge=0.2, fair_odds=2.5,
        fair_probability=0.4, lambda_intensity=0.5, classification="VALUE",
        confidence=0.7, explanation={},
    )


@pytest.mark.asyncio
async def test_reco_d_avant_ids_reliee_sans_doublon():
    """Reco au nom bookmaker abrégé : reliée et identifiant enregistré, pas recréée."""
    allocs = [_alloc(14, "Floyd Samba"), _alloc(15, "Tyrone Samba")]
    old = _legacy_rec("F. Samba")
    rows = [_odds("Samba, Floyd", 14)]
    _, added, _ = await _run_process(rows, allocs, existing_recs=[old])

    assert old.player_id == 14
    assert not [r for r in added if r.market_type == "standard"], "doublon créé"
    assert old.status == "pending"


@pytest.mark.asyncio
async def test_reco_d_avant_ids_non_reliable_expiree():
    """Nom ambigu (« Samba » seul, deux Samba) : plus suivable → expirée, pas figée."""
    allocs = [_alloc(14, "Floyd Samba"), _alloc(15, "Tyrone Samba")]
    old = _legacy_rec("Samba")
    _, _, _ = await _run_process([_odds("Samba, Floyd", 14)], allocs, existing_recs=[old])
    assert old.player_id is None
    assert old.status == "expired"
