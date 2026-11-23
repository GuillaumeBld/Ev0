"""Cotes books par joueur d'un match, pour le calculateur.

Derniere cote par joueur x marche x book, uniquement les lignes reliees a un
joueur Atlas (player_api_id renseigne) : jamais de rapprochement par nom.
"""
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

from sqlalchemy.dialects import postgresql

from app.api.fixtures import (
    FixturePlayerOddOut,
    _player_odds_stmt,
    get_fixture_player_odds,
    player_odds_rows_out,
)


def _sql(stmt) -> str:
    return str(stmt.compile(dialect=postgresql.dialect())).lower()


def test_requete_unique_derniere_cote_par_joueur_marche_book():
    sql = _sql(_player_odds_stmt(42))
    assert "distinct on" in sql
    assert "player_api_id is not null" in sql
    assert "scraped_at desc" in sql
    assert "player_odds_snapshots.fixture_id =" in sql
    # Seuls les deux marches lus par l'ecran
    assert "market_type in" in sql


def test_marches_renommes_goal_et_assist():
    t = datetime(2026, 11, 23, 14, 22, tzinfo=UTC)
    rows = [
        (101, "goalscorer", "betclic", 1.35, t),
        (101, "assist", "unibet", 3.65, t),
    ]
    out = player_odds_rows_out(rows)
    assert out == [
        FixturePlayerOddOut(player_api_id=101, market="goal", bookmaker="betclic", odds=1.35, scraped_at=t.isoformat()),
        FixturePlayerOddOut(player_api_id=101, market="assist", bookmaker="unibet", odds=3.65, scraped_at=t.isoformat()),
    ]


async def test_endpoint_rend_les_lignes_de_la_requete():
    t = datetime(2026, 11, 23, 14, 22, tzinfo=UTC)
    result = MagicMock()
    result.all.return_value = [(7, "goalscorer", "pmu", 4.0, t)]
    db = MagicMock()
    db.execute = AsyncMock(return_value=result)

    out = await get_fixture_player_odds(42, db)

    assert db.execute.await_count == 1
    assert out == [
        FixturePlayerOddOut(player_api_id=7, market="goal", bookmaker="pmu", odds=4.0, scraped_at=t.isoformat())
    ]


def test_route_declaree():
    from app.api.fixtures import router

    paths = {(r.path, tuple(sorted(r.methods))) for r in router.routes}
    assert ("/fixtures/{fixture_id}/player-odds", ("GET",)) in paths
