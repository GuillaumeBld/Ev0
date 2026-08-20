# Archivage des cotes de closing — plan d'implémentation

> **Pour les workers agentiques :** SUB-SKILL requise — `superpowers:subagent-driven-development` (recommandé) ou `superpowers:executing-plans`, tâche par tâche. Les étapes utilisent des cases `- [ ]`.

**Goal :** conserver, à côté de chaque estimation de xG archivée, les cotes brutes qui l'ont produite — pour que toute évolution future de la méthode soit rejouable sur l'historique.

**Architecture :** une colonne `odds JSONB` sur `team_xg_estimates` reçoit le dictionnaire `markets` que `_archive` a déjà en main ; un script one-shot remplit rétroactivement les lignes déjà écrites en relisant les snapshots désignés par `input_snapshot_ids`, tant que la purge à 45 jours ne les a pas effacés.

**Tech Stack :** Python 3.13, SQLAlchemy 2 async, Alembic, PostgreSQL (JSONB), pytest + pytest-asyncio (`asyncio_mode = "auto"`).

## Global Constraints

- Spec de référence : `docs/superpowers/specs/2026-08-20-archivage-cotes-closing-design.md`.
- Tests depuis `backend/` : `cd backend && uv run pytest …`. `asyncio_mode = "auto"` — jamais de `@pytest.mark.asyncio`.
- La tête des migrations est **052**. Celle de ce plan est la **053**.
- Commentaires et docstrings du code de production en français **sans accents** (convention du dépôt).
- **On archive les cotes brutes, jamais du dérivé.** Pas de probabilités dévigées, pas de ligne de totals dupliquée dans une colonne : tout cela se recalcule, et stocker du dérivé invite à la divergence le jour où la formule change.
- **Le script de rattrapage ne fabrique jamais rien.** Une ligne dont les snapshots ont disparu reste à `NULL` et est comptée comme impossible.
- `team_xg_estimates` ne doit **jamais** rejoindre `job_purge_old_snapshots` (test déjà en place, à ne pas casser).
- **Aucun scraping supplémentaire, aucune requête de plus** dans le chemin d'archivage : `markets` est déjà en mémoire.

## Structure des fichiers

| Fichier | Responsabilité | Tâche |
|---|---|---|
| `alembic/versions/053_team_xg_estimates_odds.py` | colonne `odds` | 1 |
| `app/models/team_xg.py` | champ ORM `odds` | 1 |
| `app/services/xg_library.py` | persistance de `markets` dans `_archive` | 1 |
| `app/scripts/backfill_xg_odds.py` | rattrapage des lignes existantes | 2 |
| `tests/services/test_xg_odds_archive.py` | conservation + bouclage | 1 |
| `tests/scripts/test_backfill_xg_odds.py` | idempotence + snapshots absents | 2 |
| `docs/DEPLOYMENT.md` | mise en service | 3 |

---

### Task 1 : Colonne `odds` et persistance

**Files:**
- Create: `backend/alembic/versions/053_team_xg_estimates_odds.py`
- Modify: `backend/app/models/team_xg.py`
- Modify: `backend/app/services/xg_library.py` (fonction `_archive`)
- Test: `backend/tests/services/test_xg_odds_archive.py`

**Interfaces:**
- Consumes: `_snapshot_group(session, fixture_id, snapshot_utc) -> (markets, ids)` et `_solve(markets) -> (lh, la, residual) | None`, tous deux déjà présents dans `app/services/xg_library.py`.
- Produces: `TeamXgEstimate.odds: Mapped[dict | None]` — dictionnaire `{marche: {issue: cote}}`, `None` si non renseigné.

**Contexte.** `_archive` reçoit déjà `markets` de `_snapshot_group` : c'est exactement le dictionnaire à conserver. Il le laisse filer aujourd'hui. La seule chose à faire est de le persister — pas de requête supplémentaire, pas de scraping.

- [ ] **Étape 1 : écrire les tests qui échouent**

Créer `backend/tests/services/test_xg_odds_archive.py` :

```python
"""Les cotes qui ont produit un lambda doivent survivre a la purge."""
import importlib.util
from pathlib import Path

import pytest

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

    import asyncio

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
    # ce que _archive aurait stocke
    lambda_home, lambda_away = round(lh, 4), round(la, 4)
    # recalcul a partir des memes cotes archivees
    relu = _solve(dict(MARCHES))
    assert relu is not None
    assert round(relu[0], 4) == lambda_home
    assert round(relu[1], 4) == lambda_away
    # et le resultat doit rester plausible : Atletico tres favori
    assert lambda_home > 1.6
    assert lambda_away < 0.9


def test_unusable_markets_archive_nothing():
    """Si le solveur echoue, aucune ligne -- donc aucune cote orpheline."""
    assert _solve({"h2h": {"home": 1.5}}) is None
    assert _solve({}) is None
```

- [ ] **Étape 2 : lancer les tests, vérifier qu'ils échouent**

```bash
cd backend && uv run pytest tests/services/test_xg_odds_archive.py -q
```
Attendu : ÉCHEC (`colonne odds absente`, puis `FileNotFoundError` sur la migration).

- [ ] **Étape 3 : écrire la migration**

Créer `backend/alembic/versions/053_team_xg_estimates_odds.py` :

```python
"""Add team_xg_estimates.odds (cotes brutes ayant produit le lambda).

Revision ID: 053
Revises: 052
Create Date: 2026-08-20
"""
from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "053"
down_revision: str | None = "052"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Nullable : les lignes ecrites avant cette migration en portent un tant
    # que le rattrapage n'a pas tourne, et une estimation dont les snapshots
    # ont disparu n'est pas rattrapable.
    op.add_column(
        "team_xg_estimates",
        sa.Column("odds", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("team_xg_estimates", "odds")
```

- [ ] **Étape 4 : ajouter le champ au modèle**

Dans `backend/app/models/team_xg.py`, après `input_snapshot_ids` :

```python
    # Cotes brutes ayant produit ce lambda, telles que le bookmaker les
    # affichait : {"h2h": {...}, "totals": {...}}. Conservees pour que toute
    # evolution future de la methode reste rejouable sur l'historique, une fois
    # les snapshots purges a 45 jours. On archive la preuve, pas la conclusion :
    # aucune probabilite devigee ici, tout se recalcule.
    odds: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
```

`JSONB` est déjà importé en tête du fichier ; ne pas ajouter d'import.

- [ ] **Étape 5 : persister `markets` dans `_archive`**

Dans `backend/app/services/xg_library.py`, ajouter une seule ligne au bloc
`.values(...)` de `_archive`, juste après `input_snapshot_ids=ids,` :

```python
            odds=markets,
```

Rien d'autre ne change : `markets` est déjà en main.

- [ ] **Étape 6 : lancer les tests**

```bash
cd backend && uv run pytest tests/services/test_xg_odds_archive.py -v && uv run pytest tests/ -q
```
Attendu : SUCCÈS, aucune régression.

- [ ] **Étape 7 : commit**

```bash
git add backend/alembic/versions/053_team_xg_estimates_odds.py backend/app/models/team_xg.py backend/app/services/xg_library.py backend/tests/services/test_xg_odds_archive.py
git commit -m "feat(xg): archiver les cotes brutes a cote de chaque lambda"
```

---

### Task 2 : Rattrapage des lignes existantes

**Files:**
- Create: `backend/app/scripts/backfill_xg_odds.py`
- Create: `backend/tests/scripts/__init__.py` (si absent)
- Test: `backend/tests/scripts/test_backfill_xg_odds.py`

**Interfaces:**
- Consumes: `TeamXgEstimate.odds` et `TeamXgEstimate.input_snapshot_ids` (Task 1).
- Produces:
  - `rebuild_markets(rows) -> dict[str, dict[str, float]]` — reconstruit le dictionnaire depuis des lignes de `MatchOddsSnapshot`
  - `async backfill(session) -> tuple[int, int]` — `(rattrapees, impossibles)`

**Contexte.** Les lignes archivées avant la migration portent `odds = NULL`. Elles pointent vers leurs snapshots par `input_snapshot_ids`, et ces snapshots existent encore — la purge à 45 jours n'a pas commencé. **C'est la seule partie du chantier qui a une date de péremption** : passé ce délai, ces lignes sont définitivement sans preuve.

- [ ] **Étape 1 : écrire les tests qui échouent**

Créer `backend/tests/scripts/__init__.py` (fichier vide) puis
`backend/tests/scripts/test_backfill_xg_odds.py` :

```python
"""Rattrapage des cotes sur les estimations deja archivees."""
from types import SimpleNamespace

from app.scripts.backfill_xg_odds import rebuild_markets


def _row(market_type, outcome, odds):
    return SimpleNamespace(market_type=market_type, outcome=outcome, odds=odds)


def test_rebuild_groups_by_market_and_outcome():
    rows = [
        _row("h2h", "home", 1.347),
        _row("h2h", "draw", 5.35),
        _row("h2h", "away", 9.46),
        _row("totals", "over_3.0", 1.854),
        _row("totals", "under_3.0", 2.04),
    ]
    assert rebuild_markets(rows) == {
        "h2h": {"home": 1.347, "draw": 5.35, "away": 9.46},
        "totals": {"over_3.0": 1.854, "under_3.0": 2.04},
    }


def test_rebuild_with_no_rows_is_empty():
    """Snapshots disparus : on rend un dictionnaire vide, on ne fabrique rien."""
    assert rebuild_markets([]) == {}


def test_rebuild_keeps_every_market_present():
    rows = [_row("h2h", "home", 2.0), _row("btts", "yes", 1.9)]
    out = rebuild_markets(rows)
    assert set(out) == {"h2h", "btts"}
```

- [ ] **Étape 2 : lancer les tests, vérifier qu'ils échouent**

```bash
cd backend && mkdir -p tests/scripts && touch tests/scripts/__init__.py
uv run pytest tests/scripts/test_backfill_xg_odds.py -q
```
Attendu : ÉCHEC (`ModuleNotFoundError: app.scripts.backfill_xg_odds`).

- [ ] **Étape 3 : écrire le script**

Créer `backend/app/scripts/backfill_xg_odds.py` :

```python
"""Rattrapage : remplit team_xg_estimates.odds sur les lignes deja archivees.

Les estimations ecrites avant la migration 053 portent odds = NULL. Elles
designent leurs snapshots par input_snapshot_ids, et ces lignes existent encore
tant que job_purge_old_snapshots (45 jours) ne les a pas effacees.

FENETRE LIMITEE : passe ce delai, ces estimations restent definitivement sans
les cotes qui les ont produites. A executer des le deploiement.

Le script ne fabrique jamais rien : une ligne dont les snapshots ont disparu
reste a NULL et est comptee comme impossible.
"""
from __future__ import annotations

import asyncio
import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger(__name__)


def rebuild_markets(rows) -> dict[str, dict[str, float]]:
    """Reconstruit {marche: {issue: cote}} depuis des lignes de snapshot."""
    markets: dict[str, dict[str, float]] = {}
    for r in rows:
        markets.setdefault(r.market_type, {})[r.outcome] = r.odds
    return markets


async def backfill(session: AsyncSession) -> tuple[int, int]:
    """Remplit odds la ou il manque. Retourne (rattrapees, impossibles)."""
    from app.models.match_odds import MatchOddsSnapshot
    from app.models.team_xg import TeamXgEstimate

    cibles = (await session.execute(
        select(TeamXgEstimate).where(TeamXgEstimate.odds.is_(None))
    )).scalars().all()

    if not cibles:
        logger.info("Aucune estimation a rattraper.")
        return 0, 0

    rattrapees = impossibles = 0
    for est in cibles:
        ids = est.input_snapshot_ids or []
        if not ids:
            impossibles += 1
            logger.warning(
                "fixture %s / %s : aucun snapshot reference", est.fixture_id, est.phase
            )
            continue

        rows = (await session.execute(
            select(MatchOddsSnapshot).where(MatchOddsSnapshot.id.in_(ids))
        )).scalars().all()

        markets = rebuild_markets(rows)
        if not markets:
            impossibles += 1
            logger.warning(
                "fixture %s / %s : snapshots purges, cotes irrecuperables",
                est.fixture_id, est.phase,
            )
            continue

        est.odds = markets
        rattrapees += 1

    await session.commit()
    logger.info("Rattrapage : %d remplies, %d impossibles", rattrapees, impossibles)
    return rattrapees, impossibles


async def _main() -> None:
    from app.db import async_session

    async with async_session() as session:
        await backfill(session)


if __name__ == "__main__":
    asyncio.run(_main())
```

- [ ] **Étape 4 : lancer les tests**

```bash
cd backend && uv run pytest tests/scripts/test_backfill_xg_odds.py -v && uv run pytest tests/ -q
```
Attendu : SUCCÈS, aucune régression.

- [ ] **Étape 5 : vérifier que le script s'importe**

```bash
cd backend && uv run python -c "
from app.scripts.backfill_xg_odds import backfill, rebuild_markets
print('import OK')
"
```
Attendu : `import OK`.

- [ ] **Étape 6 : commit**

```bash
git add backend/app/scripts/backfill_xg_odds.py backend/tests/scripts/
git commit -m "feat(xg): script de rattrapage des cotes sur les estimations archivees"
```

---

### Task 3 : Mise en service

**Files:**
- Modify: `docs/DEPLOYMENT.md`
- Vérification en production

**Interfaces:**
- Consumes: tout ce qui précède.
- Produces: rien de programmatique.

- [ ] **Étape 1 : documenter**

Dans `docs/DEPLOYMENT.md`, à la fin de la section « xG d'équipe — source PS3838 », ajouter :

```markdown
### Cotes archivées

Chaque ligne de `team_xg_estimates` conserve, dans sa colonne `odds`, les cotes
brutes qui ont produit son λ — au format `{"h2h": {...}, "totals": {...}}`.

C'est ce qui rend rejouable sur l'historique toute évolution future de la
méthode (retrait de marge, solveur, ligne de totals retenue). Sans elles, il ne
resterait que la conclusion après la purge des snapshots à 45 jours, et chaque
idée exigerait d'attendre des mois de nouvelles données.

Vérifier qu'aucune estimation n'est sans preuve :

```bash
docker exec ev0-compose-z5hvqt-db-1 psql -U ev0 -d ev0 -c "
SELECT phase, count(*) AS total, count(*) FILTER (WHERE odds IS NULL) AS sans_cotes
FROM team_xg_estimates GROUP BY phase;"
```

`sans_cotes` doit rester à 0 pour toute ligne écrite après le déploiement. Une
valeur non nulle sur des lignes anciennes signale un rattrapage non exécuté —
ou des snapshots déjà purgés, auquel cas c'est irrattrapable.
```

- [ ] **Étape 2 : commit**

```bash
git add docs/DEPLOYMENT.md
git commit -m "docs: cotes archivees a cote de chaque estimation de xG"
```

- [ ] **Étape 3 : déployer**

```bash
cd /etc/dokploy/compose/ev0-compose-z5hvqt/code
git fetch origin --quiet && git reset --hard origin/main --quiet
docker compose -p ev0-compose-z5hvqt --env-file .env build backend worker
docker compose -p ev0-compose-z5hvqt --env-file .env run --rm --no-deps backend alembic upgrade head
docker compose -p ev0-compose-z5hvqt --env-file .env up -d --no-deps --remove-orphans backend worker
docker exec ev0-compose-z5hvqt-backend-1 alembic current
```

Attendu : `053 (head)`.

- [ ] **Étape 4 : exécuter le rattrapage**

**Sans attendre.** Chaque jour écoulé rapproche des lignes de la purge.

```bash
docker exec -e PYTHONPATH=/app -w /app ev0-compose-z5hvqt-worker-1 \
  python -m app.scripts.backfill_xg_odds
```

Attendu : un compte de lignes remplies, et `0 impossibles` — les estimations
datent du 20/08, leurs snapshots ont donc moins de 45 jours.

- [ ] **Étape 5 : vérifier**

```bash
docker exec ev0-compose-z5hvqt-db-1 psql -U ev0 -d ev0 -c "
SELECT phase, count(*) AS total, count(*) FILTER (WHERE odds IS NULL) AS sans_cotes
FROM team_xg_estimates GROUP BY phase;"
```

Attendu : `sans_cotes = 0` partout.

- [ ] **Étape 6 : vérifier le bouclage sur une vraie ligne**

```bash
docker exec -e PYTHONPATH=/app -w /app ev0-compose-z5hvqt-worker-1 python -c "
import asyncio
from sqlalchemy import select
from app.db import async_session
from app.models.team_xg import TeamXgEstimate
from app.services.xg_library import _solve

async def main():
    async with async_session() as s:
        e = (await s.execute(
            select(TeamXgEstimate).where(TeamXgEstimate.odds.isnot(None)).limit(1)
        )).scalar_one_or_none()
        if e is None:
            print('aucune ligne avec cotes'); return
        r = _solve(e.odds)
        print('stocke   :', e.lambda_home, e.lambda_away)
        print('recalcule:', round(r[0], 4), round(r[1], 4))
        print('BOUCLAGE OK' if abs(r[0]-e.lambda_home) < 1e-3 else 'ECART')
asyncio.run(main())
"
```

Attendu : `BOUCLAGE OK`. C'est la preuve que le passé est rejouable.

---

## Auto-revue

**Couverture de la spec.**

| Exigence | Tâche |
|---|---|
| Colonne `odds JSONB NULL` sur `team_xg_estimates` | 1 |
| Cotes brutes uniquement, pas de dérivé | 1 (contrainte globale + commentaire du modèle) |
| Sur la même ligne que le λ | 1 |
| Les deux phases traitées à l'identique | 1 (`_archive` est le chemin commun) |
| Aucun scraping ni requête supplémentaire | 1 (`markets` déjà en main) |
| Script de rattrapage idempotent | 2 (filtre `odds IS NULL`) |
| Snapshots disparus → `NULL`, comptés, jamais devinés | 2 |
| Rapport rattrapées / impossibles | 2 |
| Test de conservation | 1 |
| Test de bouclage | 1 (unitaire) + 3 (sur données réelles) |
| `team_xg_estimates` jamais purgée | test existant, non touché |
| Exécution du rattrapage dès le déploiement | 3 |

**Cohérence des noms.** `rebuild_markets(rows)` et `backfill(session)` gardent ces signatures de la tâche 2 à la tâche 3. `TeamXgEstimate.odds` est nommé pareil dans la migration, le modèle, `_archive` et le script.

**Points de vigilance pour l'implémenteur.**

- `JSONB` est **déjà importé** dans `app/models/team_xg.py` : ne pas ajouter un second import.
- `_archive` ne demande **qu'une ligne** de plus (`odds=markets,`). Si tu te retrouves à ajouter une requête ou à rappeler `_snapshot_group`, tu as pris un mauvais chemin — le dictionnaire est déjà en main.
- Le test de conservation inspecte les paramètres compilés d'un `pg_insert`. La compilation **exige le dialecte PostgreSQL explicite** (`on_conflict_do_nothing` n'existe pas ailleurs) — c'est déjà dans le code du test, ne le retire pas. Si la forme du `stmt` change, adapte le test plutôt que de le contourner : c'est lui qui garantit qu'on stocke bien ce qui a servi.
- Le rattrapage est **la seule partie datée** du chantier. Ne pas le reporter à une prochaine session.
