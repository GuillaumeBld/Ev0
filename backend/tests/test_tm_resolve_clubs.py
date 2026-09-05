"""Tests pour la resolution des `canonical_teams.transfermarkt_club_id`
(app.ingestion.transfermarkt.resolve_clubs) et l'utilitaire fold_accents
(app.ingestion.transfermarkt.text_utils).

Fixture `fixtures/transfermarkt_ligue1_wettbewerb.html` : extrait REEL
(le `<table class="items">...</table>` complet, non modifie) de
https://www.transfermarkt.com/-/startseite/wettbewerb/FR1 recupere en direct
depuis cet environnement (acces reseau a transfermarkt.com confirme
disponible ici) le 2026-08-15. Contient les 18 clubs de Ligue 1 25/26.

Fixture `fixtures/transfermarkt_premier_league_wettbewerb.html` : meme
principe, extrait REEL de
https://www.transfermarkt.com/-/startseite/wettbewerb/GB1 recupere en
direct depuis cet environnement le 2026-08-16. Contient les 20 clubs de
Premier League. Sert de regression pour verifier que le parser capture bien
la totalite d'une page competition (pas seulement Ligue 1) : un diagnostic
manuel (comparaison avec une regex plus permissive ne recuperant QUE les
`href` `startseite/verein/<id>` sans exiger `title="..."` en position
immediatement precedente) a confirme qu'aucun club n'est perdu par
`parse_competition_clubs` sur les 5 pages de championnat domestique
(GB1/FR1/L1/ES1/IT1), avec des comptes de 20/18/18/20/20 correspondant
exactement au nombre de clubs de chaque championnat.

Les autres extraits HTML de ce fichier (matching) sont des snippets
synthetiques minimaux qui reprennent le MEME format de lien que celui
observe et prouve dans les fixtures reels ci-dessus
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


def test_parse_competition_clubs_premier_league_real_fixture_returns_20_clubs():
    # Regression : verifie que le parser capture bien TOUS les clubs d'une
    # page competition, pas seulement "la plupart" (cf. docstring module :
    # diagnostic comparant regex stricte vs permissive sur les 5 pages
    # domestiques, aucune perte constatee). La Premier League compte
    # exactement 20 clubs.
    html_text = (FIXTURES_DIR / "transfermarkt_premier_league_wettbewerb.html").read_text(
        encoding="utf-8"
    )
    clubs = parse_competition_clubs(html_text)

    assert len(clubs) == 20
    names = {name for name, _club_id in clubs}
    ids = {club_id for _name, club_id in clubs}
    assert "Manchester City" in names
    assert "Arsenal FC" in names
    assert "Brighton & Hove Albion" in names  # entite HTML "&amp;" a decoder
    assert len(ids) == len(clubs)  # pas de doublon d'id


def test_tm_competition_codes_cover_expected_leagues():
    """Les cinq grands championnats + la C1 restent le socle. La couverture
    hors de ce socle (echelons etrangers et deuxiemes divisions, ajoutes le
    05/09/2026 pour ancrer les 29 clubs sans championnat) est verifiee dans
    `tests/test_effectifs_source_unique.py`."""
    for cle, code in {
        "premier_league": "GB1",
        "ligue_1": "FR1",
        "bundesliga": "L1",
        "la_liga": "ES1",
        "serie_a": "IT1",
        "champions_league": "CL",
    }.items():
        assert TM_COMPETITION_CODES[cle] == code

    # Aucun code en double : deux cles pointant la meme page competition
    # feraient scraper deux fois la meme liste.
    codes = list(TM_COMPETITION_CODES.values())
    assert len(codes) == len(set(codes))


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


# --------------------------------------------------------------------------
# resolve_and_store_club_ids — niveaux de matching supplementaires
# (token_set / token_subset / fuzzy), ordre de priorite strict -> permissif.
# --------------------------------------------------------------------------

_TIERED_MATCHING_HTML = """
<table class="items"><tbody>
<tr class="odd"><td class="hauptlink no-border-links">
<a title="1.FC Köln" href="/1-fc-koeln/startseite/verein/500001/saison_id/2026">1.FC Köln</a>
</td></tr>
<tr class="even"><td class="hauptlink no-border-links">
<a title="1. FSV Mainz 05" href="/1-fsv-mainz-05/startseite/verein/500002/saison_id/2026">1. FSV Mainz 05</a>
</td></tr>
<tr class="odd"><td class="hauptlink no-border-links">
<a title="Toulouse FC" href="/toulouse-fc/startseite/verein/500003/saison_id/2026">Toulouse FC</a>
</td></tr>
<tr class="even"><td class="hauptlink no-border-links">
<a title="RC Strasbourg Alsace" href="/rc-strasbourg-alsace/startseite/verein/500004/saison_id/2026">RC Strasbourg Alsace</a>
</td></tr>
<tr class="odd"><td class="hauptlink no-border-links">
<a title="1.FC Union Berlin" href="/1-fc-union-berlin/startseite/verein/500005/saison_id/2026">1.FC Union Berlin</a>
</td></tr>
<tr class="even"><td class="hauptlink no-border-links">
<a title="Olympique Lyon" href="/olympique-lyon/startseite/verein/500006/saison_id/2026">Olympique Lyon</a>
</td></tr>
<tr class="odd"><td class="hauptlink no-border-links">
<a title="Real Madrid CF" href="/real-madrid-cf/startseite/verein/500007/saison_id/2026">Real Madrid CF</a>
</td></tr>
<tr class="even"><td class="hauptlink no-border-links">
<a title="Zzyzx Rovers FC" href="/zzyzx-rovers-fc/startseite/verein/500008/saison_id/2026">Zzyzx Rovers FC</a>
</td></tr>
</tbody></table>
"""


def _tiered_matching_teams() -> list[CanonicalTeam]:
    return [
        CanonicalTeam(id=100, name_fr="FC Köln", name_en=None, aliases=[], transfermarkt_club_id=None),
        CanonicalTeam(id=101, name_fr="FSV Mainz 05", name_en=None, aliases=[], transfermarkt_club_id=None),
        CanonicalTeam(id=102, name_fr="FC Toulouse", name_en=None, aliases=[], transfermarkt_club_id=None),
        CanonicalTeam(id=103, name_fr="RC Strasbourg", name_en=None, aliases=[], transfermarkt_club_id=None),
        CanonicalTeam(id=104, name_fr="Union Berlin", name_en=None, aliases=[], transfermarkt_club_id=None),
        CanonicalTeam(id=105, name_fr="Olympique Lyonnais", name_en=None, aliases=[], transfermarkt_club_id=None),
        # Deux clubs canoniques distincts dont les tokens sont CHACUN
        # contenus dans "Real Madrid CF" (TM) -> ambiguite volontaire au
        # niveau containment.
        CanonicalTeam(id=106, name_fr="Real Madrid", name_en=None, aliases=[], transfermarkt_club_id=None),
        CanonicalTeam(id=107, name_fr="Madrid CF", name_en=None, aliases=[], transfermarkt_club_id=None),
    ]


@pytest.mark.asyncio
async def test_resolve_token_set_equality_matches_reordered_names():
    # "Toulouse FC" (TM, tokens {toulouse, fc}) == "FC Toulouse" (canonique,
    # tokens {fc, toulouse}) : meme ensemble de tokens, ordre different.
    # Le prefixe ordinal allemand "1." de Köln/Mainz est egalement absorbe
    # par la tokenisation -> egalite d'ensemble de tokens directe pour eux
    # aussi (pas besoin de descendre au niveau containment).
    teams = _tiered_matching_teams()
    session = _make_session(teams)
    client = _FakeTmClient(_TIERED_MATCHING_HTML)

    await resolve_and_store_club_ids(session, client=client, competitions={"x": "X1"})

    by_id = {t.id: t for t in teams}
    assert by_id[102].transfermarkt_club_id == 500003  # Toulouse
    assert by_id[100].transfermarkt_club_id == 500001  # Köln
    assert by_id[101].transfermarkt_club_id == 500002  # Mainz


@pytest.mark.asyncio
async def test_resolve_token_containment_matches_strasbourg_and_union_berlin():
    # "RC Strasbourg" (canonique) contenu dans "RC Strasbourg Alsace" (TM).
    # "Union Berlin" (canonique) contenu dans "1.FC Union Berlin" (TM) :
    # tokens {union, berlin} contenu dans {fc, union, berlin} (le "1" est
    # deja filtre par la tokenisation, "fc" reste un token TM en plus ->
    # ceci passe par le niveau containment, pas l'egalite).
    teams = _tiered_matching_teams()
    session = _make_session(teams)
    client = _FakeTmClient(_TIERED_MATCHING_HTML)

    await resolve_and_store_club_ids(session, client=client, competitions={"x": "X1"})

    by_id = {t.id: t for t in teams}
    assert by_id[103].transfermarkt_club_id == 500004  # RC Strasbourg
    assert by_id[104].transfermarkt_club_id == 500005  # Union Berlin


@pytest.mark.asyncio
async def test_resolve_fuzzy_matches_lyon_lyonnais_as_last_resort():
    # "Olympique Lyon" (TM) / "Olympique Lyonnais" (canonique) : ni egalite
    # exacte, ni egalite/containment de tokens (le dernier token differe
    # entierement, "lyon" vs "lyonnais") -> seul le fuzzy difflib (~0.875)
    # les relie, en dernier recours.
    teams = _tiered_matching_teams()
    session = _make_session(teams)
    client = _FakeTmClient(_TIERED_MATCHING_HTML)

    await resolve_and_store_club_ids(session, client=client, competitions={"x": "X1"})

    by_id = {t.id: t for t in teams}
    assert by_id[105].transfermarkt_club_id == 500006  # Olympique Lyonnais


@pytest.mark.asyncio
async def test_resolve_ambiguous_token_subset_candidates_is_not_guessed():
    # "Real Madrid CF" (TM) contient a la fois les tokens de "Real Madrid"
    # ET de "Madrid CF" (deux canonical_teams distincts) -> ambigu au
    # niveau containment -> non resolu, JAMAIS devine (et pas de fallback
    # vers le niveau fuzzy pour tenter de departager).
    teams = _tiered_matching_teams()
    session = _make_session(teams)
    client = _FakeTmClient(_TIERED_MATCHING_HTML)

    report = await resolve_and_store_club_ids(session, client=client, competitions={"x": "X1"})

    by_id = {t.id: t for t in teams}
    assert by_id[106].transfermarkt_club_id is None  # Real Madrid
    assert by_id[107].transfermarkt_club_id is None  # Madrid CF
    assert "Real Madrid CF" in report.unresolved_tm


@pytest.mark.asyncio
async def test_resolve_unrelated_name_is_never_guessed_by_fuzzy():
    # "Zzyzx Rovers FC" ne ressemble a aucun club canonique connu, a aucun
    # niveau (exact/token_set/token_subset/fuzzy) -> non resolu.
    teams = _tiered_matching_teams()
    session = _make_session(teams)
    client = _FakeTmClient(_TIERED_MATCHING_HTML)

    report = await resolve_and_store_club_ids(session, client=client, competitions={"x": "X1"})

    assert "Zzyzx Rovers FC" in report.unresolved_tm
    # Aucun club canonique ne doit s'etre vu attribuer l'id TM 500008.
    assert all(team.transfermarkt_club_id != 500008 for team in teams)
