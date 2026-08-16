"""Tests pour la resolution des `canonical_teams.transfermarkt_club_id`
(app.ingestion.transfermarkt.resolve_clubs) et l'utilitaire fold_accents
(app.ingestion.transfermarkt.text_utils).

Fixture `fixtures/transfermarkt_ligue1_wettbewerb.html` : extrait REEL
(le `<table class="items">...</table>` complet, non modifie) de
https://www.transfermarkt.com/-/startseite/wettbewerb/FR1 recupere en direct
depuis cet environnement (acces reseau a transfermarkt.com confirme
disponible ici) le 2026-08-15. Contient les 18 clubs de Ligue 1 25/26.

Les autres extraits HTML de ce fichier (matching) sont des snippets
synthetiques minimaux qui reprennent le MEME format de lien que celui
observe et prouve dans le fixture reel ci-dessus
(`<a title="Nom" href="/slug/startseite/verein/ID/saison_id/ANNEE">`) —
utilises uniquement pour isoler la logique de matching, pas le parsing.
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.ingestion.transfermarkt.resolve_clubs import (
    TM_COMPETITION_CODES,
    ResolveReport,
    parse_competition_clubs,
    resolve_and_store_club_ids,
)
from app.ingestion.transfermarkt.text_utils import fold_accents
from app.models.canonical_teams import CanonicalTeam

FIXTURES_DIR = Path(__file__).parent / "fixtures"


# --------------------------------------------------------------------------
# fold_accents
# --------------------------------------------------------------------------


def test_fold_accents_strips_diacritics_and_lowercases():
    assert fold_accents("Dembélé") == "dembele"


def test_fold_accents_folds_e_like_e_accent():
    assert fold_accents("e") == fold_accents("é")
    assert fold_accents("Séville") == fold_accents("Seville")


def test_fold_accents_collapses_whitespace():
    assert fold_accents("  RC   Lens  ") == "rc lens"


def test_fold_accents_case_insensitive():
    assert fold_accents("PARIS SAINT-GERMAIN") == fold_accents("Paris Saint-Germain")


# --------------------------------------------------------------------------
# parse_competition_clubs — contre le fixture HTML REEL (Ligue 1)
# --------------------------------------------------------------------------


def test_parse_competition_clubs_real_fixture_returns_plausible_club_list():
    html_text = (FIXTURES_DIR / "transfermarkt_ligue1_wettbewerb.html").read_text(encoding="utf-8")
    clubs = parse_competition_clubs(html_text)

    # 18 clubs de Ligue 1 25/26 dans le fixture reel -> au moins 15 attendus.
    assert len(clubs) >= 15

    names = {name for name, _club_id in clubs}
    ids = {club_id for _name, club_id in clubs}
    assert "Paris Saint-Germain" in names
    assert "RC Strasbourg Alsace" in names
    assert "Olympique Marseille" in names
    assert 583 in ids  # PSG
    assert all(isinstance(club_id, int) for club_id in ids)
    # Pas de doublon d'id malgre les liens icone + texte pour un meme club.
    assert len(ids) == len(clubs)


def test_parse_competition_clubs_returns_empty_list_for_no_match():
    assert parse_competition_clubs("<html><body>rien ici</body></html>") == []


def test_tm_competition_codes_cover_expected_leagues():
    assert TM_COMPETITION_CODES == {
        "premier_league": "GB1",
        "ligue_1": "FR1",
        "bundesliga": "L1",
        "la_liga": "ES1",
        "serie_a": "IT1",
        "champions_league": "CL",
    }


# --------------------------------------------------------------------------
# resolve_and_store_club_ids — matching, mocke (session + client HTTP)
# --------------------------------------------------------------------------

_FAKE_COMPETITION_HTML = """
<table class="items"><tbody>
<tr class="odd"><td class="hauptlink no-border-links">
<a title="Paris Saint-Germain" href="/fc-paris-saint-germain/startseite/verein/583/saison_id/2026">Paris Saint-Germain</a>
</td></tr>
<tr class="even"><td class="hauptlink no-border-links">
<a title="Seville FC" href="/seville-fc/startseite/verein/9999/saison_id/2026">Seville FC</a>
</td></tr>
<tr class="odd"><td class="hauptlink no-border-links">
<a title="Unknown Rovers" href="/unknown-rovers/startseite/verein/1234/saison_id/2026">Unknown Rovers</a>
</td></tr>
</tbody></table>
"""


class _FakeResponse:
    def __init__(self, text: str) -> None:
        self.text = text


class _FakeTmClient:
    """Double du TransfermarktClient : renvoie le meme HTML pour chaque code
    demande (suffisant pour tester le matching, pas le reseau)."""

    def __init__(self, html_text: str) -> None:
        self.html_text = html_text
        self.requested_urls: list[str] = []
        self.closed = False

    def get(self, url: str, *, what: str = "", accept_json: bool = False) -> _FakeResponse:
        self.requested_urls.append(url)
        return _FakeResponse(self.html_text)

    def close(self) -> None:
        self.closed = True


def _make_session(teams: list[CanonicalTeam]) -> MagicMock:
    result = MagicMock()
    result.scalars.return_value.all.return_value = teams
    session = MagicMock()
    session.execute = AsyncMock(return_value=result)
    session.commit = AsyncMock()
    session.add = MagicMock()
    return session


def _psg(transfermarkt_club_id: int | None = None) -> CanonicalTeam:
    return CanonicalTeam(
        id=1,
        name_fr="Paris SG",
        name_en=None,
        aliases=["Paris Saint-Germain", "PSG"],
        transfermarkt_club_id=transfermarkt_club_id,
    )


def _seville(transfermarkt_club_id: int | None = None) -> CanonicalTeam:
    # Nom canonique accentue ; le club TM correspondant ("Seville FC") ne
    # l'est pas -> ne matche que via fold_accents.
    return CanonicalTeam(
        id=2,
        name_fr="Séville FC",
        name_en="Seville FC",
        aliases=[],
        transfermarkt_club_id=transfermarkt_club_id,
    )


def _other_club(transfermarkt_club_id: int | None = None) -> CanonicalTeam:
    # N'apparait jamais dans le HTML TM du test -> doit finir en unmatched_canonical.
    return CanonicalTeam(
        id=3,
        name_fr="Some Other Club",
        name_en=None,
        aliases=[],
        transfermarkt_club_id=transfermarkt_club_id,
    )


@pytest.mark.asyncio
async def test_resolve_matches_by_alias_and_accents_and_writes_ids():
    teams = [_psg(), _seville(), _other_club()]
    session = _make_session(teams)
    client = _FakeTmClient(_FAKE_COMPETITION_HTML)

    report = await resolve_and_store_club_ids(
        session, client=client, competitions={"ligue_1": "FR1"}
    )

    assert isinstance(report, ResolveReport)
    # PSG matche via son alias "Paris Saint-Germain".
    assert teams[0].transfermarkt_club_id == 583
    # Seville FC matche malgre l'accent (Séville FC canonique vs Seville FC TM).
    assert teams[1].transfermarkt_club_id == 9999
    assert report.resolved == 2
    session.commit.assert_awaited()


@pytest.mark.asyncio
async def test_resolve_unmatched_tm_club_is_reported_not_guessed():
    teams = [_psg(), _seville(), _other_club()]
    session = _make_session(teams)
    client = _FakeTmClient(_FAKE_COMPETITION_HTML)

    report = await resolve_and_store_club_ids(
        session, client=client, competitions={"ligue_1": "FR1"}
    )

    assert "Unknown Rovers" in report.unresolved_tm


@pytest.mark.asyncio
async def test_resolve_reports_unmatched_canonical_team():
    teams = [_psg(), _seville(), _other_club()]
    session = _make_session(teams)
    client = _FakeTmClient(_FAKE_COMPETITION_HTML)

    report = await resolve_and_store_club_ids(
        session, client=client, competitions={"ligue_1": "FR1"}
    )

    assert "Some Other Club" in report.unmatched_canonical
    assert "Paris SG" not in report.unmatched_canonical
    assert "Séville FC" not in report.unmatched_canonical


@pytest.mark.asyncio
async def test_resolve_is_idempotent_does_not_rewrite_already_correct_id():
    teams = [_psg(transfermarkt_club_id=583), _seville(), _other_club()]
    session = _make_session(teams)
    client = _FakeTmClient(_FAKE_COMPETITION_HTML)

    report = await resolve_and_store_club_ids(
        session, client=client, competitions={"ligue_1": "FR1"}
    )

    # PSG deja correctement associe -> compte comme resolu, mais session.add
    # n'est jamais appele pour lui (aucune ecriture superflue).
    assert teams[0].transfermarkt_club_id == 583
    assert report.resolved == 2
    for call in session.add.call_args_list:
        assert call.args[0] is not teams[0]


@pytest.mark.asyncio
async def test_resolve_never_overwrites_conflicting_existing_id():
    # PSG deja associe a un AUTRE id TM (donnee incoherente / corruption) ->
    # ne doit JAMAIS etre ecrase silencieusement, meme si le nom matche.
    teams = [_psg(transfermarkt_club_id=111111), _seville(), _other_club()]
    session = _make_session(teams)
    client = _FakeTmClient(_FAKE_COMPETITION_HTML)

    report = await resolve_and_store_club_ids(
        session, client=client, competitions={"ligue_1": "FR1"}
    )

    assert teams[0].transfermarkt_club_id == 111111
    assert "Paris Saint-Germain" in report.unresolved_tm


@pytest.mark.asyncio
async def test_resolve_ambiguous_canonical_match_is_not_guessed():
    # Deux canonical_teams differents partagent le meme nom/alias replie ->
    # ambigu, le club TM correspondant doit etre en unresolved, aucun des
    # deux ne doit recevoir l'id.
    dup_a = CanonicalTeam(id=10, name_fr="Paris Saint-Germain", aliases=[], transfermarkt_club_id=None)
    dup_b = CanonicalTeam(id=11, name_fr="PSG Reserve", aliases=["Paris Saint-Germain"], transfermarkt_club_id=None)
    session = _make_session([dup_a, dup_b])
    client = _FakeTmClient(_FAKE_COMPETITION_HTML)

    report = await resolve_and_store_club_ids(
        session, client=client, competitions={"ligue_1": "FR1"}
    )

    assert dup_a.transfermarkt_club_id is None
    assert dup_b.transfermarkt_club_id is None
    assert "Paris Saint-Germain" in report.unresolved_tm
