"""Resolution fixture <-> evenement PS3838.

SEUL endroit du chantier ou un nom d'equipe est compare. Une fois l'identifiant
pose, les cotes sont recuperees par identifiant : plus aucun rapprochement
approximatif au moment du scraping.
"""
from __future__ import annotations

import logging
import re
import unicodedata
from datetime import UTC, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ingestion.ps3838.client import Ps3838Event

logger = logging.getLogger(__name__)

MAX_KICKOFF_DELTA = timedelta(hours=2)

_STOP = {
    "fc", "cf", "sc", "ac", "cd", "ud", "sd", "as", "ss", "ssc", "afc", "rc",
    "club", "de", "la", "le", "los", "el", "sk", "nk", "gnk", "calcio", "cfr",
}


def norm_team(name: str) -> set[str]:
    """Tokens normalises : accents plies, suffixes de club retires."""
    s = unicodedata.normalize("NFKD", name or "").encode("ascii", "ignore").decode().lower()
    s = re.sub(r"[^a-z0-9 ]", " ", s)
    return {t for t in s.split() if t and t not in _STOP and len(t) > 2}


def _same_team(a: str, b: str) -> bool:
    ta, tb = norm_team(a), norm_team(b)
    if not ta or not tb:
        return False
    # Un cote doit etre entierement contenu dans l'autre : 'Real Madrid' et
    # 'Real Sociedad' partagent 'real' mais ne se contiennent pas.
    return ta <= tb or tb <= ta


def match_event(fixture, events: list[Ps3838Event]) -> Ps3838Event | None:
    """Evenement correspondant, ou None. Ne devine jamais.

    Exige les deux equipes dans le bon sens ET un coup d'envoi a +/- 2 h.
    Renvoie None si plusieurs candidats subsistent.
    """
    ko = fixture.kickoff_utc
    if ko is None:
        return None
    if ko.tzinfo is None:
        ko = ko.replace(tzinfo=UTC)

    hits = [
        ev for ev in events
        if abs(ev.kickoff_utc - ko) <= MAX_KICKOFF_DELTA
        and _same_team(fixture.home_team, ev.home)
        and _same_team(fixture.away_team, ev.away)
    ]
    return hits[0] if len(hits) == 1 else None


async def resolve_anchors(
    session: AsyncSession, events: list[Ps3838Event]
) -> tuple[int, list[str]]:
    """Pose ps3838_event_id sur les fixtures a venir non encore ancrees.

    Retourne (nb_resolus, libelles_non_resolus). Les non-resolus sont retournes
    pour surfacage, jamais devines.
    """
    from datetime import datetime

    from app.models.fixtures import Fixture

    now = datetime.now(UTC)
    rows = (await session.execute(
        select(Fixture).where(
            Fixture.ps3838_event_id.is_(None),
            Fixture.kickoff_utc > now,
            Fixture.status.notin_(["finished", "cancelled", "postponed"]),
        )
    )).scalars().all()

    taken = set(
        (await session.execute(
            select(Fixture.ps3838_event_id).where(Fixture.ps3838_event_id.isnot(None))
        )).scalars().all()
    )

    resolved = 0
    unresolved: list[str] = []
    for fx in rows:
        ev = match_event(fx, events)
        if ev is None or ev.event_id in taken:
            unresolved.append(f"{fx.home_team} - {fx.away_team} ({fx.kickoff_utc:%d/%m %H:%M})")
            continue
        fx.ps3838_event_id = ev.event_id
        taken.add(ev.event_id)
        resolved += 1

    await session.commit()
    logger.info("PS3838 anchor: %d resolus, %d non resolus", resolved, len(unresolved))
    return resolved, unresolved
