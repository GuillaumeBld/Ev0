"""Migration 068 : rattrapage de fixtures.competition_api_id."""
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


def test_migration_068_rattrape_les_identifiants_sans_toucher_aux_etiquettes():
    mig = _load("068_backfill_competition_fixtures")
    prev = _load("067_competition_et_joueur_atlas")
    assert mig.down_revision == prev.revision

    with patch.object(mig, "op") as op:
        mig.upgrade()
    sql = op.execute.call_args.args[0]
    assert "SET competition_api_id = e.league_api_id" in sql
    assert "f.competition_api_id IS NULL" in sql
    assert "'bzz_' || e.api_id" in sql
    assert "league =" not in sql.replace("league_api_id", "")


def test_migration_068_downgrade_est_un_no_op():
    mig = _load("068_backfill_competition_fixtures")
    with patch.object(mig, "op") as op:
        mig.downgrade()
    assert not op.method_calls
