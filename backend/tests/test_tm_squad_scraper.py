"""Tests pour le scraping d'effectif Transfermarkt
(app.ingestion.transfermarkt.squad_scraper).

Fixtures `fixtures/tm_kader_psg.html` (Paris Saint-Germain) et
`fixtures/tm_kader_villa.html` (Aston Villa) : extraits REELS (page
"kader" complete, vue "Compact", saison 2026), non modifies.

Constat important verifie en explorant ces 2 fixtures reels : la vue
"Compact" (`/kader/verein/<id>`, celle que `fetch_club_squad` recupere)
n'affiche JAMAIS de date de naissance complete, seulement l'age (entier).
Une date complete (`dob`) n'apparait que sur la vue "Detailed"
(`/plus/1`) que nous n'avons pas en fixture reel. `parse_squad` reste
ecrit pour capturer une date complete des qu'elle est presente sur la
page (formats verifies via des extraits synthetiques isoles ci-dessous,
meme principe que `test_tm_resolve_clubs.py`), mais n'invente/n'approxime
jamais une date a partir du seul age -> sur les 2 fixtures reels, `dob`
vaut `None` pour tous les joueurs, ce qui est le comportement honnete
(donnee absente de la page, pas un bug de parsing).

Autre constat : le parsing naif "un lien par joueur, sans balise imbriquee"
sous-compte l'effectif reel, car Transfermarkt ajoute une icone (`<span>`
imbrique dans le lien du nom) pour le capitaine et pour les joueurs
blesses/suspendus (ex: Marquinhos et John McGinn, tous deux capitaines ;
Johan Manzambi et Amadou Onana, blesses, cote Aston Villa). `parse_squad`
gere ce cas (extrait le texte du nom en retirant les balises internes) ->
27 joueurs distincts pour PSG et 25 pour Aston Villa (et non 26/22, qui
serait le compte d'un parsing naif ratant ces lignes a icone).
"""
from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from app.ingestion.transfermarkt.squad_scraper import (
    MIN_SQUAD,
    SquadResult,
    TMPlayer,
    fetch_club_squad,
    parse_squad,
)
from app.scripts.transfermarkt_career import TransfermarktError

FIXTURES_DIR = Path(__file__).parent / "fixtures"

PSG_HTML = (FIXTURES_DIR / "tm_kader_psg.html").read_text(encoding="utf-8")
VILLA_HTML = (FIXTURES_DIR / "tm_kader_villa.html").read_text(encoding="utf-8")


# --------------------------------------------------------------------------
# parse_squad — fixtures REELLES
# --------------------------------------------------------------------------


def test_parse_squad_psg_real_fixture_status_ok_and_plausible_count():
    result = parse_squad(PSG_HTML, club_id=583)

    assert result.status == "ok"
    assert len(result.players) >= 20
    assert result.club_id == 583
    assert result.raw_html == PSG_HTML
    # Pas de doublon d'id malgre les icones (capitaine, blessure...) imbriquees
    # dans le lien du nom sur certaines lignes.
    ids = [p.tm_player_id for p in result.players]
    assert len(ids) == len(set(ids))
    assert all(isinstance(p, TMPlayer) for p in result.players)


def test_parse_squad_psg_captain_name_position_correct_dob_honestly_none():
    # Marquinhos (capitaine) : le lien de son nom contient un <span> imbrique
    # (icone capitaine) -> verifie que le nom est bien nettoye des balises
    # internes plutot que tronque au premier tag rencontre.
    result = parse_squad(PSG_HTML, club_id=583)
    by_name = {p.name: p for p in result.players}

    assert "Marquinhos" in by_name
    marquinhos = by_name["Marquinhos"]
    assert marquinhos.tm_player_id == 181767
    assert marquinhos.position == "Centre-Back"
    # Vue Compact : pas de date de naissance complete sur la page -> None,
    # jamais une valeur inventee a partir de l'age seul.
    assert marquinhos.dob is None


def test_parse_squad_psg_regular_player_name_position_correct():
    result = parse_squad(PSG_HTML, club_id=583)
    by_name = {p.name: p for p in result.players}

    assert "Ousmane Dembélé" in by_name
    dembele = by_name["Ousmane Dembélé"]
    assert dembele.tm_player_id == 288230
    assert dembele.position == "Centre-Forward"


def test_parse_squad_villa_real_fixture_status_ok_and_plausible_count():
    result = parse_squad(VILLA_HTML, club_id=405)

    assert result.status == "ok"
    assert len(result.players) >= 20
    assert result.club_id == 405
    ids = [p.tm_player_id for p in result.players]
    assert len(ids) == len(set(ids))


def test_parse_squad_villa_captain_and_injured_players_name_position_correct():
    # John McGinn (capitaine) et Johan Manzambi / Amadou Onana (icones de
    # blessure) : memes lignes a icone imbriquee que Marquinhos cote PSG.
    result = parse_squad(VILLA_HTML, club_id=405)
    by_name = {p.name: p for p in result.players}

    assert "John McGinn" in by_name
    mcginn = by_name["John McGinn"]
    assert mcginn.tm_player_id == 193116
    assert mcginn.position == "Central Midfield"
    assert mcginn.dob is None

    assert "Emiliano Martínez" in by_name
    martinez = by_name["Emiliano Martínez"]
    assert martinez.tm_player_id == 111873
    assert martinez.position == "Goalkeeper"


# --------------------------------------------------------------------------
# parse_squad — dates de naissance (extraits synthetiques isoles, meme
# principe que test_tm_resolve_clubs.py : la vue Compact reelle n'en a pas,
# on isole donc le format "Detailed" TM connu sans dependre du reseau).
# --------------------------------------------------------------------------

_ROW_TEMPLATE = """
<tr class="odd">
<td class="zentriert rueckennummer bg_Sturm" title="Attack"><div class=rn_nummer>9</div></td><td class="posrela">
<table class="inline-table">
    <tr>
        <td rowspan="2">
            <img title="Test Player" alt="Test Player" />
        </td>
        <td class="hauptlink">
            <a href="/test-player/profil/spieler/999001">
                Test Player            </a>
        </td>
    </tr>
    <tr>
        <td>
            Centre-Forward        </td>
    </tr>
</table>
</td><td class="zentriert">{dob_cell}</td><td class="zentriert"><img title="France" alt="France" /></td><td class="zentriert">30/06/2028</td><td class="rechts hauptlink"><a href="/test-player/marktwertverlauf/spieler/999001">€1.00m</a></td></tr>
"""


def test_parse_squad_dob_parsed_when_mmm_d_yyyy_format_present():
    row = _ROW_TEMPLATE.format(dob_cell="Jun 25, 1994 (31)")
    result = parse_squad(row, club_id=1)
    # Page trop petite (1 joueur) -> "empty", mais le parsing du joueur
    # lui-meme (dont dob) reste teste independamment du statut.
    assert len(result.players) == 1
    assert result.players[0].dob == date(1994, 6, 25)


def test_parse_squad_dob_parsed_when_dot_format_present():
    row = _ROW_TEMPLATE.format(dob_cell="25.06.1994 (31)")
    result = parse_squad(row, club_id=1)
    assert result.players[0].dob == date(1994, 6, 25)


def test_parse_squad_dob_none_when_only_age_present():
    row = _ROW_TEMPLATE.format(dob_cell="31")
    result = parse_squad(row, club_id=1)
    assert result.players[0].dob is None


# --------------------------------------------------------------------------
# parse_squad — statuts d'erreur, jamais d'exception
# --------------------------------------------------------------------------


def test_parse_squad_empty_html_returns_structure_error():
    result = parse_squad("", club_id=1)
    assert result.status == "structure_error"
    assert result.players == []


def test_parse_squad_whitespace_only_html_returns_structure_error():
    result = parse_squad("   \n\t  ", club_id=1)
    assert result.status == "structure_error"


def test_parse_squad_html_without_any_player_link_returns_structure_error():
    # Page non vide (ex: page d'erreur TM) mais 0 lien hauptlink/profil ->
    # signale un changement de structure, pas un effectif vide.
    result = parse_squad("<html><body><h1>404 - Page not found</h1></body></html>", club_id=1)
    assert result.status == "structure_error"
    assert result.players == []


def test_parse_squad_truncated_html_mid_table_does_not_raise():
    # Fixture reelle coupee brutalement au milieu du tableau effectif.
    truncated = PSG_HTML[: PSG_HTML.find("<tbody>") + 2000]
    result = parse_squad(truncated, club_id=583)
    assert result.status in ("ok", "empty", "structure_error")
    assert isinstance(result, SquadResult)


def test_parse_squad_too_few_players_returns_empty_not_ok():
    rows = "".join(
        _ROW_TEMPLATE.format(dob_cell="31").replace("999001", str(999000 + i)) for i in range(5)
    )
    assert MIN_SQUAD > 5
    result = parse_squad(rows, club_id=1)
    assert result.status == "empty"
    assert len(result.players) == 5


# --------------------------------------------------------------------------
# fetch_club_squad — client mocke (pas de reseau)
# --------------------------------------------------------------------------


class _FakeResponse:
    def __init__(self, text: str) -> None:
        self.text = text


class _FakeTmClient:
    def __init__(self, *, text: str | None = None, error: Exception | None = None) -> None:
        self._text = text
        self._error = error
        self.requested_urls: list[str] = []

    def get(self, url: str, *, what: str = "", accept_json: bool = False) -> _FakeResponse:
        self.requested_urls.append(url)
        if self._error is not None:
            raise self._error
        return _FakeResponse(self._text or "")


@pytest.mark.asyncio
async def test_fetch_club_squad_fetches_correct_url_and_parses_response():
    client = _FakeTmClient(text=PSG_HTML)

    result = await fetch_club_squad(client, tm_club_id=583)

    assert client.requested_urls == ["https://www.transfermarkt.com/x/kader/verein/583"]
    assert result.status == "ok"
    assert result.club_id == 583
    assert len(result.players) >= 20


@pytest.mark.asyncio
async def test_fetch_club_squad_never_raises_on_transfermarkt_error():
    client = _FakeTmClient(error=TransfermarktError("HTTP 500 apres 5 tentatives"))

    result = await fetch_club_squad(client, tm_club_id=583)

    assert result.status == "structure_error"
    assert result.players == []
    assert result.club_id == 583
