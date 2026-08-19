import importlib.util
from pathlib import Path

from app.models.fixtures import Fixture


def test_fixture_has_ps3838_event_id():
    col = Fixture.__table__.columns.get("ps3838_event_id")
    assert col is not None, "colonne ps3838_event_id absente du modele"
    assert col.nullable is True


def test_migration_051_follows_050():
    path = (
        Path(__file__).resolve().parents[1]
        / "alembic" / "versions" / "051_fixtures_ps3838_event_id.py"
    )
    spec = importlib.util.spec_from_file_location("m051", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.revision == "051"
    assert module.down_revision == "050"
    assert hasattr(module, "upgrade") and hasattr(module, "downgrade")
