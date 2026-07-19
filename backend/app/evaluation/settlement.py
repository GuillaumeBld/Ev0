"""Settlement des tickets joueur en convention "avec sub" (spec 2026-07-18, §3.2).

Un ticket goal_with_sub/assist_with_sub est gagnant si le joueur nommé OU un
joueur de sa chaîne de remplacement (transitive : remplaçant du remplaçant)
réalise l'action. Tous les noms sont normalisés (accents/casse) en entrée.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ingestion.auto_settle import _normalize_name
from app.models.match_events import MatchEvent
from app.pricing.model_registry import KNOWN_MARKETS

_GOAL_TYPES = ("goal", "penalty_goal")


@dataclass
class FixtureEvents:
    """Événements d'un match, noms déjà normalisés."""

    goals: list[str]
    assists: list[str]
    subs: list[tuple[str, str]]  # (entrant, sortant), ordonnées par minute


def _fixture_events_query(fixture_id: int):
    """Requête des events d'un match, ordre chronologique.

    NULLS FIRST : sync_incidents peut stocker une substitution avec
    minute=NULL (ni time ni minute fournis par l'API). Postgres trie
    NULLS LAST par défaut → une sub NULL précoce passerait APRÈS les subs
    datées, et replacement_chain (qui dépend de l'ordre de la liste)
    raterait la chaîne transitive. Tri secondaire par id pour un ordre
    déterministe à minute égale.
    """
    return (
        select(MatchEvent)
        .where(MatchEvent.fixture_id == fixture_id)
        .order_by(MatchEvent.minute.asc().nulls_first(), MatchEvent.id.asc())
    )


async def load_fixture_events(session: AsyncSession, fixture_id: int) -> FixtureEvents:
    result = await session.execute(_fixture_events_query(fixture_id))
    goals: list[str] = []
    assists: list[str] = []
    subs: list[tuple[str, str]] = []
    for ev in result.scalars():
        name = _normalize_name(ev.player_name)
        if ev.event_type in _GOAL_TYPES:
            goals.append(name)
        elif ev.event_type == "assist":
            assists.append(name)
        elif ev.event_type == "substitution" and ev.related_player_name:
            subs.append((name, _normalize_name(ev.related_player_name)))
    return FixtureEvents(goals=goals, assists=assists, subs=subs)


def replacement_chain(subs: list[tuple[str, str]], player: str) -> set[str]:
    """Remplaçants transitifs d'un joueur (ordre des subs = ordre chronologique)."""
    chain: set[str] = set()
    targets = {player}
    for entrant, sortant in subs:
        if sortant in targets:
            chain.add(entrant)
            targets.add(entrant)
    return chain


def settle(market: str, player_name: str, events: FixtureEvents) -> bool:
    """Règle un ticket. ValueError si le marché n'est pas dans KNOWN_MARKETS."""
    if market not in KNOWN_MARKETS:
        raise ValueError(f"Marché inconnu: {market!r} (admis: {KNOWN_MARKETS})")
    player = _normalize_name(player_name)
    scorers = set(events.goals) if market.startswith("goal") else set(events.assists)
    if player in scorers:
        return True
    if market.endswith("_with_sub"):
        return bool(replacement_chain(events.subs, player) & scorers)
    return False
