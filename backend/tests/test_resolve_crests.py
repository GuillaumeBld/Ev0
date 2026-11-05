"""Tests pour la resolution automatique des ecussons
(`app.ingestion.apifootball.resolve_crests`).

Aucun test ne touche au reseau : `resolve_crests` recoit un client factice
(`_FakeApiFootballClient`) qui rend des listes de candidats pre-fabriquees.
La regle testee est la meme que pour le reste de l'ingestion (Transfermarkt
notamment) : un ecusson n'est pose que si la recherche rend un candidat
UNIQUE ; zero ou plusieurs candidats laissent `api_football_id` a NULL et
sont consignes dans le rapport, jamais devines.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.pool import StaticPool

from app.ingestion.apifootball.resolve_crests import (
    ApiFootballClient,
    ApiFootballKeyMissingError,
    CrestReport,
    TeamCandidate,
    resolve_crests,
)
from app.models.canonical_teams import CanonicalTeam


@compiles(ARRAY, "sqlite")
def _compile_array_as_json_for_sqlite(element, compiler, **kw):  # noqa: ARG001
    return "JSON"


def _team(id_: int, name_fr: str, country: str | None = None, league_api_id: int | None = 1) -> CanonicalTeam:
    return CanonicalTeam(
        id=id_,
        name_fr=name_fr,
        name_en=None,
        aliases=[],
        league_api_id=league_api_id,
        country=country,
        api_football_id=None,
    )


def _make_session(teams: list[CanonicalTeam]) -> MagicMock:
    result = MagicMock()
    result.scalars.return_value.all.return_value = teams
    session = MagicMock()
    session.execute = AsyncMock(return_value=result)
    session.commit = AsyncMock()
    return session


class _FakeApiFootballClient:
    """Double de test pour `ApiFootballClient` — aucun appel reseau.

    `responses` associe un nom de club a la liste de candidats que l'API
    rendrait pour ce nom. Un nom absent du dict rend une liste vide (aucun
    candidat), pour couvrir "introuvable" sans avoir a tout enumerer.
    """

    def __init__(self, responses: dict[str, list[TeamCandidate]] | None = None) -> None:
        self._responses = responses or {}
        self.calls: list[tuple[str, str | None]] = []

    async def search_teams(self, name: str, country: str | None = None) -> list[TeamCandidate]:
        self.calls.append((name, country))
        return self._responses.get(name, [])


class _FailingApiFootballClient:
    """Double simulant un echec reseau sur chaque recherche."""

    async def search_teams(self, name: str, country: str | None = None) -> list[TeamCandidate]:
        raise RuntimeError("boom: connexion refusee")


# ---------------------------------------------------------------------------
# Match univoque -> l'id est pose
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_match_univoque_pose_api_football_id():
    psg = _team(1, "Paris Saint-Germain", country="France")
    session = _make_session([psg])
    client = _FakeApiFootballClient({
        "Paris Saint-Germain": [
            TeamCandidate(api_football_id=85, name="Paris Saint Germain", country="France")
        ],
    })

    report = await resolve_crests(session, client)

    assert isinstance(report, CrestReport)
    assert psg.api_football_id == 85
    assert report.resolved_count == 1
    assert report.resolved == ["Paris Saint-Germain"]
    assert report.ambiguous == []
    assert report.not_found == []
    session.commit.assert_awaited()

    # La recherche part bien du nom + pays du club canonique
    assert client.calls == [("Paris Saint-Germain", "France")]


@pytest.mark.asyncio
async def test_club_sans_pays_connu_cherche_sans_filtre_pays():
    team = _team(1, "Club Inconnu", country=None)
    session = _make_session([team])
    client = _FakeApiFootballClient({
        "Club Inconnu": [TeamCandidate(api_football_id=999, name="Club Inconnu")],
    })

    await resolve_crests(session, client)

    assert client.calls == [("Club Inconnu", None)]


# ---------------------------------------------------------------------------
# Coherence pays sur le candidat unique — coeur du fix anti-faux-ecusson :
# un candidat unique ne suffit pas seul si le pays canonique est connu et
# ne correspond pas, et le cas "pays canonique inconnu" doit etre trace a
# part (resolved_unverified), pas noye dans resolved.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_pays_connu_et_candidat_unique_bon_pays_pose_dans_resolved():
    team = _team(1, "Rangers", country="Scotland")
    session = _make_session([team])
    client = _FakeApiFootballClient({
        "Rangers": [TeamCandidate(api_football_id=257, name="Rangers", country="Scotland")],
    })

    report = await resolve_crests(session, client)

    assert team.api_football_id == 257
    assert report.resolved == ["Rangers"]
    assert report.resolved_unverified == []
    assert report.rejected == []


@pytest.mark.asyncio
async def test_b_pays_connu_et_candidat_unique_mauvais_pays_rejete_sans_faux_ecusson():
    team = _team(1, "Rangers", country="Scotland")
    session = _make_session([team])
    # Homonyme reel : Rangers FC (Talca, Chili) partage le nom mais pas le pays.
    client = _FakeApiFootballClient({
        "Rangers": [TeamCandidate(api_football_id=1234, name="Rangers", country="Chile")],
    })

    report = await resolve_crests(session, client)

    # Jamais un faux ecusson : NULL plutot qu'un id incoherent avec le pays.
    assert team.api_football_id is None
    assert report.resolved == []
    assert report.resolved_unverified == []
    assert len(report.rejected) == 1
    assert report.rejected[0].name == "Rangers"
    assert report.rejected[0].canonical_id == 1
    assert "Chile" in report.rejected[0].candidates[0]


@pytest.mark.asyncio
async def test_c_pays_canonique_inconnu_et_candidat_unique_pose_mais_non_verifie():
    team = _team(1, "Club Sans Pays Connu", country=None)
    session = _make_session([team])
    client = _FakeApiFootballClient({
        "Club Sans Pays Connu": [TeamCandidate(api_football_id=999, name="Club Sans Pays Connu")],
    })

    report = await resolve_crests(session, client)

    # L'id est pose (candidat unique) mais range a part de `resolved` :
    # aucune verification pays n'a pu etre faite, la revue manuelle doit
    # pouvoir cibler precisement ce cas.
    assert team.api_football_id == 999
    assert report.resolved == []
    assert report.resolved_unverified == ["Club Sans Pays Connu"]
    assert report.rejected == []


@pytest.mark.asyncio
async def test_coherence_pays_tolere_variantes_de_libelle():
    team = _team(1, "Manchester United", country="England")
    session = _make_session([team])
    client = _FakeApiFootballClient({
        "Manchester United": [
            TeamCandidate(api_football_id=33, name="Manchester United", country="Angleterre")
        ],
    })

    report = await resolve_crests(session, client)

    assert team.api_football_id == 33
    assert report.resolved == ["Manchester United"]
    assert report.rejected == []


# ---------------------------------------------------------------------------
# Zero candidat -> NULL + consigne "introuvable"
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_zero_candidat_laisse_null_et_consigne_introuvable():
    team = _team(1, "Club Fantome")
    session = _make_session([team])
    client = _FakeApiFootballClient({})  # aucun candidat pour ce nom

    report = await resolve_crests(session, client)

    assert team.api_football_id is None
    assert report.resolved == []
    assert len(report.not_found) == 1
    assert report.not_found[0].name == "Club Fantome"
    assert report.not_found[0].canonical_id == 1


# ---------------------------------------------------------------------------
# Plusieurs candidats -> NULL + consigne "ambigu", jamais un choix au hasard
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_plusieurs_candidats_laisse_null_et_consigne_ambigu():
    team = _team(1, "Real")
    session = _make_session([team])
    client = _FakeApiFootballClient({
        "Real": [
            TeamCandidate(api_football_id=541, name="Real Madrid"),
            TeamCandidate(api_football_id=548, name="Real Betis"),
        ],
    })

    report = await resolve_crests(session, client)

    assert team.api_football_id is None
    assert report.resolved == []
    assert len(report.ambiguous) == 1
    assert report.ambiguous[0].name == "Real"
    assert set(report.ambiguous[0].candidates) == {"Real Madrid", "Real Betis"}


# ---------------------------------------------------------------------------
# dry_run ne doit jamais commit
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_dry_run_ne_commit_pas():
    psg = _team(1, "Paris Saint-Germain", country="France")
    session = _make_session([psg])
    client = _FakeApiFootballClient({
        "Paris Saint-Germain": [TeamCandidate(api_football_id=85, name="PSG", country="France")],
    })

    report = await resolve_crests(session, client, dry_run=True)

    # L'id est pose sur l'objet en memoire (utile pour inspecter le rapport
    # avant d'ecrire), mais rien n'est persiste.
    assert psg.api_football_id == 85
    assert report.resolved_count == 1
    session.commit.assert_not_awaited()


# ---------------------------------------------------------------------------
# Un echec reseau sur un club ne bloque pas les suivants
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_echec_reseau_consigne_sans_bloquer_les_autres_clubs():
    boiteux = _team(1, "Club Boiteux")
    ok = _team(2, "Club Ok", country="France")
    session = _make_session([boiteux, ok])

    class _MixteClient:
        async def search_teams(self, name: str, country: str | None = None) -> list[TeamCandidate]:
            if name == "Club Boiteux":
                raise RuntimeError("timeout")
            return [TeamCandidate(api_football_id=42, name=name, country="France")]

    report = await resolve_crests(session, _MixteClient())

    assert boiteux.api_football_id is None
    assert len(report.errors) == 1
    assert "Club Boiteux" in report.errors[0]
    # Le club suivant est quand meme resolu
    assert ok.api_football_id == 42
    assert report.resolved == ["Club Ok"]


# ---------------------------------------------------------------------------
# La requete filtre sur les clubs engages sans ecusson — jamais les autres
#
# SQLite EN MEMOIRE REEL (pas de mock sur session.execute) : le test
# precedent ne verifiait que la presence de sous-chaines dans `str(query)`,
# ce qui passerait meme avec des conditions inversees (p.ex.
# `api_football_id IS NOT NULL`). Ici on prouve le comportement reel avec
# quatre clubs couvrant les 4 combinaisons NULL/non-NULL des deux colonnes,
# et on verifie que SEUL le club "engage + sans ecusson" est selectionne.
# ---------------------------------------------------------------------------


@pytest.fixture
async def _sqlite_session_factory():
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


@pytest.mark.asyncio
async def test_la_requete_ne_selectionne_que_les_clubs_engages_sans_ecusson(
    _sqlite_session_factory,
):
    engage_sans_ecusson = CanonicalTeam(
        id=1, name_fr="Engage Sans Ecusson", league_api_id=1, api_football_id=None,
    )
    engage_avec_ecusson = CanonicalTeam(
        id=2, name_fr="Engage Avec Ecusson", league_api_id=1, api_football_id=85,
    )
    non_engage_sans_ecusson = CanonicalTeam(
        id=3, name_fr="Non Engage Sans Ecusson", league_api_id=None, api_football_id=None,
    )
    non_engage_avec_ecusson = CanonicalTeam(
        id=4, name_fr="Non Engage Avec Ecusson", league_api_id=None, api_football_id=42,
    )

    async with _sqlite_session_factory() as session:
        session.add_all(
            [engage_sans_ecusson, engage_avec_ecusson, non_engage_sans_ecusson, non_engage_avec_ecusson]
        )
        await session.commit()

        client = _FakeApiFootballClient({})  # 0 candidat pour tous -> pas de mutation
        report = await resolve_crests(session, client, dry_run=True)

        # Seul le club engage + sans ecusson a ete interroge et consigne.
        assert client.calls == [("Engage Sans Ecusson", None)]
        assert len(report.not_found) == 1
        assert report.not_found[0].name == "Engage Sans Ecusson"


# ---------------------------------------------------------------------------
# Cle API-Football absente -> erreur claire, pas un plantage httpx obscur
# ---------------------------------------------------------------------------


def test_client_leve_une_erreur_claire_sans_cle(monkeypatch):
    import app.ingestion.apifootball.resolve_crests as mod

    monkeypatch.setattr(mod.settings, "api_football_key", None)

    with pytest.raises(ApiFootballKeyMissingError, match="API_FOOTBALL_KEY manquante"):
        ApiFootballClient()


def test_client_leve_une_erreur_claire_avec_cle_vide(monkeypatch):
    import app.ingestion.apifootball.resolve_crests as mod

    monkeypatch.setattr(mod.settings, "api_football_key", "")

    with pytest.raises(ApiFootballKeyMissingError, match="API_FOOTBALL_KEY manquante"):
        ApiFootballClient()


def test_client_accepte_une_cle_explicite_meme_si_settings_est_vide(monkeypatch):
    import app.ingestion.apifootball.resolve_crests as mod

    monkeypatch.setattr(mod.settings, "api_football_key", None)

    client = ApiFootballClient(api_key="une-cle-explicite")
    assert client is not None
