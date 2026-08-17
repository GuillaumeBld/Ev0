"""La colonne alerted_odds memorise le dernier plus haut notifie."""
import importlib.util
from pathlib import Path

from app.models.recommendations import Recommendation


def test_recommendation_has_alerted_odds_column():
    col = Recommendation.__table__.columns.get("alerted_odds")
    assert col is not None, "colonne alerted_odds absente du modèle"
    assert col.nullable is True


def test_migration_050_follows_049():
    path = (
        Path(__file__).resolve().parents[1]
        / "alembic"
        / "versions"
        / "050_recommendation_alerted_odds.py"
    )
    spec = importlib.util.spec_from_file_location("m050", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    assert module.revision == "050"
    assert module.down_revision == "049"
    assert hasattr(module, "upgrade")
    assert hasattr(module, "downgrade")
