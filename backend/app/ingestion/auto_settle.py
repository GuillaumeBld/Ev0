"""auto_settle.py — automatic settlement of approved recommendations via Understat.

For each approved recommendation with result=None:
  1. Check if the fixture is finished
  2. Find the corresponding Understat match (by team names + date)
  3. Fetch the match roster from Understat
  4. Determine result: VOID (0 minutes), WON (goal or assist), LOST (played but no event)
  5. Update recommendation: result, pnl, settled_utc
"""

import asyncio
import logging
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ingestion.understat_match import (
    MatchRef,
    PlayerMatchRow,
    fetch_league_match_ids,
    fetch_match_roster,
)
from app.models.fixtures import Fixture
from app.models.recommendations import Recommendation

logger = logging.getLogger(__name__)

RATE_LIMIT = 2.0  # seconds between Understat match requests

# Leagues to auto-settle (must match fixture.league values in DB)
LEAGUES = ["ligue_1", "premier_league", "bundesliga", "la_liga", "serie_a"]

# Understat team name (lowercase) → our DB team name (lowercase)
TEAM_NAME_MAP = {
    # Premier League
    "manchester city": "man city",
    "manchester united": "man utd",
    "nottingham forest": "nott'm forest",
    "newcastle united": "newcastle",
    "wolverhampton wanderers": "wolves",
    "tottenham hotspur": "tottenham",
    "brighton & hove albion": "brighton",
    "west ham united": "west ham",
    "leicester city": "leicester",
    "ipswich town": "ipswich",
    # Ligue 1
    "paris saint-germain": "psg",
    "olympique de marseille": "marseille",
    "olympique lyonnais": "lyon",
    "stade de reims": "reims",
    "stade brestois 29": "brest",
    "rc strasbourg alsace": "strasbourg",
    "stade rennais fc": "rennes",
    "fc nantes": "nantes",
    "ogc nice": "nice",
    "montpellier hsc": "montpellier",
    "rc lens": "lens",
    "toulouse fc": "toulouse",
    # Bundesliga
    "borussia dortmund": "dortmund",
    "bayer leverkusen": "leverkusen",
    "eintracht frankfurt": "frankfurt",
    "vfb stuttgart": "stuttgart",
    "sc freiburg": "freiburg",
    "1. fc union berlin": "union berlin",
    "1. fsv mainz 05": "mainz",
    "fc augsburg": "augsburg",
    "1. fc heidenheim 1846": "heidenheim",
    "sv werder bremen": "werder bremen",
    "borussia mönchengladbach": "gladbach",
    "vfl wolfsburg": "wolfsburg",
    "vfl bochum": "bochum",
    "fc st. pauli": "st. pauli",
    # La Liga
    "atletico madrid": "atlético madrid",
    "deportivo alaves": "alavés",
    "leganes": "leganés",
    "real valladolid": "valladolid",
    # Serie A
    "ac milan": "milan",
    "hellas verona": "verona",
}


def _normalize(name: str) -> str:
    """Lowercase + strip for fuzzy matching."""
    return name.lower().strip()


def _match_team(understat_name: str, db_name: str) -> bool:
    """Check if an Understat team name corresponds to a DB team name."""
    un = _normalize(understat_name)
    db = _normalize(db_name)
    if un == db:
        return True
    mapped = TEAM_NAME_MAP.get(un, un)
    return mapped == db


def _find_understat_match(
    refs: list[MatchRef],
    home_team: str,
    away_team: str,
    kickoff_date,
) -> MatchRef | None:
    """Find the Understat match ref for a given fixture."""
    for ref in refs:
        if ref.match_date != kickoff_date:
            continue
        if _match_team(ref.home_team, home_team) and _match_team(ref.away_team, away_team):
            return ref
    return None


def _determine_result(
    player_name: str,
    market_type: str,
    roster: list[PlayerMatchRow],
) -> tuple[str | None, float | None]:
    """Return (result, pnl_override) for a recommendation given the match roster.

    result: 'won', 'lost', 'void', or None (unknown market — skip)
    pnl_override: set for void (0.0), None otherwise (computed from odds)
    """
    name_lower = player_name.lower()
    player_row: PlayerMatchRow | None = None

    # Exact match first
    for row in roster:
        if row.player_name.lower() == name_lower:
            player_row = row
            break

    # Fuzzy fallback: substring match
    if player_row is None:
        for row in roster:
            rn = row.player_name.lower()
            if name_lower in rn or rn in name_lower:
                player_row = row
                break

    # Not on team sheet → VOID
    if player_row is None:
        logger.debug("Player '%s' not found in roster → VOID", player_name)
        return "void", 0.0

    # On bench / unused sub → VOID
    if player_row.minutes == 0:
        return "void", 0.0

    # Played — check event
    if market_type in ("goalscorer", "anytime_score"):
        won = player_row.goals >= 1
    elif market_type in ("assist", "anytime_assist"):
        won = player_row.assists >= 1
    else:
        return None, None  # unknown market

    return ("won" if won else "lost"), None


async def settle_approved_recommendations(db: AsyncSession) -> int:
    """Settle all unsettled approved recommendations for finished fixtures.

    Returns the number of recommendations settled.
    """
    stmt = (
        select(Recommendation, Fixture)
        .join(Fixture, Recommendation.fixture_id == Fixture.id)
        .where(
            Recommendation.status == "approved",
            Recommendation.result.is_(None),
            Fixture.status == "finished",
        )
    )
    rows = (await db.execute(stmt)).all()

    if not rows:
        logger.info("auto_settle: no unsettled approved recs with finished fixtures")
        return 0

    # Fetch Understat match calendars per league (one request per league)
    leagues_needed = {fixture.league for _, fixture in rows}
    by_league: dict[str, list[MatchRef]] = {}
    for league in leagues_needed:
        if league not in LEAGUES:
            continue
        try:
            refs = await fetch_league_match_ids(league)
            by_league[league] = refs
            logger.info("auto_settle: %d match refs for %s", len(refs), league)
        except Exception as e:
            logger.warning("auto_settle: failed to fetch matches for %s: %s", league, e)

    # Settle each recommendation
    roster_cache: dict[str, list[PlayerMatchRow]] = {}
    settled = 0

    for rec, fixture in rows:
        refs = by_league.get(fixture.league, [])
        match_ref = _find_understat_match(
            refs,
            fixture.home_team,
            fixture.away_team,
            fixture.kickoff_utc.date(),
        )
        if match_ref is None:
            logger.warning(
                "auto_settle: no Understat match for %s vs %s on %s",
                fixture.home_team,
                fixture.away_team,
                fixture.kickoff_utc.date(),
            )
            continue

        if match_ref.understat_id not in roster_cache:
            try:
                roster_cache[match_ref.understat_id] = await fetch_match_roster(
                    match_ref.understat_id
                )
                await asyncio.sleep(RATE_LIMIT)
            except Exception as e:
                logger.warning(
                    "auto_settle: failed to fetch roster %s: %s", match_ref.understat_id, e
                )
                continue

        roster = roster_cache[match_ref.understat_id]
        result, pnl_override = _determine_result(rec.player_name, rec.market_type, roster)
        if result is None:
            continue

        if pnl_override is not None:
            pnl = pnl_override
        elif result == "won":
            pnl = round(10.0 * (rec.best_odds - 1), 2)
        elif result == "lost":
            pnl = -10.0
        else:
            pnl = 0.0

        rec.result = result
        rec.pnl = pnl
        rec.settled_utc = datetime.now(UTC)
        settled += 1
        logger.info(
            "auto_settle: rec %d (%s %s) → %s pnl=%.2f",
            rec.id, rec.player_name, rec.market_type, result, pnl,
        )

    await db.commit()
    logger.info("auto_settle: committed %d settlements", settled)
    return settled
