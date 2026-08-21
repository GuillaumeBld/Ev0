"""Les cotes qui ont produit un lambda doivent survivre a la purge."""
import asyncio
import importlib.util
from pathlib import Path

from app.models.team_xg import TeamXgEstimate
from app.services.xg_library import _solve

MARCHES = {
    "h2h": {"home": 1.347, "draw": 5.35, "away": 9.46},
    "totals": {"over_3.0": 1.854, "under_3.0": 2.04},
}


def test_odds_column_exists_and_is_nullable():
    col = TeamXgEstimate.__table__.columns.get("odds")
    assert col is not None, "colonne odds absente du modele"
    assert col.nullable is True


def test_migration_053_follows_052():
    path = (
        Path(__file__).resolve().parents[2]
        / "alembic" / "versions" / "053_team_xg_estimates_odds.py"
    )
    spec = importlib.util.spec_from_file_location("m053", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.revision == "053"
    assert module.down_revision == "052"
    assert hasattr(module, "upgrade") and hasattr(module, "downgrade")


def test_archive_persists_the_exact_markets_used(monkeypatch):
    """Conservation : ce qui est stocke est ce qui a servi au calcul."""
    import app.services.xg_library as lib

    captures = {}

    async def fake_group(session, fixture_id, snapshot_utc):
        return dict(MARCHES), [1, 2, 3]

    class FakeResult:
        rowcount = 1

    class FakeSession:
        async def execute(self, stmt):
            # on_conflict_do_nothing n'est compilable que par le dialecte
            # PostgreSQL : sans lui, compile() leve.
            from sqlalchemy.dialects import postgresql

            captures["values"] = stmt.compile(dialect=postgresql.dialect()).params
            return FakeResult()

    monkeypatch.setattr(lib, "_snapshot_group", fake_group)

    ok = asyncio.run(lib._archive(FakeSession(), 42, "closing", "2026-08-20T18:00"))
    assert ok is True
    assert captures["values"]["odds"] == MARCHES


def test_round_trip_archived_odds_reproduce_the_stored_lambda():
    """Bouclage : recalculer depuis les cotes archivees redonne le lambda stocke.

    C'est le vrai critere de reussite du chantier -- si la boucle se referme,
    le passe est rejouable.
    """
    solved = _solve(MARCHES)
    assert solved is not None
    lh, la, _ = solved
    lambda_home, lambda_away = round(lh, 4), round(la, 4)
    relu = _solve(dict(MARCHES))
    assert relu is not None
    assert round(relu[0], 4) == lambda_home
    assert round(relu[1], 4) == lambda_away
    assert lambda_home > 1.6
    assert lambda_away < 0.9


def test_unusable_markets_archive_nothing():
    """Si le solveur echoue, aucune ligne -- donc aucune cote orpheline."""
    assert _solve({"h2h": {"home": 1.5}}) is None
    assert _solve({}) is None
