"""Migration 067 : identifiants Atlas sur les matchs et les cotes joueur."""
from __future__ import annotations

import importlib.util
from pathlib import Path
from unittest.mock import patch

from app.models.fixtures import Fixture
from app.models.player_odds_snapshot import PlayerOddsSnapshot

_VERSIONS = Path(__file__).resolve().parents[1] / "alembic" / "versions"


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, _VERSIONS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_migration_067_ajoute_les_deux_colonnes():
    mig = _load("067_competition_et_joueur_atlas")
    prev = _load("066_atlas_toutes_competitions")
    assert mig.down_revision == prev.revision

    with patch.object(mig, "op") as op:
        mig.upgrade()
    added = {(call.args[0], call.args[1].name): call.args[1]
             for call in op.add_column.call_args_list}
    for key in (("fixtures", "competition_api_id"),
                ("player_odds_snapshots", "player_api_id")):
        assert key in added
        assert added[key].nullable is True
    indexed = {(c.args[1], tuple(c.args[2])) for c in op.create_index.call_args_list}
    assert ("fixtures", ("competition_api_id",)) in indexed
    assert ("player_odds_snapshots", ("player_api_id",)) in indexed

    with patch.object(mig, "op") as op:
        mig.downgrade()
    dropped = {(c.args[0], c.args[1]) for c in op.drop_column.call_args_list}
    assert dropped == {("fixtures", "competition_api_id"),
                       ("player_odds_snapshots", "player_api_id")}
    assert op.drop_index.call_count == 2


def test_modeles_portent_les_colonnes_nullables_indexees():
    for model, name in ((Fixture, "competition_api_id"),
                        (PlayerOddsSnapshot, "player_api_id")):
        col = model.__table__.c[name]
        assert col.nullable is True
        assert col.index is True
