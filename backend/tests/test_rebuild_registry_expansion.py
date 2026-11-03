"""Tests Tache 2 de l'expansion ~57 competitions : la boucle de segmentation
stricte de `rebuild_team_registry.rebuild` doit desormais etre derivee du
`LEAGUE_REGISTRY` (Tache 1) au lieu du dict `EFFECTIFS_REGLEMENTAIRES`
code en dur (big-5), et ecrire `country` en plus de `league_api_id`/`season`.

Suit le pattern de `tests/scripts/test_rebuild_team_registry.py` pour le faux
client (mock de `get_all`), mais avec un moteur SQLite EN MEMOIRE REEL pour
`canonical_teams` (comme `tests/test_sync_squads.py` le fait pour d'autres
tables) : on veut prouver que `country` et le garde-fou "rien ecrit si abort"
tiennent au niveau de la base, pas seulement au niveau d'un mock.

`canonical_teams.aliases` est un ARRAY(Text) postgres, que SQLite ne sait pas
compiler nativement ; on enregistre, UNIQUEMENT dans ce module de test, un
compilateur DDL qui le fait passer pour un JSON generique — exactement le
meme procede que celui deja employe pour JSONB dans test_sync_squads.py.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.pool import StaticPool

from app.ingestion.bzzoiro.league_registry import LEAGUE_REGISTRY
from app.models.canonical_teams import CanonicalTeam
from app.scripts.rebuild_team_registry import (
    EFFECTIFS_STRICTS,
    SegmentationError,
    rebuild,
)

SEASON = "2026-2027"


@compiles(ARRAY, "sqlite")
def _compile_array_as_json_for_sqlite(element, compiler, **kw):  # noqa: ARG001
    return "JSON"


@pytest.fixture
async def session_factory():
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    async with engine.begin() as conn:
        await conn.run_sync(lambda sync_conn: CanonicalTeam.__table__.create(sync_conn))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        yield factory
    finally:
        await engine.dispose()


def _event(home_id, home_nom, away_id, away_nom):
    return {
        "id": 1000 + home_id,
        "home_team_obj": {"id": home_id, "name": home_nom},
        "away_team_obj": {"id": away_id, "name": away_nom},
    }


def _events_pour(n_clubs: int, decalage: int = 0):
    """Fabrique des matchs couvrant exactement n_clubs clubs distincts.

    Fonctionne aussi pour un compte impair : le dernier match sans
    partenaire est accouple au premier club (deja compte), si bien que
    l'ensemble distinct final reste exactement n_clubs.
    """
    clubs = [(decalage + i, f"Club {decalage + i}") for i in range(1, n_clubs + 1)]
    evs = []
    i = 0
    while i + 1 < len(clubs):
        (hid, hn), (aid, an) = clubs[i], clubs[i + 1]
        evs.append(_event(hid, hn, aid, an))
        i += 2
    if i < len(clubs):
        hid, hn = clubs[i]
        aid, an = clubs[0]
        evs.append(_event(hid, hn, aid, an))
    return evs


def _client_nominal():
    """Faux client qui rend, pour chaque championnat strict, exactement son
    effectif reglementaire — avec des identifiants de club disjoints entre
    championnats (decalage de 1000 par championnat, largement > tout
    expected_count du registre)."""
    decalages = {lid: i * 1000 for i, lid in enumerate(EFFECTIFS_STRICTS)}

    async def _get_all(path, params=None):
        lid = params["league"]
        return _events_pour(EFFECTIFS_STRICTS[lid], decalages[lid])

    client = MagicMock()
    client.get_all = AsyncMock(side_effect=_get_all)
    return client


# ---------------------------------------------------------------------------
# L'ensemble strict derive du registre
# ---------------------------------------------------------------------------


def test_effectifs_stricts_derive_du_registre():
    attendu = {
        league_id: meta.expected_count
        for league_id, meta in LEAGUE_REGISTRY.items()
        if meta.kind in ("domestic_1", "domestic_2") and meta.format == "round_robin"
    }
    assert EFFECTIFS_STRICTS == attendu


def test_effectifs_stricts_contient_les_29_domestic_round_robin():
    assert len(EFFECTIFS_STRICTS) == 29
    for league_id in EFFECTIFS_STRICTS:
        meta = LEAGUE_REGISTRY[league_id]
        assert meta.kind in ("domestic_1", "domestic_2")
        assert meta.format == "round_robin"


def test_effectifs_stricts_exclut_uefa_et_selection():
    # 7 = Champions League (uefa_club, groups) : ne fixe pas un engagement
    # championnat, traite en Tache 4.
    assert 7 not in EFFECTIFS_STRICTS
    # 27 = Coupe du monde 2026 (selection, groups) : idem.
    assert 27 not in EFFECTIFS_STRICTS


# ---------------------------------------------------------------------------
# Ecriture nominale : league_api_id + season + country
# ---------------------------------------------------------------------------


async def test_ligue_round_robin_au_bon_compte_est_engagee_avec_country(session_factory):
    client = _client_nominal()

    async with session_factory() as session:
        comptes = await rebuild(session, client, season=SEASON)

        assert comptes == EFFECTIFS_STRICTS

        # Ligue 1 (id 6) : 18 clubs attendus, pays "France" dans le registre.
        lignes = (
            await session.execute(
                select(CanonicalTeam).where(CanonicalTeam.league_api_id == 6)
            )
        ).scalars().all()
        assert len(lignes) == 18
        for ligne in lignes:
            assert ligne.season == SEASON
            assert ligne.country == LEAGUE_REGISTRY[6].country == "France"

        # Serie A (id 4) : pays "Italy", pour confirmer que country suit bien
        # la ligue et n'est pas une valeur figee.
        serie_a = (
            await session.execute(
                select(CanonicalTeam).where(CanonicalTeam.league_api_id == 4)
            )
        ).scalars().all()
        assert len(serie_a) == 20
        for ligne in serie_a:
            assert ligne.country == "Italy"


async def test_club_existant_recupere_aussi_le_country(session_factory):
    """Un club deja present (apparie par bzz_team_id) doit voir son country
    mis a jour, pas seulement league_api_id/season — meme chemin de code que
    pour un club nouveau (cf. rebuild_team_registry.rebuild, branche
    `existant`)."""
    client = _client_nominal()

    async with session_factory() as session:
        # Pre-cree un club qui correspond au premier engage de Ligue 1 (id 6),
        # sans country ni engagement : simule une ligne canonique anterieure.
        decalage_l1 = list(EFFECTIFS_STRICTS).index(6) * 1000
        session.add(CanonicalTeam(
            name_fr="Club prealable",
            name_en="Club prealable",
            bzz_team_id=decalage_l1 + 1,
            league_api_id=None,
            season=None,
            country=None,
        ))
        await session.commit()

        await rebuild(session, client, season=SEASON)

        ligne = (
            await session.execute(
                select(CanonicalTeam).where(CanonicalTeam.bzz_team_id == decalage_l1 + 1)
            )
        ).scalar_one()
        assert ligne.league_api_id == 6
        assert ligne.season == SEASON
        assert ligne.country == "France"


# ---------------------------------------------------------------------------
# Garde-fous : abort-on-ecart et club en double championnat, rien n'est ecrit
# ---------------------------------------------------------------------------


async def test_ecart_de_compte_sur_une_ligue_interrompt_sans_rien_ecrire(session_factory):
    decalages = {lid: i * 1000 for i, lid in enumerate(EFFECTIFS_STRICTS)}

    async def _get_all(path, params=None):
        lid = params["league"]
        n = EFFECTIFS_STRICTS[lid]
        if lid == 6:
            n -= 1  # Ligue 1 : un club manquant.
        return _events_pour(n, decalages[lid])

    client = MagicMock()
    client.get_all = AsyncMock(side_effect=_get_all)

    async with session_factory() as session:
        # Pre-existant : une ligne deja engagee pour CETTE saison, qui
        # servirait de preuve si le remplacement en bloc (reset) s'etait
        # execute malgre l'erreur.
        session.add(CanonicalTeam(
            name_fr="Ancien engage",
            name_en="Ancien engage",
            bzz_team_id=999999,
            league_api_id=5,
            season=SEASON,
            country="Germany",
        ))
        await session.commit()

        with pytest.raises(SegmentationError) as exc:
            await rebuild(session, client, season=SEASON)

        assert "17" in str(exc.value) and "18" in str(exc.value)

        # Rien n'a ete ecrit : la ligne preexistante garde son engagement.
        ligne = (
            await session.execute(
                select(CanonicalTeam).where(CanonicalTeam.bzz_team_id == 999999)
            )
        ).scalar_one()
        assert ligne.league_api_id == 5
        assert ligne.season == SEASON
        assert ligne.country == "Germany"

        total = (await session.execute(select(CanonicalTeam))).scalars().all()
        assert len(total) == 1


async def test_club_engage_dans_deux_championnats_interrompt_sans_rien_ecrire(session_factory):
    decalages = {lid: i * 1000 for i, lid in enumerate(EFFECTIFS_STRICTS)}
    leagues = list(EFFECTIFS_STRICTS)
    premiere, deuxieme = leagues[0], leagues[1]

    async def _get_all(path, params=None):
        lid = params["league"]
        evs = _events_pour(EFFECTIFS_STRICTS[lid], decalages[lid])
        if lid == deuxieme:
            # Le club 1 (engage dans `premiere`, decalage 0) reapparait ici.
            evs[0]["home_team_obj"] = {"id": decalages[premiere] + 1, "name": "Club double"}
        return evs

    client = MagicMock()
    client.get_all = AsyncMock(side_effect=_get_all)

    async with session_factory() as session:
        with pytest.raises(SegmentationError) as exc:
            await rebuild(session, client, season=SEASON)

        assert "deux championnats" in str(exc.value)

        total = (await session.execute(select(CanonicalTeam))).scalars().all()
        assert total == []
