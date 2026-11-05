"""Tests pour la resolution des ecussons via footylogos.com
(`app.ingestion.footylogos.resolve_crests`).

Aucun test ne touche au reseau : `FootyLogosClient` recoit un
`httpx.AsyncClient` dont le transport est mocke (`httpx.MockTransport`).
La regle testee, identique au reste de l'ingestion (Transfermarkt,
API-Football) : `crest_url` n'est pose QUE si l'URL image candidate rend
un GET 200 — zero candidat valide laisse `crest_url` a NULL et consigne
le club dans le rapport, jamais une URL devinee.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.ext.compiler import compiles

from app.ingestion.footylogos.resolve_crests import (
    CrestReport,
    FootyLogosClient,
    candidate_slugs,
    crest_preview_url,
    extract_country_slugs,
    resolve_crests,
    slugify,
)
from app.models.canonical_teams import CanonicalTeam


@compiles(ARRAY, "sqlite")
def _compile_array_as_json_for_sqlite(element, compiler, **kw):  # noqa: ARG001
    return "JSON"


def _team(
    id_: int,
    name_fr: str,
    name_en: str | None = None,
    country: str | None = None,
    league_api_id: int | None = 1,
    crest_url: str | None = None,
) -> CanonicalTeam:
    return CanonicalTeam(
        id=id_,
        name_fr=name_fr,
        name_en=name_en,
        aliases=[],
        league_api_id=league_api_id,
        country=country,
        crest_url=crest_url,
    )


def _make_session(teams: list[CanonicalTeam]) -> MagicMock:
    result = MagicMock()
    result.scalars.return_value.all.return_value = teams
    session = MagicMock()
    session.execute = AsyncMock(return_value=result)
    session.commit = AsyncMock()
    return session


def _client_from_handler(handler, retry_delay: float = 0.0) -> FootyLogosClient:
    """Construit un `FootyLogosClient` dont le `httpx.AsyncClient` sous-jacent
    tape directement sur `handler` — aucun reseau reel. `retry_delay=0`
    pour que les tests couvrant le retry 429 restent instantanes."""
    transport = httpx.MockTransport(handler)
    http_client = httpx.AsyncClient(transport=transport, timeout=5.0)
    return FootyLogosClient(http_client=http_client, retry_delay=retry_delay)


# ---------------------------------------------------------------------------
# Slug direct -> pose
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_slug_direct_resout_et_pose_crest_url():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"  # jamais HEAD, cf. docstring module
        assert "Mozilla" in request.headers.get("user-agent", "")
        if request.url.path == "/previews/arsenal/arsenal-logo-footylogos-320.webp":
            return httpx.Response(200, content=b"fake-webp")
        return httpx.Response(404)

    team = _team(1, "Arsenal", country="England")
    session = _make_session([team])
    client = _client_from_handler(handler)

    report = await resolve_crests(session, client)

    assert team.crest_url == crest_preview_url("arsenal")
    assert report.resolved == ["Arsenal"]
    assert report.resolved_country_page == []
    assert report.not_found == []
    assert report.by_country == {"England": {"resolved": 1, "not_found": 0}}
    session.commit.assert_awaited()


# ---------------------------------------------------------------------------
# Variante (prefixe afc-) -> pose
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_variante_prefixe_afc_resout_et_pose_crest_url():
    # "Bournemouth" seul ne rend jamais 200 ; seule la variante prefixee
    # "afc-bournemouth" existe sur footylogos.
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/previews/afc-bournemouth/afc-bournemouth-logo-footylogos-320.webp":
            return httpx.Response(200, content=b"fake-webp")
        return httpx.Response(404)

    team = _team(1, "Bournemouth", country="England")
    assert "afc-bournemouth" in candidate_slugs(team)

    session = _make_session([team])
    client = _client_from_handler(handler)

    report = await resolve_crests(session, client)

    assert team.crest_url == crest_preview_url("afc-bournemouth")
    assert report.resolved == ["Bournemouth"]


# ---------------------------------------------------------------------------
# Variante (suffixe -cf) -> pose
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_variante_suffixe_cf_resout_et_pose_crest_url():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/previews/elche-cf/elche-cf-logo-footylogos-320.webp":
            return httpx.Response(200, content=b"fake-webp")
        return httpx.Response(404)

    team = _team(1, "Elche", country="Spain")
    session = _make_session([team])
    client = _client_from_handler(handler)

    report = await resolve_crests(session, client)

    assert team.crest_url == crest_preview_url("elche-cf")
    assert report.resolved == ["Elche"]


# ---------------------------------------------------------------------------
# Dictionnaire FR->EN seed -> pose (nom francise, slug non derivable par
# simple slugification)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_seed_fr_en_resout_barcelone():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/previews/fc-barcelona/fc-barcelona-logo-footylogos-320.webp":
            return httpx.Response(200, content=b"fake-webp")
        return httpx.Response(404)

    team = _team(1, "Barcelone", country="Spain")
    session = _make_session([team])
    client = _client_from_handler(handler)

    report = await resolve_crests(session, client)

    assert team.crest_url == crest_preview_url("fc-barcelona")
    assert report.resolved == ["Barcelone"]


# ---------------------------------------------------------------------------
# Repli page pays + match par similarite -> pose (apres verification 200)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_repli_page_pays_avec_match_flou_resout_et_pose_crest_url():
    country_page_html = """
    <html><body>
      <a href="/fr/logos/the-rightclub">The Rightclub</a>
      <a href="/fr/logos/some-other-team">Some Other Team</a>
    </body></html>
    """

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/fr/country/england":
            return httpx.Response(200, text=country_page_html)
        if request.url.path == "/previews/the-rightclub/the-rightclub-logo-footylogos-320.webp":
            return httpx.Response(200, content=b"fake-webp")
        # Tous les candidats slug directs/variantes echouent -> force le repli
        return httpx.Response(404)

    team = _team(1, "The Right Club", country="England")
    session = _make_session([team])
    client = _client_from_handler(handler)

    report = await resolve_crests(session, client)

    assert team.crest_url == crest_preview_url("the-rightclub")
    assert report.resolved == []
    assert report.resolved_country_page == ["The Right Club"]
    assert report.by_country == {"England": {"resolved": 1, "not_found": 0}}


def test_extract_country_slugs_matche_les_deux_formats_de_lien():
    html = '<a href="/fr/logos/a-fc">A</a><a href="/logos/b-fc">B</a>'
    assert extract_country_slugs(html) == ["a-fc", "b-fc"]


# ---------------------------------------------------------------------------
# Aucun candidat 200 -> crest_url reste NULL, consigne dans not_found —
# jamais un faux ecusson.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_aucun_candidat_200_laisse_null_et_consigne():
    country_page_html = '<a href="/fr/logos/completely-unrelated-slug">X</a>'

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/fr/country/england":
            return httpx.Response(200, text=country_page_html)
        return httpx.Response(404)

    team = _team(1, "Club Fantome", country="England")
    session = _make_session([team])
    client = _client_from_handler(handler)

    report = await resolve_crests(session, client)

    assert team.crest_url is None
    assert report.resolved == []
    assert report.resolved_country_page == []
    assert len(report.not_found) == 1
    assert report.not_found[0].name == "Club Fantome"
    assert report.not_found[0].canonical_id == 1
    assert report.not_found[0].country == "England"
    assert report.by_country == {"England": {"resolved": 0, "not_found": 1}}
    session.commit.assert_awaited()


@pytest.mark.asyncio
async def test_aucun_candidat_et_pays_inconnu_laisse_null_sans_requete_pays():
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        return httpx.Response(404)

    team = _team(1, "Club Sans Pays", country=None)
    session = _make_session([team])
    client = _client_from_handler(handler)

    report = await resolve_crests(session, client)

    assert team.crest_url is None
    assert len(report.not_found) == 1
    assert report.not_found[0].country is None
    # Aucune requete vers une page pays : le pays canonique est inconnu.
    assert not any("/country/" in path for path in calls)


# ---------------------------------------------------------------------------
# dry_run ne commit jamais
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_dry_run_ne_commit_pas():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/previews/arsenal/arsenal-logo-footylogos-320.webp":
            return httpx.Response(200, content=b"fake-webp")
        return httpx.Response(404)

    team = _team(1, "Arsenal", country="England")
    session = _make_session([team])
    client = _client_from_handler(handler)

    report = await resolve_crests(session, client, dry_run=True)

    # Pose sur l'objet en memoire (utile pour inspecter le rapport), mais
    # rien n'est persiste.
    assert team.crest_url == crest_preview_url("arsenal")
    assert report.resolved == ["Arsenal"]
    session.commit.assert_not_awaited()


# ---------------------------------------------------------------------------
# only_missing filtre les clubs deja resolus
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_only_missing_filtre_les_clubs_deja_resolus(monkeypatch):
    """`only_missing=True` (defaut) doit restreindre la requete SQL aux
    clubs sans crest_url. On capture la clause WHERE compilee plutot que de
    monter une base SQLite complete, pour rester un test unitaire rapide."""
    import app.ingestion.footylogos.resolve_crests as mod

    captured: dict[str, object] = {}

    async def _fake_execute(stmt):
        captured["stmt"] = stmt
        result = MagicMock()
        result.scalars.return_value.all.return_value = []
        return result

    session = MagicMock()
    session.execute = AsyncMock(side_effect=_fake_execute)
    session.commit = AsyncMock()

    await mod.resolve_crests(session, FootyLogosClient(http_client=httpx.AsyncClient()), only_missing=True)

    compiled = str(captured["stmt"].compile(compile_kwargs={"literal_binds": True}))
    assert "crest_url IS NULL" in compiled
    assert "league_api_id IS NOT NULL" in compiled


@pytest.mark.asyncio
async def test_all_ne_filtre_pas_sur_crest_url(monkeypatch):
    import app.ingestion.footylogos.resolve_crests as mod

    captured: dict[str, object] = {}

    async def _fake_execute(stmt):
        captured["stmt"] = stmt
        result = MagicMock()
        result.scalars.return_value.all.return_value = []
        return result

    session = MagicMock()
    session.execute = AsyncMock(side_effect=_fake_execute)
    session.commit = AsyncMock()

    await mod.resolve_crests(session, FootyLogosClient(http_client=httpx.AsyncClient()), only_missing=False)

    compiled = str(captured["stmt"].compile(compile_kwargs={"literal_binds": True}))
    # Le SELECT porte sur CanonicalTeam entier (donc "crest_url" apparait
    # comme colonne projetee) mais la clause de filtre dediee doit etre
    # absente : --all ne restreint pas sur crest_url.
    assert "crest_url IS NULL" not in compiled
    assert "league_api_id IS NOT NULL" in compiled


# ---------------------------------------------------------------------------
# 429 -> retente puis reussit
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_429_retente_puis_reussit():
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        if len(calls) == 1:
            return httpx.Response(429)
        return httpx.Response(200, content=b"fake-webp")

    client = _client_from_handler(handler, retry_delay=0.0)

    exists = await client.image_exists("real-madrid")

    assert exists is True
    assert len(calls) == 2  # 1er appel 429, 2e appel 200 (retente reussie)


@pytest.mark.asyncio
async def test_429_persistant_epuise_les_tentatives_et_rend_false():
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        return httpx.Response(429)

    client = _client_from_handler(handler, retry_delay=0.0)
    client._max_retries = 3  # borne explicite, coherent avec le defaut

    exists = await client.image_exists("club-toujours-limite")

    assert exists is False
    assert len(calls) == 3  # borne par max_retries, jamais une boucle infinie


@pytest.mark.asyncio
async def test_resolve_crests_propage_le_retry_429_dans_le_flux_complet():
    """Le retry 429 doit fonctionner de bout en bout via resolve_crests, pas
    seulement sur le client isole."""
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        if request.url.path == "/previews/arsenal/arsenal-logo-footylogos-320.webp" and len(calls) == 1:
            return httpx.Response(429)
        if request.url.path == "/previews/arsenal/arsenal-logo-footylogos-320.webp":
            return httpx.Response(200, content=b"fake-webp")
        return httpx.Response(404)

    team = _team(1, "Arsenal", country="England")
    session = _make_session([team])
    client = _client_from_handler(handler, retry_delay=0.0)

    report = await resolve_crests(session, client)

    assert team.crest_url == crest_preview_url("arsenal")
    assert report.resolved == ["Arsenal"]


# ---------------------------------------------------------------------------
# Helpers purs (slugify / candidate_slugs) — pas de reseau
# ---------------------------------------------------------------------------


def test_slugify_couvre_les_cas_du_cahier_des_charges():
    assert slugify("Arsenal") == "arsenal"
    assert slugify("Real Madrid") == "real-madrid"
    assert slugify("Ludogorets") == "ludogorets"
    assert slugify("Club Brugge") == "club-brugge"
    assert slugify("Tottenham Hotspur") == "tottenham-hotspur"
    assert slugify("AS Monaco & Co") == "as-monaco-and-co"


def test_candidate_slugs_inclut_prefixe_affixe_et_suffixes():
    team = _team(1, "Leeds", country="England")
    candidates = candidate_slugs(team)
    assert "leeds" in candidates
    assert "leeds-united" in candidates
    assert "afc-leeds" in candidates
