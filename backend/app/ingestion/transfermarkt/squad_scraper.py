"""Scraping de l'effectif courant d'un club depuis sa page "kader" Transfermarkt.

Reutilise le client HTTP durci (`TransfermarktClient` : UA navigateur,
rate-limit, retry/backoff) de `app.scripts.transfermarkt_career` plutot que
de le reimplementer (meme principe que `app.ingestion.transfermarkt.resolve_clubs`
pour la resolution des clubs).

Format de page verifie contre deux extraits REELS de la vue "Compact"
(`/kader/verein/<id>`, saison 2026, tab "Compact" active) :
  - backend/tests/fixtures/tm_kader_psg.html (Paris Saint-Germain, 26 joueurs)
  - backend/tests/fixtures/tm_kader_villa.html (Aston Villa, 22 joueurs)

Chaque ligne du tableau effectif (`<tr class="odd">` / `<tr class="even">`)
contient :
  - le nom + l'id TM du joueur dans `td.hauptlink a[href*="/profil/spieler/<id>"]`.
    Le contenu du lien peut inclure des balises imbriquees (ex: l'icone
    "capitaine" `<span title="Team captain">&nbsp;</span>` juste apres le nom,
    vu sur Marquinhos/PSG et John McGinn/Aston Villa dans les 2 fixtures) ->
    on capture tout le contenu du lien puis on retire les balises internes,
    on ne s'arrete pas au premier tag rencontre ;
  - la position PRECISE (ex: "Centre-Back") dans la 2e ligne du mini-tableau
    `table.inline-table` juste sous le nom (element "sous le nom" du spec) ;
  - une position GENERIQUE de secours (ex: "Defender") dans l'attribut
    `title` de la cellule `td.rueckennummer` (toujours presente sur les 2
    fixtures, meme si le mini-tableau ne matchait pas) ;
  - la date de naissance, QUAND la page la fournit. La vue "Compact" des 2
    fixtures reels n'affiche que l'age (entier, ex: "27"), jamais une date
    complete -> `dob` vaut alors `None` pour tous les joueurs. Ce n'est pas
    un bug de parsing : la donnee n'existe simplement pas dans cette vue.
    Le parsing reste ecrit pour capturer une date complete des qu'elle est
    presente sur la page (formats observes sur Transfermarkt : "Jun 25,
    1994" ou "25.06.1994", eventuellement suivis de "(age)"), sans jamais
    rien inventer/approximer a partir du seul age.
"""
from __future__ import annotations

import html
import logging
import re
from dataclasses import dataclass
from datetime import date, datetime
from typing import Literal

from app.scripts.transfermarkt_career import BASE_URL, TransfermarktClient, TransfermarktError

logger = logging.getLogger(__name__)

# En dessous de ce nombre de joueurs parses, la page est consideree comme
# anormalement incomplete (effectif reel, meme un club modeste, en compte
# toujours nettement plus) -> statut "empty" plutot que "ok".
MIN_SQUAD = 15

_TAG_RE = re.compile(r"<[^>]+>")
_WHITESPACE_RE = re.compile(r"\s+")

_ROW_RE = re.compile(r'<tr class="(?:odd|even)">')
_HAUPTLINK_RE = re.compile(
    r'<td class="hauptlink">\s*<a href="(/[a-z0-9\-]+/profil/spieler/(\d+))">(.*?)</a>',
    re.DOTALL,
)
_POSITION_BUCKET_RE = re.compile(r'rueckennummer[^>]*\btitle="([^"]*)"')
# La position precise est la derniere cellule du mini-tableau `inline-table`
# imbrique (2e <tr>, juste avant la fermeture de ce mini-tableau).
_POSITION_DETAIL_RE = re.compile(r"<tr>\s*<td>\s*([^<]*?)\s*</td>\s*</tr>\s*</table>")

# Formats de date de naissance rencontres sur les pages TM (vue "Detailed").
# La vue "Compact" (nos 2 fixtures reels) n'en affiche aucun -> dob = None.
_DOB_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("%b %d, %Y", re.compile(r"([A-Z][a-z]{2} \d{1,2}, \d{4})")),
    ("%d.%m.%Y", re.compile(r"(\d{1,2}\.\d{1,2}\.\d{4})")),
)


@dataclass
class TMPlayer:
    """Un joueur tel que liste sur la page effectif Transfermarkt d'un club."""

    name: str
    dob: date | None
    position: str | None
    tm_player_id: int


@dataclass
class SquadResult:
    """Resultat du parsing/fetch d'une page effectif Transfermarkt.

    `status`:
      - "ok" : >= MIN_SQUAD joueurs parses.
      - "empty" : page qui ressemble a un effectif mais avec < MIN_SQUAD
        joueurs (page partiellement rendue, effectif hors-saison, etc.).
      - "structure_error" : page vide, HTTP en echec, ou 0 lien joueur
        trouve sur une page non vide (changement de structure TM).
    """

    club_id: int
    players: list[TMPlayer]
    status: Literal["ok", "empty", "structure_error"]
    raw_html: str


def _clean_text(fragment: str) -> str:
    """Retire les balises HTML imbriquees, decode les entites, normalise les
    espaces (y compris `&nbsp;`) d'un fragment de texte."""
    without_tags = _TAG_RE.sub("", fragment)
    return _WHITESPACE_RE.sub(" ", html.unescape(without_tags)).strip()


def _parse_dob(row_html: str) -> date | None:
    for fmt, pattern in _DOB_PATTERNS:
        match = pattern.search(row_html)
        if not match:
            continue
        try:
            return datetime.strptime(match.group(1), fmt).date()
        except ValueError:
            continue
    return None


def _split_rows(html_text: str) -> list[str]:
    """Decoupe le document en fragments `<tr class="odd|even">...` (un par
    ligne du tableau effectif), chacun allant jusqu'au debut de la ligne
    suivante (ou la fin du document pour la derniere).

    Les `<tr>` imbriques dans le mini-tableau `inline-table` de chaque ligne
    n'ont pas de classe "odd"/"even" -> ils ne coupent jamais une ligne en
    deux, cette regle de decoupage est donc sure.
    """
    starts = [m.start() for m in _ROW_RE.finditer(html_text)]
    if not starts:
        return []
    starts.append(len(html_text))
    return [html_text[starts[i] : starts[i + 1]] for i in range(len(starts) - 1)]


def _parse_row(row_html: str) -> TMPlayer | None:
    hauptlink = _HAUPTLINK_RE.search(row_html)
    if not hauptlink:
        return None

    tm_player_id = int(hauptlink.group(2))
    name = _clean_text(hauptlink.group(3))
    if not name:
        return None

    detail_match = _POSITION_DETAIL_RE.search(row_html, hauptlink.end())
    position = _clean_text(detail_match.group(1)) if detail_match else ""
    if not position:
        bucket_match = _POSITION_BUCKET_RE.search(row_html)
        position = _clean_text(bucket_match.group(1)) if bucket_match else ""

    return TMPlayer(
        name=name,
        dob=_parse_dob(row_html),
        position=position or None,
        tm_player_id=tm_player_id,
    )


def parse_squad(html_text: str, club_id: int) -> SquadResult:
    """Parse une page "kader" Transfermarkt (vue Compact ou Detailed) en
    liste de `TMPlayer`. Parsing pur (pas de reseau) -> testable directement
    sur des fixtures HTML.

    Ne leve jamais d'exception : toute page qui ne ressemble pas a une page
    effectif (0 lien joueur `hauptlink` sur une page non vide) -> statut
    "structure_error". Une page qui parse mais avec moins de `MIN_SQUAD`
    joueurs -> "empty". Sinon "ok".
    """
    if not html_text or not html_text.strip():
        return SquadResult(club_id=club_id, players=[], status="structure_error", raw_html=html_text or "")

    players = [player for row in _split_rows(html_text) if (player := _parse_row(row)) is not None]

    if not players:
        return SquadResult(club_id=club_id, players=[], status="structure_error", raw_html=html_text)

    if len(players) < MIN_SQUAD:
        return SquadResult(club_id=club_id, players=players, status="empty", raw_html=html_text)

    return SquadResult(club_id=club_id, players=players, status="ok", raw_html=html_text)


async def fetch_club_squad(client: TransfermarktClient, tm_club_id: int) -> SquadResult:
    """Recupere puis parse la page effectif Transfermarkt du club `tm_club_id`.

    Ne remonte jamais d'exception reseau/HTTP : un echec du `TransfermarktClient`
    (timeout, HTTP != 200 apres retries, etc.) est converti en
    `SquadResult(status="structure_error")` plutot que de se propager.
    """
    url = f"{BASE_URL}/x/kader/verein/{tm_club_id}"
    try:
        resp = client.get(url, what=f"la page effectif Transfermarkt du club {tm_club_id}")
    except TransfermarktError as exc:
        logger.warning("Echec recuperation effectif TM du club %d : %s", tm_club_id, exc)
        return SquadResult(club_id=tm_club_id, players=[], status="structure_error", raw_html="")

    return parse_squad(resp.text, tm_club_id)
