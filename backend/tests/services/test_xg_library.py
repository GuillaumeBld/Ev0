import importlib.util
from pathlib import Path

from app.models.team_xg import TeamXgEstimate


def test_phase_column_exists():
    col = TeamXgEstimate.__table__.columns.get("phase")
    assert col is not None, "colonne phase absente du modele"
    assert col.nullable is False


def test_unique_constraint_on_fixture_and_phase():
    names = {c.name for c in TeamXgEstimate.__table__.constraints}
    cols = {
        tuple(sorted(c.columns.keys()))
        for c in TeamXgEstimate.__table__.constraints
        if hasattr(c, "columns")
    }
    assert ("fixture_id", "phase") in cols, f"contrainte absente, presentes: {cols} {names}"


def test_migration_052_follows_051():
    path = (
        Path(__file__).resolve().parents[2]
        / "alembic" / "versions" / "052_team_xg_estimates_phase.py"
    )
    spec = importlib.util.spec_from_file_location("m052", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.revision == "052"
    assert module.down_revision == "051"


def test_library_is_never_purged():
    """job_purge_old_snapshots ne doit toucher que les deux tables de snapshots."""
    src = (Path(__file__).resolve().parents[2] / "app" / "worker.py").read_text()
    start = src.index("async def job_purge_old_snapshots")
    body = src[start:start + 2500]
    assert "team_xg_estimates" not in body
    assert "match_odds_snapshots" in body
