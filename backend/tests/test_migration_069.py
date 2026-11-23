"""Migration 069 : identifiant Atlas sur team_lineup_players."""
from __future__ import annotations

import importlib.util
from pathlib import Path
from unittest.mock import patch

_VERSIONS = Path(__file__).resolve().parents[1] / "alembic" / "versions"


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, _VERSIONS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_migration_069_ajoute_player_api_id_nullable_indexe():
    mig = _load("069_team_lineup_players_player_api_id")
    prev = _load("068_backfill_competition_fixtures")
    assert mig.down_revision == prev.revision

    with patch.object(mig, "op") as op:
        mig.upgrade()
    table, col = op.add_column.call_args.args
    assert table == "team_lineup_players"
    assert col.name == "player_api_id" and col.nullable is True
    assert op.create_index.call_args.args == (
        "ix_team_lineup_players_player_api_id", "team_lineup_players", ["player_api_id"],
    )


def test_migration_069_downgrade_retire_la_colonne():
    mig = _load("069_team_lineup_players_player_api_id")
    with patch.object(mig, "op") as op:
        mig.downgrade()
    op.drop_index.assert_called_once_with(
        "ix_team_lineup_players_player_api_id", table_name="team_lineup_players",
    )
    op.drop_column.assert_called_once_with("team_lineup_players", "player_api_id")


def test_modele_porte_player_api_id():
    from app.models.lineups import TeamLineupPlayer

    col = TeamLineupPlayer.__table__.c.player_api_id
    assert col.nullable and col.index
