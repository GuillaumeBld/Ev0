# Lot 1 — Fondations transition saison 2026-27 : Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Débloquer la saison codée en dur (rollover automatique au 1er août), poser le registre de modèles Alpha/Beta avec la table de snapshots pré-match, et exécuter le spike sur l'historique de l'API Bzzoiro.

**Architecture:** Un service `season_service` devient la source unique de vérité de la saison courante (calcul par date + override `app_config`) ; l'agrégation et la sync d'événements s'y branchent. Une table `model_pricing_snapshots` enregistre les prix par (match, joueur, marché, modèle), upsertables jusqu'au coup d'envoi puis figés. Le spike est un script en lecture seule contre l'API Bzzoiro dont le livrable est un rapport de décision.

**Tech Stack:** Python 3.12, FastAPI/SQLAlchemy 2 async, Alembic, pytest (+pytest-asyncio), httpx. Spec de référence : `docs/superpowers/specs/2026-07-18-transition-saison-alpha-beta-design.md`.

## Global Constraints

- **Alpha (moteur `team_xg.py`) est GELÉ** : aucune modification de `backend/app/pricing/team_xg.py` dans ce lot.
- Tout est **pré-match** : aucune logique in-play nulle part.
- Noms de modèles : exactement `"alpha"` et `"beta"` (minuscules).
- Bascule de saison : **1er août** (`"2025-2026"` → `"2026-2027"` ; format `NNNN-NNNN` conservé, identique à `bzz_player_season_stats.season`).
- Migration Alembic suivante : **revision `046`, down_revision `"045"`**.
- Jamais d'échec silencieux : toute valeur de config invalide est loggée en warning avec fallback explicite.
- Le spike est **read-only** : aucun write en DB, aucun dépassement volontaire de quota (max ~20 requêtes API).
- Tests : `cd ~/ev0/backend && pytest tests/ -v` ; ne pas casser la suite existante.
- Branche de travail : `feat/lot1-fondations-saison` (créée depuis `main` à jour, PR vers `main` — `main` est protégée).

---

### Task 1: Service de saison courante (`season_service`)

**Files:**
- Create: `backend/app/services/season_service.py`
- Test: `backend/tests/services/test_season_service.py`

**Interfaces:**
- Produces: `compute_season(today: date) -> str` ; `season_start(season: str) -> date` ; `async current_season(session: AsyncSession, today: date | None = None) -> str` ; constante `SEASON_CONFIG_KEY = "current_season"`. Les tâches 2 et suivantes consomment ces trois fonctions.

- [ ] **Step 1: Écrire les tests qui échouent**

Créer `backend/tests/services/test_season_service.py` (le dossier `tests/services/` existe déjà) :

```python
"""Tests du service de saison courante — rollover au 1er août."""

from datetime import date
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services.season_service import (
    SEASON_CONFIG_KEY,
    compute_season,
    current_season,
    season_start,
)


def _mock_session(config_value: str | None) -> MagicMock:
    """Session async mockée : renvoie une ligne AppConfig (ou None)."""
    session = MagicMock()
    row = None
    if config_value is not None:
        row = MagicMock()
        row.value = config_value
    result = MagicMock()
    result.scalar_one_or_none.return_value = row
    session.execute = AsyncMock(return_value=result)
    return session


class TestComputeSeason:
    def test_juillet_reste_saison_precedente(self):
        assert compute_season(date(2026, 7, 18)) == "2025-2026"

    def test_premier_aout_bascule(self):
        assert compute_season(date(2026, 8, 1)) == "2026-2027"

    def test_janvier_milieu_de_saison(self):
        assert compute_season(date(2027, 1, 15)) == "2026-2027"


class TestSeasonStart:
    def test_debut_de_saison(self):
        assert season_start("2026-2027") == date(2026, 8, 1)

    def test_saison_precedente(self):
        assert season_start("2025-2026") == date(2025, 8, 1)


class TestCurrentSeason:
    @pytest.mark.asyncio
    async def test_sans_config_calcule_depuis_la_date(self):
        session = _mock_session(None)
        assert await current_season(session, today=date(2026, 9, 1)) == "2026-2027"

    @pytest.mark.asyncio
    async def test_override_config_prioritaire(self):
        session = _mock_session("2026-2027")
        assert await current_season(session, today=date(2026, 7, 1)) == "2026-2027"

    @pytest.mark.asyncio
    async def test_override_invalide_fallback_avec_warning(self, caplog):
        session = _mock_session("n_importe_quoi")
        with caplog.at_level("WARNING"):
            season = await current_season(session, today=date(2026, 7, 1))
        assert season == "2025-2026"
        assert "current_season" in caplog.text
```

- [ ] **Step 2: Vérifier qu'ils échouent**

Run: `cd ~/ev0/backend && pytest tests/services/test_season_service.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.services.season_service'`

- [ ] **Step 3: Implémenter le service**

Créer `backend/app/services/season_service.py` :

```python
"""Saison courante — source unique de vérité (spec 2026-07-18, §3.5).

La saison bascule le 1er août. Résolution :
1. override manuel via app_config (clé "current_season", ex. "2026-2027") ;
2. sinon calcul depuis la date du jour.
Un override invalide est ignoré avec warning — jamais d'échec silencieux.
"""

from __future__ import annotations

import logging
import re
from datetime import date

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

SEASON_CONFIG_KEY = "current_season"
SEASON_ROLLOVER_MONTH = 8  # 1er août
_SEASON_RE = re.compile(r"^(\d{4})-(\d{4})$")


def compute_season(today: date) -> str:
    """Saison au format "NNNN-NNNN" pour une date donnée (bascule au 1er août)."""
    if today.month >= SEASON_ROLLOVER_MONTH:
        return f"{today.year}-{today.year + 1}"
    return f"{today.year - 1}-{today.year}"


def season_start(season: str) -> date:
    """Date de début (1er août de la première année) d'une saison "NNNN-NNNN"."""
    match = _SEASON_RE.match(season)
    if not match:
        raise ValueError(f"Format de saison invalide: {season!r} (attendu NNNN-NNNN)")
    return date(int(match.group(1)), SEASON_ROLLOVER_MONTH, 1)


async def current_season(session: AsyncSession, today: date | None = None) -> str:
    """Saison courante : override app_config si valide, sinon calcul par date."""
    from app.models.app_config import AppConfig

    result = await session.execute(
        select(AppConfig).where(AppConfig.key == SEASON_CONFIG_KEY)
    )
    row = result.scalar_one_or_none()
    if row is not None:
        value = row.value.strip()
        if _SEASON_RE.match(value):
            return value
        logger.warning(
            "app_config[%s]=%r invalide (attendu NNNN-NNNN) — fallback calcul par date",
            SEASON_CONFIG_KEY, value,
        )
    return compute_season(today or date.today())
```

- [ ] **Step 4: Vérifier que les tests passent**

Run: `cd ~/ev0/backend && pytest tests/services/test_season_service.py -v`
Expected: 8 PASS

- [ ] **Step 5: Commit**

```bash
cd ~/ev0 && git add backend/app/services/season_service.py backend/tests/services/test_season_service.py
git commit -m "feat(saison): service de saison courante — rollover auto au 1er août + override app_config"
```

---

### Task 2: Brancher l'agrégation et la sync d'événements sur le service (fin du hardcode)

**Files:**
- Modify: `backend/app/ingestion/bzzoiro/aggregate.py` (lignes 13, 47, 277)
- Modify: `backend/app/ingestion/bzzoiro/sync_events.py` (lignes 14, 52-53)
- Modify: `backend/app/ingestion/bzzoiro/constants.py` (ligne 59 — suppression de `SEASON_START_DATE`)
- Test: `backend/tests/ingestion/test_aggregate_season_rollover.py`

**Interfaces:**
- Consumes: `current_season(session)`, `season_start(season)` de la tâche 1.
- Produces: `aggregate_all_leagues(session, season: str | None = None)` — signature inchangée pour l'appelant worker (`job_aggregate_season_stats` appelle sans `season`, ce qui résout désormais la saison courante automatiquement).

- [ ] **Step 1: Écrire le test qui échoue**

Créer `backend/tests/ingestion/test_aggregate_season_rollover.py` :

```python
"""La saison de l'agrégation n'est plus codée en dur — elle vient du season_service."""

import inspect

from app.ingestion.bzzoiro import aggregate


def test_aggregate_all_leagues_sans_saison_par_defaut_en_dur():
    """Le défaut doit être None (résolu via current_season), plus "2025-2026"."""
    sig = inspect.signature(aggregate.aggregate_all_leagues)
    assert sig.parameters["season"].default is None


def test_constante_season_start_date_supprimee():
    from app.ingestion.bzzoiro import constants
    assert not hasattr(constants, "SEASON_START_DATE")
```

- [ ] **Step 2: Vérifier qu'il échoue**

Run: `cd ~/ev0/backend && pytest tests/ingestion/test_aggregate_season_rollover.py -v`
Expected: 2 FAIL (défaut actuel `"2025-2026"` ; constante encore présente)

- [ ] **Step 3: Modifier `aggregate.py`**

Ligne 13, remplacer l'import :

```python
from app.services.season_service import current_season, season_start as season_start_of
```

(supprimer `SEASON_START_DATE` de l'import des constants ; garder les autres imports de constants intacts).

Dans `aggregate_season_stats` (ligne 47), remplacer :

```python
    cutoff_date = date.fromisoformat(season_start or SEASON_START_DATE)
```

par :

```python
    cutoff_date = date.fromisoformat(season_start) if season_start else season_start_of(season)
```

Dans `aggregate_all_leagues` (ligne 277), remplacer la signature :

```python
async def aggregate_all_leagues(session: AsyncSession, season: str | None = None) -> int:
```

et ajouter en tête de fonction (avant la boucle sur les ligues) :

```python
    if season is None:
        season = await current_season(session)
        logger.info("Saison courante résolue: %s", season)
```

- [ ] **Step 4: Modifier `sync_events.py`**

Ligne 14 : retirer `SEASON_START_DATE` de l'import des constants et ajouter :

```python
from app.services.season_service import current_season, season_start
```

Lignes 52-53, remplacer :

```python
    if full_season:
        date_from = SEASON_START_DATE
```

par :

```python
    if full_season:
        date_from = season_start(await current_season(session)).isoformat()
```

- [ ] **Step 5: Supprimer la constante**

Dans `backend/app/ingestion/bzzoiro/constants.py`, supprimer la ligne 59 (`SEASON_START_DATE = "2025-08-01"`) et son commentaire attenant. Puis vérifier qu'aucun usage ne subsiste :

Run: `grep -rn "SEASON_START_DATE" ~/ev0/backend/`
Expected: aucun résultat

- [ ] **Step 6: Lancer toute la suite**

Run: `cd ~/ev0/backend && pytest tests/ -v`
Expected: tout PASS (y compris les 2 nouveaux tests). Si un test existant référençait `SEASON_START_DATE` ou le défaut `"2025-2026"`, l'adapter au nouveau contrat (défaut `None` + résolution par service) — ne pas le supprimer.

- [ ] **Step 7: Commit**

```bash
cd ~/ev0 && git add backend/app/ingestion/bzzoiro/ backend/tests/ingestion/test_aggregate_season_rollover.py
git commit -m "feat(saison): agrégation et sync events branchées sur season_service — fin du hardcode 2025-2026"
```

---

### Task 3: Registre de modèles Alpha/Beta + table de snapshots pré-match

**Files:**
- Create: `backend/app/pricing/model_registry.py`
- Create: `backend/app/models/model_pricing.py`
- Create: `backend/app/services/model_snapshot_service.py`
- Create: `backend/alembic/versions/046_model_pricing_snapshots.py`
- Modify: `backend/app/models/__init__.py` (export du nouveau modèle, même pattern que les autres)
- Test: `backend/tests/services/test_model_snapshot_service.py`

**Interfaces:**
- Produces:
  - `MODEL_ALPHA = "alpha"`, `MODEL_BETA = "beta"`, `KNOWN_MODELS`, `DEFAULT_MODEL = MODEL_ALPHA` (module `app.pricing.model_registry`) ;
  - modèle ORM `ModelPricingSnapshot` (table `model_pricing_snapshots`) ;
  - `async upsert_snapshot(session, *, model_name: str, fixture_id: int, player_api_id: int, player_name: str, market: str, probability: float, fair_odds: float, as_of_utc: datetime) -> bool` (False si la ligne est figée — rien n'est modifié) ;
  - `async freeze_fixture(session, fixture_id: int) -> int` (fige toutes les lignes du match, renvoie le nombre de lignes figées).
  - Marchés admis dans `market` : `"goal_with_sub"`, `"assist_with_sub"`, `"goal"`, `"assist"` (constante `KNOWN_MARKETS` dans `model_registry`).

- [ ] **Step 1: Écrire les tests qui échouent**

Créer `backend/tests/services/test_model_snapshot_service.py` :

```python
"""Registre Alpha/Beta + snapshots pré-match : upsert jusqu'au gel, figé ensuite."""

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.models.model_pricing import ModelPricingSnapshot
from app.pricing.model_registry import (
    DEFAULT_MODEL,
    KNOWN_MARKETS,
    KNOWN_MODELS,
    MODEL_ALPHA,
    MODEL_BETA,
)
from app.services.model_snapshot_service import upsert_snapshot


def test_registre_expose_alpha_et_beta():
    assert MODEL_ALPHA == "alpha"
    assert MODEL_BETA == "beta"
    assert KNOWN_MODELS == ("alpha", "beta")
    assert DEFAULT_MODEL == "alpha"
    assert "goal_with_sub" in KNOWN_MARKETS


def test_modele_orm_colonnes_et_contrainte():
    assert ModelPricingSnapshot.__tablename__ == "model_pricing_snapshots"
    for col in ("model_name", "fixture_id", "player_api_id", "market",
                "probability", "fair_odds", "as_of_utc", "frozen"):
        assert hasattr(ModelPricingSnapshot, col)
    constraint_names = {c.name for c in ModelPricingSnapshot.__table__.constraints}
    assert "uq_model_pricing_snapshot" in constraint_names


def _session_returning(row):
    session = MagicMock()
    result = MagicMock()
    result.scalar_one_or_none.return_value = row
    session.execute = AsyncMock(return_value=result)
    session.add = MagicMock()
    session.flush = AsyncMock()
    return session


_KWARGS = dict(
    model_name="alpha", fixture_id=1, player_api_id=42, player_name="Mbappé",
    market="goal_with_sub", probability=0.31, fair_odds=3.23,
    as_of_utc=datetime(2026, 8, 15, 16, 0, tzinfo=timezone.utc),
)


class TestUpsertSnapshot:
    @pytest.mark.asyncio
    async def test_cree_la_ligne_si_absente(self):
        session = _session_returning(None)
        assert await upsert_snapshot(session, **_KWARGS) is True
        session.add.assert_called_once()

    @pytest.mark.asyncio
    async def test_met_a_jour_si_non_figee(self):
        row = MagicMock()
        row.frozen = False
        session = _session_returning(row)
        assert await upsert_snapshot(session, **_KWARGS) is True
        assert row.probability == 0.31
        session.add.assert_not_called()

    @pytest.mark.asyncio
    async def test_refuse_si_figee(self):
        row = MagicMock()
        row.frozen = True
        row.probability = 0.28
        session = _session_returning(row)
        assert await upsert_snapshot(session, **_KWARGS) is False
        assert row.probability == 0.28  # intacte
        session.add.assert_not_called()

    @pytest.mark.asyncio
    async def test_rejette_modele_inconnu(self):
        session = _session_returning(None)
        with pytest.raises(ValueError, match="gamma"):
            await upsert_snapshot(session, **{**_KWARGS, "model_name": "gamma"})

    @pytest.mark.asyncio
    async def test_rejette_marche_inconnu(self):
        session = _session_returning(None)
        with pytest.raises(ValueError, match="first_goal"):
            await upsert_snapshot(session, **{**_KWARGS, "market": "first_goal"})
```

- [ ] **Step 2: Vérifier qu'ils échouent**

Run: `cd ~/ev0/backend && pytest tests/services/test_model_snapshot_service.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.pricing.model_registry'`

- [ ] **Step 3: Créer le registre**

Créer `backend/app/pricing/model_registry.py` :

```python
"""Registre des modèles de pricing — champion/challenger (spec 2026-07-18, §3.1).

Alpha = moteur actuel (team_xg.py), gelé, seul en prod jusqu'à bascule.
Beta = challenger calibré (lot 3). Ajouter un modèle = une entrée ici.
"""

MODEL_ALPHA = "alpha"
MODEL_BETA = "beta"
KNOWN_MODELS: tuple[str, ...] = (MODEL_ALPHA, MODEL_BETA)
DEFAULT_MODEL = MODEL_ALPHA

# Marchés snapshotés — convention "avec sub" en tête (spec §2)
KNOWN_MARKETS: tuple[str, ...] = ("goal_with_sub", "assist_with_sub", "goal", "assist")
```

- [ ] **Step 4: Créer le modèle ORM**

Créer `backend/app/models/model_pricing.py` :

```python
"""Snapshots de pricing par modèle — registre Alpha/Beta (spec 2026-07-18, §3.1)."""

from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class ModelPricingSnapshot(Base, TimestampMixin):
    """Prix pré-match d'un (match, joueur, marché) pour un modèle donné.

    Une ligne par (fixture, joueur, marché, modèle), upsertée jusqu'au coup
    d'envoi puis figée (frozen=True). Seules les lignes figées sont admissibles
    pour comparer les modèles — rien n'est recalculé a posteriori.
    """

    __tablename__ = "model_pricing_snapshots"
    __table_args__ = (
        UniqueConstraint(
            "fixture_id", "player_api_id", "market", "model_name",
            name="uq_model_pricing_snapshot",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    model_name: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    fixture_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("fixtures.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    player_api_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    player_name: Mapped[str] = mapped_column(String(200), nullable=False)
    market: Mapped[str] = mapped_column(String(30), nullable=False)
    probability: Mapped[float] = mapped_column(Float, nullable=False)
    fair_odds: Mapped[float] = mapped_column(Float, nullable=False)
    as_of_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    frozen: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
```

Puis ajouter l'export dans `backend/app/models/__init__.py`, en suivant le pattern des autres modèles du fichier (import + `__all__` si présent) :

```python
from app.models.model_pricing import ModelPricingSnapshot
```

- [ ] **Step 5: Créer le service de snapshots**

Créer `backend/app/services/model_snapshot_service.py` :

```python
"""Écriture et gel des snapshots de pricing par modèle (spec 2026-07-18, §3.1)."""

from __future__ import annotations

import logging
from datetime import datetime

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.model_pricing import ModelPricingSnapshot
from app.pricing.model_registry import KNOWN_MARKETS, KNOWN_MODELS

logger = logging.getLogger(__name__)


async def upsert_snapshot(
    session: AsyncSession,
    *,
    model_name: str,
    fixture_id: int,
    player_api_id: int,
    player_name: str,
    market: str,
    probability: float,
    fair_odds: float,
    as_of_utc: datetime,
) -> bool:
    """Crée ou met à jour le snapshot. Retourne False (sans rien toucher) si figé."""
    if model_name not in KNOWN_MODELS:
        raise ValueError(f"Modèle inconnu: {model_name!r} (admis: {KNOWN_MODELS})")
    if market not in KNOWN_MARKETS:
        raise ValueError(f"Marché inconnu: {market!r} (admis: {KNOWN_MARKETS})")

    result = await session.execute(
        select(ModelPricingSnapshot).where(
            ModelPricingSnapshot.fixture_id == fixture_id,
            ModelPricingSnapshot.player_api_id == player_api_id,
            ModelPricingSnapshot.market == market,
            ModelPricingSnapshot.model_name == model_name,
        )
    )
    row = result.scalar_one_or_none()
    if row is None:
        session.add(
            ModelPricingSnapshot(
                model_name=model_name,
                fixture_id=fixture_id,
                player_api_id=player_api_id,
                player_name=player_name,
                market=market,
                probability=probability,
                fair_odds=fair_odds,
                as_of_utc=as_of_utc,
            )
        )
        return True
    if row.frozen:
        logger.debug(
            "Snapshot figé, upsert ignoré: fixture=%d player=%d market=%s model=%s",
            fixture_id, player_api_id, market, model_name,
        )
        return False
    row.player_name = player_name
    row.probability = probability
    row.fair_odds = fair_odds
    row.as_of_utc = as_of_utc
    return True


async def freeze_fixture(session: AsyncSession, fixture_id: int) -> int:
    """Fige tous les snapshots du match (appelé au coup d'envoi). Retourne le nombre figé."""
    result = await session.execute(
        update(ModelPricingSnapshot)
        .where(
            ModelPricingSnapshot.fixture_id == fixture_id,
            ModelPricingSnapshot.frozen.is_(False),
        )
        .values(frozen=True)
    )
    frozen_count = result.rowcount or 0
    logger.info("Fixture %d: %d snapshots figés au coup d'envoi", fixture_id, frozen_count)
    return frozen_count
```

- [ ] **Step 6: Vérifier que les tests passent**

Run: `cd ~/ev0/backend && pytest tests/services/test_model_snapshot_service.py -v`
Expected: 7 PASS

- [ ] **Step 7: Écrire la migration**

Créer `backend/alembic/versions/046_model_pricing_snapshots.py` :

```python
"""model_pricing_snapshots : registre Alpha/Beta, prix pré-match figés au coup d'envoi.

Revision ID: 046
Revises: 045
Create Date: 2026-07-18
"""
import sqlalchemy as sa
from alembic import op

revision = "046"
down_revision = "045"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "model_pricing_snapshots",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("model_name", sa.String(20), nullable=False, index=True),
        sa.Column(
            "fixture_id",
            sa.Integer(),
            sa.ForeignKey("fixtures.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("player_api_id", sa.Integer(), nullable=False, index=True),
        sa.Column("player_name", sa.String(200), nullable=False),
        sa.Column("market", sa.String(30), nullable=False),
        sa.Column("probability", sa.Float(), nullable=False),
        sa.Column("fair_odds", sa.Float(), nullable=False),
        sa.Column("as_of_utc", sa.DateTime(timezone=True), nullable=False),
        sa.Column("frozen", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint(
            "fixture_id", "player_api_id", "market", "model_name",
            name="uq_model_pricing_snapshot",
        ),
    )


def downgrade() -> None:
    op.drop_table("model_pricing_snapshots")
```

- [ ] **Step 8: Vérifier la migration à sec + suite complète**

Run: `cd ~/ev0/backend && alembic heads && alembic history -r 044: | head -8`
Expected: une seule head = `046` ; chaîne `044 → 045 → 046`.
(Ne PAS lancer `alembic upgrade` ici — la migration s'appliquera en prod au déploiement, comme d'habitude.)

Run: `cd ~/ev0/backend && pytest tests/ -v`
Expected: tout PASS.

- [ ] **Step 9: Commit**

```bash
cd ~/ev0 && git add backend/app/pricing/model_registry.py backend/app/models/model_pricing.py backend/app/models/__init__.py backend/app/services/model_snapshot_service.py backend/alembic/versions/046_model_pricing_snapshots.py backend/tests/services/test_model_snapshot_service.py
git commit -m "feat(modeles): registre Alpha/Beta + table model_pricing_snapshots (prix pré-match figés au coup d'envoi)"
```

---

### Task 4: Spike — historique de l'API Bzzoiro (read-only)

**Files:**
- Create: `backend/app/scripts/spike_bzzoiro_history.py`
- Create (livrable): `docs/superpowers/specs/2026-07-19-spike-bzzoiro-historique.md`

**Interfaces:**
- Consumes: `BzzoiroClient` (`app.ingestion.bzzoiro.client`), `settings.bzzoiro_api_key` (défini dans `backend/app/config.py:33` et `backend/.env`), constantes `TARGET_LEAGUE_INTERNAL_IDS`.
- Produces: un rapport de décision tranchant entre les 3 issues de la spec §3.5 : (1) agrégats saison historiques disponibles, (2) seulement les matchs historiques → backfill échelonné, (3) rien → fallback Transfermarkt. Ce rapport conditionne le contenu des lots 2-3.

Pas de TDD ici — c'est une investigation en lecture seule, le livrable est le rapport.

- [ ] **Step 1: Écrire le script de sondage**

Créer `backend/app/scripts/spike_bzzoiro_history.py` :

```python
"""Spike read-only : l'API Bzzoiro expose-t-elle l'historique ? (spec 2026-07-18, §3.5)

Sonde 3 questions, ~15 requêtes max, aucun write en DB :
  A. /api/leagues/ — quelles ligues au-delà des 6 cibles (Eredivisie, Liga
     Portugal, Championship…) et quels season_id/saisons y sont listés ?
  B. /api/events/ avec une fenêtre 2024-25 (date_from=2024-08-01,
     date_to=2025-06-30) sur la Premier League — les matchs historiques
     sont-ils servis ?
  C. /api/player-stats/ pour un joueur connu — les lignes couvrent-elles les
     matchs d'avant août 2025 ?

Usage : cd backend && python -m app.scripts.spike_bzzoiro_history
"""

import asyncio
import json

from app.config import settings
from app.ingestion.bzzoiro.client import BzzoiroClient
from app.ingestion.bzzoiro.constants import TARGET_LEAGUE_INTERNAL_IDS


async def main() -> None:
    assert settings.bzzoiro_api_key, "BZZOIRO_API_KEY manquante"
    async with BzzoiroClient(settings.bzzoiro_api_key) as client:
        # A. Ligues disponibles (périmètre + saisons listées)
        leagues = await client.get_page("/api/leagues/")
        print("=== A. LEAGUES ===")
        print(json.dumps(leagues, indent=2, ensure_ascii=False)[:4000])

        # B. Matchs historiques 2024-25 (Premier League, internal_id=1)
        pl = TARGET_LEAGUE_INTERNAL_IDS["premier_league"]
        events = await client.get_page(
            "/api/events/",
            {"league": pl, "date_from": "2024-08-01", "date_to": "2024-09-01"},
        )
        print("=== B. EVENTS 2024-25 (PL, août 2024) ===")
        print(json.dumps(events, indent=2, ensure_ascii=False)[:4000])

        # C. Stats par joueur — profondeur temporelle
        # Prendre le premier joueur retourné par la ligue pour rester générique.
        stats = await client.get_page("/api/player-stats/", {"league": pl})
        print("=== C. PLAYER-STATS (échantillon, regarder les dates) ===")
        print(json.dumps(stats, indent=2, ensure_ascii=False)[:4000])


if __name__ == "__main__":
    asyncio.run(main())
```

Note : si un des endpoints accepte un paramètre `season`/`season_id` (visible dans la réponse A), relancer B et C avec ce paramètre pour la saison 2024-25 et le documenter — c'est le cœur de la décision.

- [ ] **Step 2: Exécuter le spike**

Run: `cd ~/ev0/backend && python -m app.scripts.spike_bzzoiro_history 2>&1 | tee /private/tmp/claude-501/-Users-yohan-resin/abb356f1-f871-474e-bddb-e46739b372ee/scratchpad/spike_bzzoiro_output.txt`
Expected: les trois sections s'affichent. En cas de 401/403, vérifier la clé dans `backend/.env` ; en cas de 404 sur un endpoint, le noter tel quel dans le rapport (c'est une donnée du spike, pas une erreur à corriger).

- [ ] **Step 3: Rédiger le rapport de décision**

Créer `docs/superpowers/specs/2026-07-19-spike-bzzoiro-historique.md` avec cette structure (remplir chaque section depuis la sortie réelle du spike — aucune section vide) :

```markdown
# Spike — historique API Bzzoiro (résultats)

**Date** : <date d'exécution>
**Réf** : spec 2026-07-18 §3.5, plan lot 1 tâche 4

## A. Ligues et saisons exposées
<liste des ligues au-delà des 6 cibles ; season_id/saisons visibles>

## B. Matchs historiques (fenêtre 2024-25)
<servis ou non ; volume estimé si oui>

## C. Profondeur des player-stats
<profondeur temporelle observée ; param season accepté ou non>

## Décision (issue spec §3.5)
Option retenue : <1 | 2 | 3>
- Option 1 — agrégats historiques directs : <faisable/non + endpoint>
- Option 2 — backfill matchs échelonné : <volume estimé, plan d'échelonnement
  ligue par ligue sous quota, ordre = ligues à plus fort volume de paris>
- Option 3 — fallback Transfermarkt : <requis ou non, pour quels cas>

## Conséquences sur les lots 2-3
<ce que ça change concrètement dans le harnais et Beta>
```

- [ ] **Step 4: Commit**

```bash
cd ~/ev0 && git add backend/app/scripts/spike_bzzoiro_history.py docs/superpowers/specs/2026-07-19-spike-bzzoiro-historique.md
git commit -m "spike(bzzoiro): sondage historique API — rapport de décision pour les lots 2-3"
```

---

## Fin de lot

- [ ] Suite complète verte : `cd ~/ev0/backend && pytest tests/ -v`
- [ ] PR `feat/lot1-fondations-saison` → `main` (main est protégée) : `gh pr create` avec un résumé des 4 tâches et un lien vers la spec ; signaler dans la PR la décision du spike (elle conditionne les plans des lots 2-3).
- [ ] Rappel déploiement (mémoire projet) : rebuild backend+worker avec `--no-deps --remove-orphans`, jamais `--force-recreate` avec un service nommé ; la migration 046 s'applique au déploiement.
```
