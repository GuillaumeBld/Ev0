"""Bet365 : lecture du « Pari personnalisé » d'un match, depuis le texte de la page.

Bet365 n'est pas lu par le serveur : le texte vient d'une page ouverte dans un
navigateur (Claude in Chrome), collé tel quel. Ce module ne fait que le
comprendre et l'enregistrer.

Pourquoi le « Pari personnalisé » et pas l'onglet « Buteurs » : relevé du
24/11/2026, l'onglet Buteurs affichait des cotes en retard (Haaland passeur
3,98 alors que le panier donnait 4,18) ; le Pari personnalisé est à jour.

Forme du texte (page du match, onglet Pari personnalisé, « Afficher plus »
cliqué), marché « Joueur décisif » :

    Joueur décisif
    REMPLAÇANT+
    Joueur / 5 derniers
    9                 <- numéro de maillot (facultatif)
    Erling Haaland
    2                 <- buts des 5 derniers matchs (ou « N/A »)
    ...
    Buteur
    1.40
    ...               <- une cote par joueur, dans le même ordre
    Passeur
    4.18
    ...
    Décisif
    1.24
    ...
    Afficher moins

Le site s'affiche parfois en anglais (« Score or Assist », « Player / Last 5 »,
« Score », « Assist ») : les deux écritures sont reconnues.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import UTC, datetime

from app.ingestion.scrape_result import AVEC_SUB, SANS_SUB, MatchScrapeResult, PlayerOdds

BOOKMAKER = "bet365"

_MARKET_TITLES = ("joueur décisif", "joueur decisif", "score or assist")
_PLAYERS_HEADER = ("joueur / 5 derniers", "player / last 5")
_GOAL_HEADERS = ("buteur", "score")
_ASSIST_HEADERS = ("passeur", "assist")
_EITHER_HEADERS = ("décisif", "decisif", "score or assist")
_SUB_MARKER = ("remplaçant+", "remplacant+", "substitute+", "sub+")
_ODDS_RE = re.compile(r"^\d{1,3}\.\d{2}$")
_HAS_LETTER = re.compile(r"[^\W\d_]")


class Bet365ParseError(ValueError):
    """Le texte ne ressemble pas au marché « Joueur décisif » attendu."""


@dataclass
class Bet365Match:
    players: list[str] = field(default_factory=list)
    goal: list[float | None] = field(default_factory=list)
    assist: list[float | None] = field(default_factory=list)
    avec_sub: bool = False


def _odds_block(lines: list[str], start: int, n: int) -> tuple[list[float | None], int]:
    """n cotes à partir de `start` ; None pour une cote absente (ligne non numérique)."""
    out: list[float | None] = []
    i = start
    while i < len(lines) and len(out) < n and _ODDS_RE.match(lines[i]):
        out.append(float(lines[i]))
        i += 1
    return out, i


def parse_bet_builder_text(text: str) -> Bet365Match:
    """Joueurs et cotes buteur / passeur du marché « Joueur décisif ».

    Lève Bet365ParseError si le marché est absent ou si le nombre de cotes ne
    correspond pas au nombre de joueurs : on n'enregistre jamais un décalage
    (la cote d'un joueur attribuée au suivant).
    """
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    low = [ln.lower() for ln in lines]

    try:
        title = next(i for i, ln in enumerate(low) if ln in _MARKET_TITLES)
        header = next(i for i in range(title, len(low)) if low[i] in _PLAYERS_HEADER)
    except StopIteration:
        raise Bet365ParseError("marché « Joueur décisif » introuvable dans le texte") from None

    avec_sub = any(ln in _SUB_MARKER for ln in low[title:header])

    # Joueurs : toute ligne contenant une lettre, jusqu'à l'en-tête « Buteur ».
    try:
        goal_at = next(i for i in range(header + 1, len(low)) if low[i] in _GOAL_HEADERS)
    except StopIteration:
        raise Bet365ParseError("colonne « Buteur » introuvable") from None
    players = [
        lines[i] for i in range(header + 1, goal_at)
        if _HAS_LETTER.search(lines[i]) and low[i] != "n/a"
    ]
    if not players:
        raise Bet365ParseError("aucun joueur lu")

    goal, i = _odds_block(lines, goal_at + 1, len(players))
    if i >= len(low) or low[i] not in _ASSIST_HEADERS:
        raise Bet365ParseError(
            f"{len(players)} joueurs mais {len(goal)} cotes buteur : colonnes décalées"
        )
    assist, j = _odds_block(lines, i + 1, len(players))
    if j >= len(low) or low[j] not in _EITHER_HEADERS:
        raise Bet365ParseError(
            f"{len(players)} joueurs mais {len(assist)} cotes passeur : colonnes décalées"
        )
    if len(goal) != len(players) or len(assist) != len(players):
        raise Bet365ParseError(
            f"{len(players)} joueurs, {len(goal)} cotes buteur, {len(assist)} cotes passeur : "
            "colonnes décalées"
        )
    return Bet365Match(players=players, goal=goal, assist=assist, avec_sub=avec_sub)


def to_scrape_result(
    parsed: Bet365Match, fixture_id: int, home_team: str, away_team: str,
    kickoff_utc: datetime | None, league: str,
) -> MatchScrapeResult:
    variant = AVEC_SUB if parsed.avec_sub else SANS_SUB
    return MatchScrapeResult(
        fixture_id=fixture_id, home_team=home_team, away_team=away_team,
        kickoff_utc=kickoff_utc, league=league, bookmaker=BOOKMAKER,
        scraped_at=datetime.now(UTC),
        goalscorer=[PlayerOdds(n, o, variant) for n, o in zip(parsed.players, parsed.goal, strict=True) if o],
        assist=[PlayerOdds(n, o, variant) for n, o in zip(parsed.players, parsed.assist, strict=True) if o],
    )
