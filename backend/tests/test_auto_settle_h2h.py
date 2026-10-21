"""Un 1N2 se regle sur le score a 90', une fois celui-ci stabilise.

Incident du 12/10/2026 : Belgique–Senegal (2-2 a 90', 3-2 a.p.) et
Celje–Slovan (1-1, 1-2 a.p.) avaient ete regles sur le score apres
prolongation capte le soir du match.
"""
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

from app.ingestion.auto_settle import settle_approved_recommendations


def _h2h(outcome: str, kickoff: datetime):
    rec = MagicMock()
    rec.id = 2220
    rec.market_type = "h2h"
    rec.player_name = outcome
    rec.best_odds = 2.3
    rec.result = None
    fixture = MagicMock()
    fixture.id = 1110287
    fixture.home_team = "Belgium"
    fixture.away_team = "Senegal"
    fixture.home_score = 2
    fixture.away_score = 2
    fixture.kickoff_utc = kickoff
    return rec, fixture


def _db(rows):
    result = MagicMock()
    result.all.return_value = rows
    db = MagicMock()
    db.execute = AsyncMock(return_value=result)
    db.commit = AsyncMock()
    return db


async def test_h2h_waits_until_the_score_is_stable():
    rec, fixture = _h2h("Belgium", datetime.now(UTC) - timedelta(hours=3))

    out = await settle_approved_recommendations(_db([(rec, fixture)]))

    assert out["settled"] == 0
    assert rec.result is None


async def test_h2h_settles_on_the_score_once_stable():
    rec, fixture = _h2h("Belgium", datetime.now(UTC) - timedelta(hours=7))

    out = await settle_approved_recommendations(_db([(rec, fixture)]))

    assert out["settled"] == 1
    assert rec.result == "lost"
    assert rec.pnl == -10.0
