"""Tests Tache 4 de l'expansion ~57 competitions : la passe de rattachement
UEFA/qualif doit respecter l'invariant "un club UEFA garde son championnat
domestique s'il en a un ; sinon il est enregistre sans pays ni championnat
devine, et consigne".

Suit le pattern de `tests/test_rebuild_registry_expansion.py` (moteur SQLite
EN MEMOIRE REEL pour `canonical_teams`, faux client qui mocke `get_all` —
aucun reseau).
"""
from __future__ import annotations

import logging
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.pool import StaticPool

from app.models.canonical_teams import CanonicalTeam
from app.scripts.rebuild_team_registry import (
    UEFA_CLUB_COMPETITIONS,
    attach_european,
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


def test_uefa_club_competitions_est_exactement_7_8_83_90():
    assert set(UEFA_CLUB_COMPETITIONS) == {7, 8, 83, 90}


async def test_club_deja_domestique_garde_son_championnat(session_factory):
    """AC Milan, deja segmente en Serie A (league_api_id=4) par `rebuild`
    (Task 2), apparait aussi dans l'enumeration UCL — la Ligue des champions
    lui fournit des matchs, pas un engagement championnat : rien ne change."""

    async def _get_all(path, params=None):
        if params["league"] == 7:
            return [_event(63, "AC Milan", 999, "Qualif Only")]
        return []

    client = MagicMock()
    client.get_all = AsyncMock(side_effect=_get_all)

    async with session_factory() as session:
        session.add(CanonicalTeam(
            name_fr="AC Milan",
            name_en="AC Milan",
            bzz_team_id=63,
            league_api_id=4,
            season=SEASON,
            country="Italy",
        ))
        await session.commit()

        await attach_european(session, client, season=SEASON)

        ligne = (
            await session.execute(
                select(CanonicalTeam).where(CanonicalTeam.bzz_team_id == 63)
            )
        ).scalar_one()
        assert ligne.league_api_id == 4
        assert ligne.country == "Italy"
        assert ligne.season == SEASON


async def test_club_qualif_uefa_seulement_est_enregistre_sans_championnat(
    session_factory, caplog
):
    """Un club vu UNIQUEMENT en UCL, sans ligne domestique dans le perimetre,
    doit etre enregistre (appariable par bzz_team_id) avec league_api_id=NULL
    et country=NULL — jamais de pays devine — et le cas doit etre consigne."""

    async def _get_all(path, params=None):
        if params["league"] == 7:
            return [_event(999, "Qualif Only FC", 63, "AC Milan")]
        return []

    client = MagicMock()
    client.get_all = AsyncMock(side_effect=_get_all)

    async with session_factory() as session:
        with caplog.at_level(logging.INFO):
            await attach_european(session, client, season=SEASON)

        ligne = (
            await session.execute(
                select(CanonicalTeam).where(CanonicalTeam.bzz_team_id == 999)
            )
        ).scalar_one()
        assert ligne.league_api_id is None
        assert ligne.country is None

    assert "sans championnat" in caplog.text
    assert "Qualif Only FC" in caplog.text


async def test_club_qualif_existant_sans_domestique_est_corrige_sans_pays(
    session_factory, caplog
):
    """Une ligne canonique preexistante (appariee par bzz_team_id), sans
    engagement domestique dans le perimetre, ne doit jamais se voir attribuer
    un pays ou un league_api_id devine — meme si elle portait des valeurs
    stagnantes d'un ancien run."""

    async def _get_all(path, params=None):
        if params["league"] == 83:
            return [_event(999, "Qualif Only FC", 998, "Autre Qualif")]
        return []

    client = MagicMock()
    client.get_all = AsyncMock(side_effect=_get_all)

    async with session_factory() as session:
        session.add(CanonicalTeam(
            name_fr="Qualif Only FC",
            name_en="Qualif Only FC",
            bzz_team_id=999,
            league_api_id=None,
            season=None,
            country=None,
        ))
        await session.commit()

        with caplog.at_level(logging.INFO):
            await attach_european(session, client, season=SEASON)

        ligne = (
            await session.execute(
                select(CanonicalTeam).where(CanonicalTeam.bzz_team_id == 999)
            )
        ).scalar_one()
        assert ligne.league_api_id is None
        assert ligne.country is None

    assert "sans championnat" in caplog.text


async def test_aucun_club_ne_finit_avec_un_league_api_id_uefa(session_factory):
    """Invariant global : 7/8/83/90 ne doivent JAMAIS apparaitre dans
    canonical_teams.league_api_id — l'UEFA fournit des matchs, jamais un
    engagement championnat."""

    async def _get_all(path, params=None):
        mapping = {
            7: [_event(63, "AC Milan", 1, "Qualif A")],
            8: [_event(2, "Qualif B", 3, "Qualif C")],
            83: [_event(4, "Qualif D", 5, "Qualif E")],
            90: [_event(6, "Qualif F", 7777, "Qualif G")],
        }
        return mapping.get(params["league"], [])

    client = MagicMock()
    client.get_all = AsyncMock(side_effect=_get_all)

    async with session_factory() as session:
        session.add(CanonicalTeam(
            name_fr="AC Milan",
            name_en="AC Milan",
            bzz_team_id=63,
            league_api_id=4,
            season=SEASON,
            country="Italy",
        ))
        await session.commit()

        await attach_european(session, client, season=SEASON)

        toutes = (await session.execute(select(CanonicalTeam))).scalars().all()
        assert len(toutes) >= 7
        for ligne in toutes:
            assert ligne.league_api_id not in UEFA_CLUB_COMPETITIONS
            assert (ligne.country is not None) == (ligne.league_api_id is not None)


async def test_country_non_nul_ssi_league_api_id_non_nul(session_factory):
    """L'invariant denormalise : country rempli ssi league_api_id l'est."""

    async def _get_all(path, params=None):
        if params["league"] == 7:
            return [_event(63, "AC Milan", 999, "Qualif Only FC")]
        return []

    client = MagicMock()
    client.get_all = AsyncMock(side_effect=_get_all)

    async with session_factory() as session:
        session.add(CanonicalTeam(
            name_fr="AC Milan",
            name_en="AC Milan",
            bzz_team_id=63,
            league_api_id=4,
            season=SEASON,
            country="Italy",
        ))
        await session.commit()

        await attach_european(session, client, season=SEASON)

        toutes = (await session.execute(select(CanonicalTeam))).scalars().all()
        for ligne in toutes:
            assert (ligne.country is not None) == (ligne.league_api_id is not None)
