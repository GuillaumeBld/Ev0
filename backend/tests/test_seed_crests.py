"""Tests pour le seed des ecussons (`app.scripts.seed_crests`).

Les 854 ecussons resolus en production sont desormais ancres dans le code
via `app/data/crests.json` ({bzz_team_id: crest_url}). `seed_crests()` doit
les reappliquer en base par `bzz_team_id` -- JAMAIS par nom -- pour qu'une
restauration de base ou une reconstruction du referentiel ne les perde
jamais ni ne les melange entre deux clubs.

Suit le pattern SQLite en memoire reel de `test_attach_european.py` /
`test_footylogos_crests.py` pour `canonical_teams`, avec un petit fichier
crests.json de test (jamais le vrai fichier de 854 entrees).
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.pool import StaticPool

from app.models.canonical_teams import CanonicalTeam
from app.scripts.seed_crests import seed_crests

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


@pytest.fixture
def small_crests_file(tmp_path: Path) -> Path:
    """Petit crests.json de test : jamais le vrai fichier de 854 entrees."""
    data = {
        "63": "https://assets.footylogos.com/previews/ac-milan/ac-milan-logo-footylogos-320.webp",
        "77": "https://assets.footylogos.com/previews/inter/inter-logo-footylogos-320.webp",
        "999999": "https://assets.footylogos.com/previews/fantome/fantome-logo-footylogos-320.webp",
    }
    path = tmp_path / "crests_test.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


async def test_seed_applique_lurl_sur_le_bon_bzz_team_id(
    session_factory, small_crests_file
):
    """L'URL du fichier doit atterrir sur la ligne dont le bzz_team_id
    correspond -- et PAS sur une autre ligne, meme si son nom ressemble."""
    async with session_factory() as session:
        session.add(CanonicalTeam(
            name_fr="AC Milan",
            name_en="AC Milan",
            bzz_team_id=63,
            league_api_id=4,
            season=SEASON,
            country="Italy",
            crest_url=None,
        ))
        session.add(CanonicalTeam(
            name_fr="Inter",
            name_en="Inter Milan",
            bzz_team_id=77,
            league_api_id=4,
            season=SEASON,
            country="Italy",
            crest_url=None,
        ))
        # Ligne piege : nom proche de "AC Milan" mais bzz_team_id different,
        # ne doit JAMAIS recevoir l'ecusson destine a bzz_team_id=63.
        session.add(CanonicalTeam(
            name_fr="AC Milan (ancien doublon)",
            name_en="AC Milan (ancien doublon)",
            bzz_team_id=12345,
            league_api_id=None,
            season=None,
            country=None,
            crest_url=None,
        ))
        await session.commit()

        rapport = await seed_crests(session, data_path=small_crests_file)

        milan = (await session.execute(
            select(CanonicalTeam).where(CanonicalTeam.bzz_team_id == 63)
        )).scalar_one()
        inter = (await session.execute(
            select(CanonicalTeam).where(CanonicalTeam.bzz_team_id == 77)
        )).scalar_one()
        doublon = (await session.execute(
            select(CanonicalTeam).where(CanonicalTeam.bzz_team_id == 12345)
        )).scalar_one()

        assert milan.crest_url == (
            "https://assets.footylogos.com/previews/ac-milan/"
            "ac-milan-logo-footylogos-320.webp"
        )
        assert inter.crest_url == (
            "https://assets.footylogos.com/previews/inter/"
            "inter-logo-footylogos-320.webp"
        )
        # Le doublon au nom proche ne recoit jamais l'ecusson du bon Milan.
        assert doublon.crest_url is None

    assert rapport["appliques"] == 2


async def test_bzz_id_absent_en_base_est_compte_absent_sans_toucher_le_reste(
    session_factory, small_crests_file
):
    """`999999` n'existe dans aucune ligne canonique : il doit etre compte
    dans `bzz_ids_absents_en_base`, et aucune autre ligne ne doit bouger."""
    async with session_factory() as session:
        session.add(CanonicalTeam(
            name_fr="AC Milan",
            name_en="AC Milan",
            bzz_team_id=63,
            league_api_id=4,
            season=SEASON,
            country="Italy",
            crest_url=None,
        ))
        await session.commit()

        rapport = await seed_crests(session, data_path=small_crests_file)

        assert rapport["appliques"] == 1
        assert 999999 in rapport["bzz_ids_absents_en_base"]
        # 77 (Inter) n'est pas en base non plus dans ce test -- seul 63 y est.
        assert sorted(rapport["bzz_ids_absents_en_base"]) == [77, 999999]

        toutes = (await session.execute(select(CanonicalTeam))).scalars().all()
        assert len(toutes) == 1  # aucune ligne fantome creee


async def test_idempotent_deuxieme_passage_ne_change_rien(
    session_factory, small_crests_file
):
    """Relancer le seed deux fois de suite doit produire le meme etat --
    aucun effet de bord, aucune erreur d'unicite."""
    async with session_factory() as session:
        session.add(CanonicalTeam(
            name_fr="AC Milan",
            name_en="AC Milan",
            bzz_team_id=63,
            league_api_id=4,
            season=SEASON,
            country="Italy",
            crest_url=None,
        ))
        session.add(CanonicalTeam(
            name_fr="Inter",
            name_en="Inter Milan",
            bzz_team_id=77,
            league_api_id=4,
            season=SEASON,
            country="Italy",
            crest_url=None,
        ))
        await session.commit()

        premier = await seed_crests(session, data_path=small_crests_file)
        second = await seed_crests(session, data_path=small_crests_file)

        assert premier == second

        milan = (await session.execute(
            select(CanonicalTeam).where(CanonicalTeam.bzz_team_id == 63)
        )).scalar_one()
        assert milan.crest_url == (
            "https://assets.footylogos.com/previews/ac-milan/"
            "ac-milan-logo-footylogos-320.webp"
        )


async def test_ligne_sans_bzz_id_dans_le_fichier_nest_jamais_touchee(
    session_factory, small_crests_file
):
    """Une ligne canonique dont le bzz_team_id n'apparait pas dans le
    fichier garde son crest_url (ou son absence) intact."""
    async with session_factory() as session:
        session.add(CanonicalTeam(
            name_fr="Club Hors Fichier",
            name_en="Club Hors Fichier",
            bzz_team_id=55555,
            league_api_id=4,
            season=SEASON,
            country="France",
            crest_url="https://assets.footylogos.com/previews/deja-pose.webp",
        ))
        await session.commit()

        await seed_crests(session, data_path=small_crests_file)

        ligne = (await session.execute(
            select(CanonicalTeam).where(CanonicalTeam.bzz_team_id == 55555)
        )).scalar_one()
        assert ligne.crest_url == "https://assets.footylogos.com/previews/deja-pose.webp"


async def test_rebuild_preserve_crest_url_preexistant(session_factory):
    """Non-regression : un `rebuild()` qui re-ecrit l'engagement d'un club
    deja en base ne doit JAMAIS remettre son crest_url a NULL. Le reset en
    bloc de `rebuild()` ne porte que sur league_api_id/season/country."""
    from unittest.mock import AsyncMock, MagicMock

    from app.scripts.rebuild_team_registry import rebuild

    def _event(home_id, home_nom, away_id, away_nom):
        return {
            "id": 1000 + home_id,
            "home_team_obj": {"id": home_id, "name": home_nom},
            "away_team_obj": {"id": away_id, "name": away_nom},
        }

    async def _get_all(path, params=None):
        # Ligue 4 (Serie A) : effectif attendu non recreee ici, on
        # restreint via league_ids pour ne segmenter qu'une seule ligue.
        return [_event(63, "AC Milan", 77, "Inter")]

    client = MagicMock()
    client.get_all = AsyncMock(side_effect=_get_all)

    async with session_factory() as session:
        session.add(CanonicalTeam(
            name_fr="AC Milan",
            name_en="AC Milan",
            bzz_team_id=63,
            league_api_id=4,
            season="2024-2025",
            country="Italy",
            crest_url=(
                "https://assets.footylogos.com/previews/ac-milan/"
                "ac-milan-logo-footylogos-320.webp"
            ),
        ))
        await session.commit()

        from app.ingestion.bzzoiro.league_registry import LEAGUE_REGISTRY

        # On restreint la segmentation stricte a la ligue 4 avec un
        # effectif attendu de 2, pour que le controle Phase 1 passe sans
        # avoir a monter tout le registre big-5.
        import app.scripts.rebuild_team_registry as mod

        ancien_effectifs = mod.EFFECTIFS_STRICTS
        mod.EFFECTIFS_STRICTS = {4: 2}
        try:
            assert LEAGUE_REGISTRY.get(4) is not None  # ligue 4 existe bien
            await rebuild(session, client, season=SEASON, league_ids=[4])
        finally:
            mod.EFFECTIFS_STRICTS = ancien_effectifs

        milan = (await session.execute(
            select(CanonicalTeam).where(CanonicalTeam.bzz_team_id == 63)
        )).scalar_one()
        assert milan.league_api_id == 4
        assert milan.season == SEASON
        assert milan.crest_url == (
            "https://assets.footylogos.com/previews/ac-milan/"
            "ac-milan-logo-footylogos-320.webp"
        )


def test_crests_json_est_un_dict_non_vide_bien_forme():
    """Validation legere du fichier source de verite : charge, non vide,
    cles = entiers-chaines, valeurs = URLs http(s). Ne fige pas le compte
    exact (854 aujourd'hui) -- juste "non vide et bien forme"."""
    data_path = (
        Path(__file__).resolve().parents[1] / "app" / "data" / "crests.json"
    )
    data = json.loads(data_path.read_text(encoding="utf-8"))

    assert isinstance(data, dict)
    assert len(data) > 0
    for key, value in data.items():
        assert isinstance(key, str) and key.isdigit()
        assert isinstance(value, str)
        assert value.startswith(("http://", "https://"))
