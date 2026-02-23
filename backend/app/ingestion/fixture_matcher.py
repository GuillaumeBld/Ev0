"""Match Odds API events to DB fixtures by team names + date window.

The Odds API returns its own event IDs (UUIDs) while our DB fixtures use
FotMob IDs.  This module bridges the two by normalizing team names and
matching within a date window.
"""

import logging
import re
import unicodedata
from datetime import datetime, timedelta
from typing import Any

logger = logging.getLogger(__name__)

# ── Team name normalization ──────────────────────────────────────

# Hardcoded overrides for known mismatches between The Odds API and
# FotMob / FBref team names.  Grow this dict from logs over time.
TEAM_ALIASES: dict[str, str] = {
    "paris saint germain": "paris-saint-germain",
    "paris saint-germain": "paris-saint-germain",
    "paris sg": "paris-saint-germain",
    "psg": "paris-saint-germain",
    "olympique lyonnais": "lyon",
    "olympique de marseille": "marseille",
    "as monaco": "monaco",
    "rc lens": "lens",
    "rc strasbourg alsace": "strasbourg",
    "stade rennais": "rennes",
    "stade de reims": "reims",
    "stade brestois 29": "brest",
    "fc nantes": "nantes",
    "ogc nice": "nice",
    "toulouse fc": "toulouse",
    "le havre ac": "le-havre",
    "clermont foot": "clermont",
    "fc lorient": "lorient",
    "montpellier hsc": "montpellier",
    "angers sco": "angers",
    "aj auxerre": "auxerre",
    "as saint-etienne": "saint-etienne",
    "man city": "manchester-city",
    "man utd": "manchester-united",
    "manchester united": "manchester-united",
    "manchester city": "manchester-city",
    "tottenham hotspur": "tottenham",
    "wolverhampton wanderers": "wolverhampton",
    "wolves": "wolverhampton",
    "newcastle united": "newcastle",
    "west ham united": "west-ham",
    "nottingham forest": "nottm-forest",
    "nott'm forest": "nottm-forest",
    "leicester city": "leicester",
    "brighton and hove albion": "brighton",
    "brighton hove albion": "brighton",
    "crystal palace": "crystal-palace",
    "ipswich town": "ipswich",
}


def normalize_team_name(name: str) -> str:
    """Normalize a team name for fuzzy matching.

    - Strip accents
    - Lowercase
    - Remove FC / AS / SC / AC suffixes/prefixes
    - Remove punctuation
    - Collapse whitespace to single hyphen
    """
    # Remove accents
    normalized = unicodedata.normalize("NFKD", name)
    normalized = "".join(c for c in normalized if not unicodedata.combining(c))

    normalized = normalized.lower().strip()

    # Check alias map first (on lowered original, before stripping suffixes)
    if normalized in TEAM_ALIASES:
        return TEAM_ALIASES[normalized]

    # Remove common suffixes/prefixes
    normalized = re.sub(r"\b(fc|as|sc|ac|rc|aj|ogc|hsc|sco)\b", "", normalized)

    # Remove punctuation
    normalized = re.sub(r"[.,'\"-]", "", normalized)

    # Collapse whitespace → single hyphen
    normalized = re.sub(r"\s+", " ", normalized).strip()
    normalized = normalized.replace(" ", "-").strip("-")

    return normalized


# ── Date window matching ─────────────────────────────────────────

_DATE_WINDOW = timedelta(hours=36)


def _parse_commence_time(event: dict[str, Any]) -> datetime | None:
    """Parse the commence_time ISO string from an Odds API event."""
    raw = event.get("commence_time", "")
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None


def _kickoff_within_window(
    fixture_kickoff: datetime, event_time: datetime
) -> bool:
    """Return True if fixture kickoff is within +/-36h of event time."""
    return abs(fixture_kickoff - event_time) <= _DATE_WINDOW


# ── Main matcher ─────────────────────────────────────────────────


def match_odds_event_to_fixture(
    event: dict[str, Any],
    fixtures: list[Any],
) -> Any | None:
    """Match a single Odds API event to a DB Fixture.

    Strategy (in order):
    1. **Cached ID**: if any fixture already has ``odds_api_event_id == event["id"]``,
       return it immediately.
    2. **Team-name + date match**: normalize both teams and look for a fixture
       where BOTH home *and* away teams match, within a 36-hour date window.
       A single-team match is explicitly rejected (log warning).

    Returns the matched ``Fixture`` ORM object, or ``None``.
    """
    event_id = event.get("id", "")

    # ── Fast path: cached odds_api_event_id ──────────────────────
    for fixture in fixtures:
        if fixture.odds_api_event_id and fixture.odds_api_event_id == event_id:
            return fixture

    # ── Slow path: team name + date matching ─────────────────────
    event_home = normalize_team_name(event.get("home_team", ""))
    event_away = normalize_team_name(event.get("away_team", ""))
    event_time = _parse_commence_time(event)

    if not event_home or not event_away:
        logger.warning("Event %s missing team names, skipping", event_id)
        return None

    single_match = None  # track partial matches for warning

    for fixture in fixtures:
        # Date window filter (if we can parse both dates)
        if (
            event_time
            and hasattr(fixture, "kickoff_utc")
            and fixture.kickoff_utc
            and not _kickoff_within_window(fixture.kickoff_utc, event_time)
        ):
            continue

        fix_home = normalize_team_name(fixture.home_team)
        fix_away = normalize_team_name(fixture.away_team)

        home_match = fix_home == event_home
        away_match = fix_away == event_away

        if home_match and away_match:
            return fixture

        # Track single-team match for safety logging
        if home_match or away_match:
            single_match = fixture

    if single_match:
        logger.warning(
            "Event %s (%s vs %s) matched only ONE team to fixture %s (%s vs %s) — skipping for safety",
            event_id,
            event.get("home_team"),
            event.get("away_team"),
            single_match.external_id,
            single_match.home_team,
            single_match.away_team,
        )

    return None
