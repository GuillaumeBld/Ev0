"""Client PS3838 — cotes 1X2 et totals depuis la categorie football.

PS3838 est une declinaison de la plateforme Pinnacle : memes identifiants
d'evenements, memes prix, mais joignable depuis le VPS la ou
guest.api.arcadia.pinnacle.com repond 403.

Pas de page par competition : tout passe par sportId=29.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime

import httpx

logger = logging.getLogger(__name__)

_BASE = "https://www.ps3838.com/sports-service/sv/compact/events"
_SOCCER = 29
_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/140.0 Safari/537.36"
)
_TIMEOUT = 30.0

# Deux flux distincts, interroges a chaque cycle puis fusionnes. Un match a
# 3h du coup d'envoi peut n'etre dans aucun des deux.
_QUERY_IMMINENT = {"sp": _SOCCER}  # matchs a venir sous ~2h
_QUERY_UPCOMING = {"sp": _SOCCER, "mk": 0, "pa": 0}  # matchs a partir du lendemain

# Ordre de fusion explicite : "upcoming" est ecrit en premier, puis
# "imminent" ecrase les doublons — ses cotes sont les plus fraiches pres du
# coup d'envoi, c'est donc lui qui doit primer.
_QUERIES_IN_MERGE_ORDER = (_QUERY_UPCOMING, _QUERY_IMMINENT)


@dataclass
class Ps3838Event:
    event_id: int
    home: str
    away: str
    kickoff_utc: datetime
    league: str
    h2h: dict[str, float] | None = None
    totals: dict[str, float] | None = None
    total_line: float | None = None


def _to_decimal_odds(value) -> float | None:
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return f if f > 1.0 else None


def _parse_h2h(raw) -> dict[str, float] | None:
    """PS3838 range le 1X2 en [exterieur, domicile, nul]."""
    if not raw or len(raw) < 3:
        return None
    away, home, draw = _to_decimal_odds(raw[0]), _to_decimal_odds(raw[1]), _to_decimal_odds(raw[2])
    if home is None or draw is None or away is None:
        return None
    return {"home": home, "draw": draw, "away": away}


def _parse_totals(raw) -> tuple[float | None, dict[str, float] | None]:
    """Retient la ligne principale la plus proche de 2.5.

    Les lignes quart (label contenant '-', ex. '3-3.5') sont ignorees : elles
    ne se resolvent pas par un simple over/under.
    """
    if not raw:
        return None, None

    candidates: list[tuple[float, float, float]] = []
    for entry in raw:
        if not entry or len(entry) < 4:
            continue
        label, line, over, under = (
            entry[0],
            entry[1],
            _to_decimal_odds(entry[2]),
            _to_decimal_odds(entry[3]),
        )
        if isinstance(label, str) and "-" in label:
            continue
        try:
            line = float(line)
        except (TypeError, ValueError):
            continue
        if over is None or under is None:
            continue
        candidates.append((line, over, under))

    if not candidates:
        return None, None

    # Ligne demi-entiere privilegiee (pas de push), puis proximite a 2.5.
    line, over, under = min(
        candidates, key=lambda c: (abs(c[0] - round(c[0])) < 1e-9, abs(c[0] - 2.5))
    )
    key = f"{line:g}" if line != int(line) else f"{line:.1f}"
    return line, {f"over_{key}": over, f"under_{key}": under}


def parse_events(payload: dict) -> list[Ps3838Event]:
    """Extrait les evenements de tous les blocs exploitables du payload."""
    out: dict[int, Ps3838Event] = {}
    for block in (payload or {}).values():
        if not isinstance(block, list):
            continue
        for sport in block:
            if not (isinstance(sport, list) and len(sport) > 2 and isinstance(sport[2], list)):
                continue
            for league in sport[2]:
                if not (isinstance(league, list) and len(league) > 2 and isinstance(league[2], list)):
                    continue
                league_name = league[1] if isinstance(league[1], str) else ""
                for ev in league[2]:
                    if not (isinstance(ev, list) and len(ev) > 8 and isinstance(ev[0], int)):
                        continue
                    periods = ev[8] if isinstance(ev[8], dict) else {}
                    full = periods.get("0") or []
                    h2h = _parse_h2h(full[2] if len(full) > 2 else None)
                    line, totals = _parse_totals(full[1] if len(full) > 1 else None)
                    try:
                        ko = datetime.fromtimestamp(ev[4] / 1000, UTC)
                    except (TypeError, ValueError, OSError):
                        continue
                    out[ev[0]] = Ps3838Event(
                        event_id=ev[0],
                        home=str(ev[1]),
                        away=str(ev[2]),
                        kickoff_utc=ko,
                        league=league_name,
                        h2h=h2h,
                        totals=totals,
                        total_line=line,
                    )
    return list(out.values())


async def fetch_events() -> list[Ps3838Event]:
    """Les deux flux, fusionnes. L'imminent prime en cas de doublon."""
    merged: dict[int, Ps3838Event] = {}
    async with httpx.AsyncClient(
        timeout=_TIMEOUT, headers={"User-Agent": _UA, "Accept": "application/json"}
    ) as client:
        for params in _QUERIES_IN_MERGE_ORDER:
            try:
                r = await client.get(_BASE, params=params)
                r.raise_for_status()
                for ev in parse_events(r.json()):
                    merged[ev.event_id] = ev
            except Exception as exc:
                logger.warning("PS3838: appel %s echoue: %s", params, exc)
    logger.info("PS3838: %d evenements", len(merged))
    return list(merged.values())
