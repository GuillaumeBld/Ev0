# Recommendations View All Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Passer la page Recommendations en mode "View All" par défaut (toutes les recos actives paginées 50/page), le filtre date devenant un opt-in qui seul bascule vers la vue date-filtrée.

**Architecture:** Deux changements coordonnés — (1) backend : `target_date` reste optionnel mais crée maintenant deux code paths distincts : View All (lecture DB paginée) vs date-filtrée (génération à la volée, comportement actuel) ; (2) frontend : `selectedDate` passe à `null` par défaut, le filtre date devient un bouton toggle, pagination ajoutée.

**Tech Stack:** FastAPI + SQLAlchemy async (backend), Next.js 14 + React Query (frontend). Fichiers clés : `backend/app/api/recommendations.py`, `frontend/src/app/dashboard/recommendations/page.tsx`, `frontend/src/lib/api.ts`.

---

## Contexte technique

### Fichiers modifiés

| Fichier | Action |
|---------|--------|
| `backend/app/api/recommendations.py` | Modifier : `RecommendationsResponse` + view-all path dans les 2 endpoints |
| `backend/tests/test_recommendations_view_all.py` | Créer : tests pour la pagination et le view-all |
| `frontend/src/lib/api.ts` | Modifier : params `page`/`page_size`, type réponse paginée |
| `frontend/src/app/dashboard/recommendations/page.tsx` | Modifier : `selectedDate` null, `page` state, pagination UI, date filter opt-in |

### Deux code paths dans `/recommendations`

- **View All** (`target_date` absent) : lit depuis `RecommendationModel` × `FixtureModel` en DB, filtre `status IN ('pending','approved')` + `kickoff_utc >= now`, pagine. N'appelle PAS `get_recommendations_for_date`.
- **Date mode** (`target_date` présent) : comportement actuel intact — génère via `get_recommendations_for_date`, persiste, retourne.

### `RecommendationsResponse` — champs à ajouter/corriger

```python
class RecommendationsResponse(BaseModel):
    date: str | None = None          # optionnel (None en view all)
    count: int = 0                   # rétrocompatible
    recommendations: list[Recommendation]
    error: str | None = None
    # Pagination (présents dans les deux modes)
    total: int = 0
    page: int = 1
    page_size: int = 50
    pages: int = 1
```

---

## Chunk 1 : Backend

### Task 1 : `RecommendationsResponse` + view-all dans `GET /recommendations`

**Files:**
- Modify: `backend/app/api/recommendations.py:70-229`
- Create: `backend/tests/test_recommendations_view_all.py`

- [ ] **Step 1 : Écrire les tests qui échouent**

Créer `backend/tests/test_recommendations_view_all.py` :

```python
"""Tests for view-all pagination in recommendations API."""
from app.api.recommendations import RecommendationsResponse, Recommendation


def _make_rec(**kwargs) -> Recommendation:
    defaults = dict(
        id=1, fixture_id="ext-1", fixture_name="PSG vs Lyon",
        kickoff_utc="2026-04-10T18:45:00+00:00",
        player_name="Mbappe", team="PSG", market_type="goalscorer",
        fair_odds=3.5, best_bookmaker="Betclic", best_odds=4.0,
        edge=0.14, classification="VALUE", confidence=0.72, explanation={},
    )
    defaults.update(kwargs)
    return Recommendation(**defaults)


class TestRecommendationsResponsePagination:
    def test_pagination_fields_present(self):
        resp = RecommendationsResponse(
            recommendations=[_make_rec()],
            total=100,
            page=2,
            page_size=50,
            pages=2,
        )
        assert resp.total == 100
        assert resp.page == 2
        assert resp.page_size == 50
        assert resp.pages == 2

    def test_pagination_defaults(self):
        """Default pagination = page 1, page_size 50, pages 1."""
        resp = RecommendationsResponse(recommendations=[])
        assert resp.page == 1
        assert resp.page_size == 50
        assert resp.pages == 1
        assert resp.total == 0

    def test_date_optional(self):
        """date field is now optional (None in view-all mode)."""
        resp = RecommendationsResponse(recommendations=[])
        assert resp.date is None

    def test_recs_returned_without_date(self):
        """RecommendationsResponse builds fine without date (view-all)."""
        recs = [_make_rec(id=i) for i in range(1, 4)]
        resp = RecommendationsResponse(recommendations=recs, total=3, pages=1)
        assert len(resp.recommendations) == 3
```

- [ ] **Step 2 : Vérifier que les tests échouent**

```bash
cd /Users/yohan.resin/Ev0/backend
uv run pytest tests/test_recommendations_view_all.py -v
```

Attendu : `FAILED` — champs `total`/`page`/`page_size`/`pages` n'existent pas, `date` est required.

- [ ] **Step 3 : Mettre à jour `RecommendationsResponse`**

Dans `backend/app/api/recommendations.py`, trouver (lignes 70-76) :

```python
class RecommendationsResponse(BaseModel):
    """Response with list of recommendations."""

    date: str
    count: int
    recommendations: list[Recommendation]
    error: str | None = None
```

Remplacer par :

```python
class RecommendationsResponse(BaseModel):
    """Response with list of recommendations."""

    date: str | None = None
    count: int = 0
    recommendations: list[Recommendation]
    error: str | None = None
    # Pagination
    total: int = 0
    page: int = 1
    page_size: int = 50
    pages: int = 1
```

- [ ] **Step 4 : Vérifier que les tests passent**

```bash
uv run pytest tests/test_recommendations_view_all.py -v
```

Attendu : `4 passed`

- [ ] **Step 5 : Écrire les tests View All endpoint**

Ajouter dans `test_recommendations_view_all.py` (après les tests existants) :

```python
import pytest
from math import ceil
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime, timezone


class TestGetRecommendationsViewAll:
    """Tests for the view-all code path (no target_date)."""

    def _make_db_rec(self, id_=1, market_type="goalscorer", edge=0.14, status="pending"):
        rec = MagicMock()
        rec.id = id_
        rec.player_name = "Mbappe"
        rec.market_type = market_type
        rec.fair_odds = 3.5
        rec.best_bookmaker = "Betclic"
        rec.best_odds = 4.0
        rec.edge = edge
        rec.classification = "VALUE"
        rec.confidence = 0.72
        rec.explanation = {}
        rec.status = status
        return rec

    def _make_db_fix(self, external_id="ext-1"):
        fix = MagicMock()
        fix.external_id = external_id
        fix.home_team = "PSG"
        fix.away_team = "Lyon"
        fix.kickoff_utc = datetime(2026, 4, 10, 18, 45, tzinfo=timezone.utc)
        return fix

    @pytest.mark.asyncio
    async def test_view_all_reads_from_db_not_generator(self):
        """When target_date is None, must NOT call get_recommendations_for_date."""
        from app.api.recommendations import get_recommendations

        mock_db = AsyncMock()
        # Mock count query → 1
        count_result = MagicMock()
        count_result.scalar.return_value = 1
        # Mock items query
        items_result = MagicMock()
        items_result.all.return_value = [(self._make_db_rec(), self._make_db_fix())]
        mock_db.execute = AsyncMock(side_effect=[count_result, items_result])

        with patch("app.api.recommendations.get_recommendations_for_date") as mock_gen:
            response = await get_recommendations(db=mock_db, target_date=None)
            mock_gen.assert_not_called()

        assert len(response.recommendations) == 1

    @pytest.mark.asyncio
    async def test_view_all_pagination_metadata(self):
        """Returns correct total/page/pages metadata."""
        from app.api.recommendations import get_recommendations

        mock_db = AsyncMock()
        count_result = MagicMock()
        count_result.scalar.return_value = 120
        items_result = MagicMock()
        items_result.all.return_value = [
            (self._make_db_rec(id_=i), self._make_db_fix(f"ext-{i}"))
            for i in range(1, 51)  # 50 items (page 1 of 3)
        ]
        mock_db.execute = AsyncMock(side_effect=[count_result, items_result])

        response = await get_recommendations(db=mock_db, target_date=None)

        assert response.total == 120
        assert response.page == 1
        assert response.page_size == 50
        assert response.pages == 3

    @pytest.mark.asyncio
    async def test_view_all_market_filter_applied(self):
        """market_type filter — db.execute is called twice (count + items) in view-all mode."""
        from app.api.recommendations import get_recommendations
        from app.api.recommendations import MarketType

        mock_db = AsyncMock()
        count_result = MagicMock()
        count_result.scalar.return_value = 0
        items_result = MagicMock()
        items_result.all.return_value = []
        mock_db.execute = AsyncMock(side_effect=[count_result, items_result])

        response = await get_recommendations(
            db=mock_db, target_date=None, market_type=MarketType.GOALSCORER
        )
        # Two DB calls = count + items queries (not one like the date-mode)
        assert mock_db.execute.call_count == 2
        assert response.total == 0

    @pytest.mark.asyncio
    async def test_date_mode_returns_pages_one(self):
        """With target_date, endpoint returns pages=1 and page=1 (no pagination)."""
        from app.api.recommendations import get_recommendations
        from datetime import date

        mock_db = AsyncMock()
        with patch("app.api.recommendations.get_recommendations_for_date", new=AsyncMock(return_value=([], None))):
            response = await get_recommendations(db=mock_db, target_date=date(2026, 4, 10))

        assert response.pages == 1
        assert response.page == 1
        assert response.date == "2026-04-10"

    @pytest.mark.asyncio
    async def test_view_all_no_date_returns_all_active(self):
        """Without target_date, returns active (pending/approved) recs ordered ASC by kickoff."""
        from app.api.recommendations import get_recommendations

        mock_db = AsyncMock()
        count_result = MagicMock()
        count_result.scalar.return_value = 2
        items_result = MagicMock()
        # Two fixtures, earlier kickoff first (ordered ASC)
        fix1 = MagicMock()
        fix1.external_id = "ext-1"
        fix1.home_team = "PSG"
        fix1.away_team = "Lyon"
        fix1.kickoff_utc = datetime(2026, 4, 10, 18, 45, tzinfo=timezone.utc)
        fix2 = MagicMock()
        fix2.external_id = "ext-2"
        fix2.home_team = "OM"
        fix2.away_team = "Nice"
        fix2.kickoff_utc = datetime(2026, 4, 12, 20, 0, tzinfo=timezone.utc)
        items_result.all.return_value = [
            (self._make_db_rec(id_=1), fix1),
            (self._make_db_rec(id_=2), fix2),
        ]
        mock_db.execute = AsyncMock(side_effect=[count_result, items_result])

        with patch("app.api.recommendations.get_recommendations_for_date") as mock_gen:
            response = await get_recommendations(db=mock_db, target_date=None)
            mock_gen.assert_not_called()

        assert response.total == 2
        assert len(response.recommendations) == 2
        # First recommendation is the earlier fixture
        assert response.recommendations[0].fixture_id == "ext-1"
        assert response.recommendations[1].fixture_id == "ext-2"
```

- [ ] **Step 6 : Vérifier que les nouveaux tests échouent**

```bash
uv run pytest tests/test_recommendations_view_all.py::TestGetRecommendationsViewAll -v
```

Attendu : `FAILED` — l'endpoint actuel appelle `get_recommendations_for_date` même sans date.

- [ ] **Step 7 : Implémenter le view-all path dans `GET /recommendations`**

Dans `backend/app/api/recommendations.py` :

**7a.** Ajouter les imports manquants en tête de fichier. Vérifier que `func` et `ceil` sont absents puis ajouter :

```python
from math import ceil
from sqlalchemy import func, select
```

(remplacer `from sqlalchemy import select` par `from sqlalchemy import func, select`)

**7b.** Ajouter les params `page` et `page_size` au endpoint :

```python
@router.get("/recommendations", response_model=RecommendationsResponse)
async def get_recommendations(
    db: AsyncSession = Depends(get_db),
    target_date: date | None = Query(None, description="Date for recommendations (default: today)"),
    market_type: MarketType | None = Query(None, description="Filter by market type"),
    league: str | None = Query(None, description="Filter by league (ligue_1, premier_league)"),
    min_edge: float = Query(0.05, description="Minimum edge threshold"),
    page: int = Query(1, ge=1, description="Page number (view-all only)"),
    page_size: int = Query(50, ge=1, le=200, description="Items per page (view-all only)"),
) -> RecommendationsResponse:
```

**7c.** Juste après la docstring, ajouter le view-all branch AVANT le code existant :

```python
    """Get betting recommendations for a given date."""
    # ── View All mode (no target_date) ─────────────────────────────────
    if target_date is None:
        filters = [
            RecommendationModel.status.in_(["pending", "approved"]),
            FixtureModel.kickoff_utc >= datetime.now(UTC),
        ]
        if market_type:
            filters.append(RecommendationModel.market_type == market_type.value)
        if min_edge > 0:
            filters.append(RecommendationModel.edge >= min_edge)

        base_query = (
            select(RecommendationModel, FixtureModel)
            .join(FixtureModel, RecommendationModel.fixture_id == FixtureModel.id)
            .where(*filters)
        )

        count_result = await db.execute(
            select(func.count()).select_from(base_query.subquery())
        )
        total = count_result.scalar() or 0

        result = await db.execute(
            base_query
            .order_by(FixtureModel.kickoff_utc.asc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        rows = result.all()

        recommendations = [
            Recommendation(
                id=rec.id,
                fixture_id=fix.external_id,
                fixture_name=f"{fix.home_team} vs {fix.away_team}",
                kickoff_utc=fix.kickoff_utc.isoformat(),
                player_name=rec.player_name,
                team="",
                market_type=rec.market_type,
                fair_odds=rec.fair_odds,
                best_bookmaker=rec.best_bookmaker,
                best_odds=rec.best_odds,
                edge=rec.edge,
                classification=rec.classification,
                confidence=rec.confidence,
                explanation=rec.explanation or {},
                status=rec.status,
            )
            for rec, fix in rows
        ]
        pages = max(1, ceil(total / page_size))
        return RecommendationsResponse(
            recommendations=recommendations,
            total=total,
            page=page,
            page_size=page_size,
            pages=pages,
        )
    # ── Date mode (existing behaviour) ─────────────────────────────────
    effective_date = target_date
    # ... rest of existing code unchanged (replace `effective_date = target_date or date.today()`)
```

**Important :** Modifier la ligne suivante dans le code date-mode :
```python
# Avant
effective_date = target_date or date.today()
# Après (target_date ne peut plus être None ici)
effective_date = target_date
```

Et mettre à jour le `return` final du date-mode pour inclure les champs de pagination :
```python
return RecommendationsResponse(
    date=str(effective_date),
    count=len(recommendations),
    recommendations=recommendations,
    error=error_msg,
    total=len(recommendations),
    page=1,
    page_size=len(recommendations) or 50,
    pages=1,
)
```

- [ ] **Step 8 : Vérifier que tous les tests passent**

```bash
uv run pytest tests/test_recommendations_view_all.py -v
```

Attendu : `9 passed`

- [ ] **Step 9 : Run full suite pour vérifier aucune régression**

```bash
uv run pytest tests/ -x -q --ignore=tests/test_match_events.py --ignore=tests/test_pricing_assist.py
```

- [ ] **Step 10 : Commit**

```bash
git add backend/app/api/recommendations.py \
        backend/tests/test_recommendations_view_all.py
git commit -m "feat: recommendations view-all mode avec pagination"
```

---

### Task 2 : View-all dans `GET /recommendations/expired`

**Files:**
- Modify: `backend/app/api/recommendations.py:232-274`
- Modify: `backend/tests/test_recommendations_view_all.py`

- [ ] **Step 1 : Écrire le test**

Ajouter dans `test_recommendations_view_all.py` :

```python
class TestGetExpiredRecommendationsViewAll:
    def _make_db_rec(self, id_=1):
        rec = MagicMock()
        rec.id = id_
        rec.player_name = "Mbappe"
        rec.market_type = "goalscorer"
        rec.fair_odds = 3.5
        rec.best_bookmaker = "Betclic"
        rec.best_odds = 4.0
        rec.edge = 0.14
        rec.classification = "VALUE"
        rec.confidence = 0.72
        rec.explanation = {}
        rec.status = "expired"
        return rec

    def _make_db_fix(self, external_id="ext-1"):
        fix = MagicMock()
        fix.external_id = external_id
        fix.home_team = "PSG"
        fix.away_team = "Lyon"
        fix.kickoff_utc = datetime(2026, 3, 15, 18, 45, tzinfo=timezone.utc)
        return fix

    @pytest.mark.asyncio
    async def test_view_all_expired_no_date_filter(self):
        """Without target_date, returns all expired recs (paginated)."""
        from app.api.recommendations import get_expired_recommendations

        mock_db = AsyncMock()
        count_result = MagicMock()
        count_result.scalar.return_value = 5
        items_result = MagicMock()
        items_result.all.return_value = [
            (self._make_db_rec(id_=i), self._make_db_fix(f"ext-{i}"))
            for i in range(1, 6)
        ]
        mock_db.execute = AsyncMock(side_effect=[count_result, items_result])

        response = await get_expired_recommendations(db=mock_db, target_date=None)

        assert response.total == 5
        assert len(response.recommendations) == 5
        assert response.pages == 1

    @pytest.mark.asyncio
    async def test_view_all_expired_two_db_queries(self):
        """View-all expired issues two DB queries (count + items), not one like date mode."""
        from app.api.recommendations import get_expired_recommendations

        mock_db = AsyncMock()
        count_result = MagicMock()
        count_result.scalar.return_value = 2
        items_result = MagicMock()
        items_result.all.return_value = [
            (self._make_db_rec(id_=2), self._make_db_fix("ext-2")),
            (self._make_db_rec(id_=1), self._make_db_fix("ext-1")),
        ]
        mock_db.execute = AsyncMock(side_effect=[count_result, items_result])

        response = await get_expired_recommendations(db=mock_db, target_date=None)
        assert mock_db.execute.call_count == 2  # count query + items query
        assert response.total == 2
        assert len(response.recommendations) == 2

    @pytest.mark.asyncio
    async def test_date_mode_expired_one_db_query(self):
        """Date mode expired issues a single DB query (no count needed, no pagination)."""
        from app.api.recommendations import get_expired_recommendations
        from datetime import date

        mock_db = AsyncMock()
        items_result = MagicMock()
        items_result.all.return_value = []  # empty list — avoids MagicMock iteration error
        mock_db.execute = AsyncMock(return_value=items_result)

        response = await get_expired_recommendations(
            db=mock_db, target_date=date(2026, 4, 10)
        )
        assert mock_db.execute.call_count == 1  # no count query in date mode
        assert response.pages == 1
        assert response.page == 1
```

- [ ] **Step 2 : Vérifier que les tests échouent**

```bash
uv run pytest tests/test_recommendations_view_all.py::TestGetExpiredRecommendationsViewAll -v
```

- [ ] **Step 3 : Implémenter le view-all path dans `GET /recommendations/expired`**

Dans `backend/app/api/recommendations.py`, remplacer le corps de `get_expired_recommendations` par :

```python
@router.get("/recommendations/expired", response_model=RecommendationsResponse)
async def get_expired_recommendations(
    db: AsyncSession = Depends(get_db),
    target_date: date | None = Query(None, description="Date (default: today)"),
    page: int = Query(1, ge=1, description="Page number (view-all only)"),
    page_size: int = Query(50, ge=1, le=200, description="Items per page (view-all only)"),
) -> RecommendationsResponse:
    """Get expired recommendations. Without target_date: all expired, paginated."""
    # ── View All mode ────────────────────────────────────────────────
    if target_date is None:
        base_query = (
            select(RecommendationModel, FixtureModel)
            .join(FixtureModel, RecommendationModel.fixture_id == FixtureModel.id)
            .where(RecommendationModel.status == "expired")
        )

        count_result = await db.execute(
            select(func.count()).select_from(base_query.subquery())
        )
        total = count_result.scalar() or 0

        result = await db.execute(
            base_query
            .order_by(FixtureModel.kickoff_utc.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        rows = result.all()

        recommendations = [
            Recommendation(
                id=rec.id,
                fixture_id=fix.external_id,
                fixture_name=f"{fix.home_team} vs {fix.away_team}",
                kickoff_utc=fix.kickoff_utc.isoformat(),
                player_name=rec.player_name,
                team="",
                market_type=rec.market_type,
                fair_odds=rec.fair_odds,
                best_bookmaker=rec.best_bookmaker,
                best_odds=rec.best_odds,
                edge=rec.edge,
                classification=rec.classification,
                confidence=rec.confidence,
                explanation=rec.explanation or {},
                status=rec.status,
            )
            for rec, fix in rows
        ]
        pages = max(1, ceil(total / page_size))
        return RecommendationsResponse(
            recommendations=recommendations,
            total=total,
            page=page,
            page_size=page_size,
            pages=pages,
        )

    # ── Date mode (existing behaviour) ──────────────────────────────
    effective_date = target_date
    day_start = datetime.combine(effective_date, datetime.min.time(), tzinfo=UTC)
    day_end = datetime.combine(effective_date, datetime.max.time(), tzinfo=UTC)

    result = await db.execute(
        select(RecommendationModel, FixtureModel)
        .join(FixtureModel, RecommendationModel.fixture_id == FixtureModel.id)
        .where(
            RecommendationModel.status == "expired",
            FixtureModel.kickoff_utc >= day_start,
            FixtureModel.kickoff_utc <= day_end,
        )
        .order_by(FixtureModel.kickoff_utc.asc())
    )
    rows = result.all()

    recommendations = [
        Recommendation(
            id=rec.id,
            fixture_id=fix.external_id,
            fixture_name=f"{fix.home_team} vs {fix.away_team}",
            kickoff_utc=fix.kickoff_utc.isoformat(),
            player_name=rec.player_name,
            team="",
            market_type=rec.market_type,
            fair_odds=rec.fair_odds,
            best_bookmaker=rec.best_bookmaker,
            best_odds=rec.best_odds,
            edge=rec.edge,
            classification=rec.classification,
            confidence=rec.confidence,
            explanation=rec.explanation or {},
            status=rec.status,
        )
        for rec, fix in rows
    ]
    return RecommendationsResponse(
        date=str(effective_date),
        count=len(recommendations),
        recommendations=recommendations,
        total=len(recommendations),
        page=1,
        page_size=len(recommendations) or 50,
        pages=1,
    )
```

- [ ] **Step 4 : Vérifier que les tests passent**

```bash
uv run pytest tests/test_recommendations_view_all.py -v
```

Attendu : `12 passed`

- [ ] **Step 5 : Run full suite**

```bash
uv run pytest tests/ -q --ignore=tests/test_match_events.py --ignore=tests/test_pricing_assist.py
```

- [ ] **Step 6 : Commit**

```bash
git add backend/app/api/recommendations.py \
        backend/tests/test_recommendations_view_all.py
git commit -m "feat: recommendations/expired view-all mode avec pagination"
```

---

## Chunk 2 : Frontend

### Task 3 : Mettre à jour `api.ts`

**Files:**
- Modify: `frontend/src/lib/api.ts:125-143`

- [ ] **Step 1 : Lire le fichier pour localiser les sections recommendations**

```bash
grep -n "recommendation" /Users/yohan.resin/Ev0/frontend/src/lib/api.ts | head -20
```

- [ ] **Step 2 : Mettre à jour `getRecommendations`**

Trouver (lignes ~125-136) :

```typescript
export async function getRecommendations(params?: {
  date?: string
  market_type?: string
  min_edge?: number
}) {
  const queryParams: Record<string, string | number> = {}
  if (params?.date) queryParams.target_date = params.date
  if (params?.market_type) queryParams.market_type = params.market_type
  if (params?.min_edge != null) queryParams.min_edge = params.min_edge
  const { data } = await api.get('/api/v1/recommendations', { params: queryParams })
  return data
}
```

Remplacer par :

```typescript
export interface RecommendationsApiResponse {
  date: string | null
  count: number
  recommendations: Recommendation[]
  error: string | null
  total: number
  page: number
  page_size: number
  pages: number
}

export async function getRecommendations(params?: {
  date?: string
  market_type?: string
  min_edge?: number
  page?: number
  page_size?: number
}): Promise<RecommendationsApiResponse> {
  const queryParams: Record<string, string | number> = {}
  if (params?.date) queryParams.target_date = params.date
  if (params?.market_type) queryParams.market_type = params.market_type
  if (params?.min_edge != null) queryParams.min_edge = params.min_edge
  if (params?.page != null) queryParams.page = params.page
  if (params?.page_size != null) queryParams.page_size = params.page_size
  const { data } = await api.get('/api/v1/recommendations', { params: queryParams })
  return data
}
```

- [ ] **Step 3 : Mettre à jour `getExpiredRecommendations`**

Trouver (lignes ~138-143) :

```typescript
export async function getExpiredRecommendations(params?: { date?: string }) {
  const queryParams: Record<string, string> = {}
  if (params?.date) queryParams.target_date = params.date
  const { data } = await api.get('/api/v1/recommendations/expired', { params: queryParams })
  return data
}
```

Remplacer par :

```typescript
export async function getExpiredRecommendations(params?: {
  date?: string
  page?: number
  page_size?: number
}): Promise<RecommendationsApiResponse> {
  const queryParams: Record<string, string | number> = {}
  if (params?.date) queryParams.target_date = params.date
  if (params?.page != null) queryParams.page = params.page
  if (params?.page_size != null) queryParams.page_size = params.page_size
  const { data } = await api.get('/api/v1/recommendations/expired', { params: queryParams })
  return data
}
```

- [ ] **Step 4 : Vérifier la compilation TypeScript**

```bash
cd /Users/yohan.resin/Ev0/frontend
npx tsc --noEmit 2>&1 | head -20
```

Attendu : pas d'erreur liée aux types recommendations.

- [ ] **Step 5 : Commit**

```bash
git add frontend/src/lib/api.ts
git commit -m "feat: api.ts — pagination params + type RecommendationsApiResponse"
```

---

### Task 4 : Mettre à jour `page.tsx`

**Files:**
- Modify: `frontend/src/app/dashboard/recommendations/page.tsx`

- [ ] **Step 1 : Lire le fichier en entier**

Lire `/Users/yohan.resin/Ev0/frontend/src/app/dashboard/recommendations/page.tsx` pour avoir l'état actuel sous les yeux.

- [ ] **Step 2 : Réécrire le fichier**

Remplacer le contenu complet par :

```tsx
'use client'

import { useState, useEffect, useRef } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Filter, RefreshCw, TrendingUp, Calendar, ChevronDown, X } from 'lucide-react'
import { RecommendationCard } from '@/components/RecommendationCard'
import { getRecommendations, getExpiredRecommendations } from '@/lib/api'
import { LineupData } from '@/components/lineups/LineupDisplay'

type MarketFilter = 'all' | 'goalscorer' | 'assist'
type EdgeFilter = 'all' | '5+' | '10+' | '15+'

type FixtureLineupCache = {
  home_team: string
  away_team: string
  home: LineupData | null
  away: LineupData | null
}

function edgeFilterToMinEdge(f: EdgeFilter): number {
  if (f === '5+') return 0.05
  if (f === '10+') return 0.10
  if (f === '15+') return 0.15
  return 0
}

function parseOpponent(fixtureName: string, team: string): string {
  const parts = fixtureName.split(' vs ')
  if (parts.length === 2) {
    return parts[0].trim() === team ? parts[1].trim() : parts[0].trim()
  }
  return fixtureName
}

function formatDateLabel(isoDate: string): string {
  const d = new Date(isoDate + 'T00:00:00')
  return d.toLocaleDateString('fr-FR', { day: 'numeric', month: 'short' })
}

interface ApiRecommendation {
  id: number
  fixture_id: string
  fixture_name: string
  kickoff_utc: string
  player_name: string
  team: string
  market_type: 'goalscorer' | 'assist'
  fair_odds: number
  best_bookmaker: string
  best_odds: number
  edge: number
  classification: string
  confidence: number
  explanation: Record<string, any>
  status?: string
}

export default function RecommendationsPage() {
  const [marketFilter, setMarketFilter] = useState<MarketFilter>('all')
  const [edgeFilter, setEdgeFilter] = useState<EdgeFilter>('5+')
  const [selectedDate, setSelectedDate] = useState<string | null>(null)  // null = View All
  const [page, setPage] = useState(1)
  const [expiredPage, setExpiredPage] = useState(1)
  const [expiredOpen, setExpiredOpen] = useState(false)
  const [lineupCache, setLineupCache] = useState<Record<string, FixtureLineupCache>>({})
  const fetchingFixtures = useRef<Set<string>>(new Set())

  // Reset pages when filters change
  useEffect(() => {
    setPage(1)
    setExpiredPage(1)
  }, [selectedDate, marketFilter, edgeFilter])

  const isViewAll = selectedDate === null

  const { data, isLoading, isError, error, refetch } = useQuery({
    queryKey: ['recommendations', selectedDate, marketFilter, edgeFilter, page],
    refetchInterval: 10_000,
    queryFn: async () => {
      const minEdge = edgeFilterToMinEdge(edgeFilter)
      const response = await getRecommendations({
        date: selectedDate ?? undefined,
        market_type: marketFilter !== 'all' ? marketFilter : undefined,
        min_edge: minEdge,
        ...(isViewAll ? { page, page_size: 50 } : {}),
      })

      const recs: ApiRecommendation[] = response.recommendations || []
      return {
        recs: recs.map((rec) => ({
          id: rec.id,
          fixtureId: String(rec.fixture_id),
          player: rec.player_name,
          team: rec.team,
          opponent: parseOpponent(rec.fixture_name, rec.team),
          market: rec.market_type,
          fairOdds: rec.fair_odds,
          bestOdds: rec.best_odds,
          bookmaker: rec.best_bookmaker,
          edge: rec.edge,
          confidence: rec.confidence,
          kickoff: rec.kickoff_utc,
          explanation: rec.explanation,
          status: (rec.status as 'pending' | 'approved' | 'rejected') ?? 'pending',
        })),
        total: response.total,
        pages: response.pages,
      }
    },
  })

  const { data: expiredData } = useQuery({
    queryKey: ['recommendations-expired', selectedDate, expiredPage],
    refetchInterval: 10_000,
    queryFn: async () => {
      const response = await getExpiredRecommendations({
        date: selectedDate ?? undefined,
        ...(isViewAll ? { page: expiredPage, page_size: 50 } : {}),
      })
      const recs: ApiRecommendation[] = response.recommendations || []
      return {
        recs: recs.map((rec) => ({
          id: rec.id,
          player: rec.player_name,
          team: rec.team,
          opponent: parseOpponent(rec.fixture_name, rec.team),
          market: rec.market_type,
          fairOdds: rec.fair_odds,
          bestOdds: rec.best_odds,
          bookmaker: rec.best_bookmaker,
          edge: rec.edge,
          confidence: rec.confidence,
          kickoff: rec.kickoff_utc,
          explanation: rec.explanation,
        })),
        total: response.total,
        pages: response.pages,
      }
    },
  })

  const filteredRecs = data?.recs || []
  const totalPages = data?.pages || 1
  const expiredRecs = expiredData?.recs || []
  const expiredTotalPages = expiredData?.pages || 1

  // Fetch lineups for each unique fixture in recommendations (non-fatal, cached)
  useEffect(() => {
    if (!filteredRecs.length) return
    const uniqueIds = Array.from(new Set(filteredRecs.map((r) => r.fixtureId)))
    for (const fxId of uniqueIds) {
      if (!fxId || lineupCache[fxId] || fetchingFixtures.current.has(fxId)) continue
      fetchingFixtures.current.add(fxId)
      fetch(`/api/v1/lineups/fixture/${fxId}`)
        .then((r) => (r.ok ? r.json() : null))
        .then((d) => { if (d) setLineupCache((prev) => ({ ...prev, [fxId]: d })) })
        .catch(() => { /* non-fatal */ })
        .finally(() => fetchingFixtures.current.delete(fxId))
    }
  }, [filteredRecs]) // eslint-disable-line react-hooks/exhaustive-deps

  return (
    <div className="p-4 md:p-8">
      {/* Header */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4 mb-8">
        <div>
          <h1 className="text-2xl font-bold text-white">Recommandations</h1>
          <p className="text-gray-400 mt-1">
            {isViewAll
              ? `${data?.total ?? 0} picks disponibles`
              : `${filteredRecs.length} picks disponibles`}
          </p>
        </div>
        <button
          onClick={() => refetch()}
          className="flex items-center gap-2 px-4 py-2 bg-gray-700 hover:bg-gray-600 text-white rounded-lg transition-colors"
        >
          <RefreshCw className="w-4 h-4" />
          Actualiser
        </button>
      </div>

      {/* Filters */}
      <div className="flex flex-wrap gap-4 mb-6">
        {/* Date filter — opt-in toggle */}
        {selectedDate ? (
          <div className="flex items-center gap-2 bg-brand-700 rounded-lg px-4 py-2">
            <Calendar className="w-4 h-4 text-brand-200" />
            <span className="text-white text-sm">{formatDateLabel(selectedDate)}</span>
            <button
              onClick={() => setSelectedDate(null)}
              className="text-brand-200 hover:text-white ml-1"
              aria-label="Supprimer le filtre date"
            >
              <X className="w-3.5 h-3.5" />
            </button>
          </div>
        ) : (
          <div className="relative flex items-center gap-2 bg-gray-800 hover:bg-gray-700 rounded-lg px-4 py-2 cursor-pointer transition-colors">
            <Calendar className="w-4 h-4 text-gray-400" />
            <span className="text-gray-400 text-sm">Filtrer par date</span>
            <input
              type="date"
              onChange={(e) => { if (e.target.value) setSelectedDate(e.target.value) }}
              className="absolute inset-0 opacity-0 cursor-pointer w-full"
              aria-label="Filtrer par date"
            />
          </div>
        )}

        {/* Market filter */}
        <div className="flex items-center gap-1 bg-gray-800 rounded-lg p-1">
          {(['all', 'goalscorer', 'assist'] as const).map((m) => (
            <button
              key={m}
              onClick={() => setMarketFilter(m)}
              className={`px-3 py-1.5 rounded-md text-sm transition-colors ${
                marketFilter === m
                  ? 'bg-brand-600 text-white'
                  : 'text-gray-400 hover:text-white'
              }`}
            >
              {m === 'all' ? 'Tous' : m === 'goalscorer' ? '🎯 Buteur' : '🅰️ Passeur'}
            </button>
          ))}
        </div>

        {/* Edge filter */}
        <div className="flex items-center gap-1 bg-gray-800 rounded-lg p-1">
          <TrendingUp className="w-4 h-4 text-gray-400 ml-2" />
          {(['all', '5+', '10+', '15+'] as const).map((e) => (
            <button
              key={e}
              onClick={() => setEdgeFilter(e)}
              className={`px-3 py-1.5 rounded-md text-sm transition-colors ${
                edgeFilter === e
                  ? 'bg-green-600 text-white'
                  : 'text-gray-400 hover:text-white'
              }`}
            >
              {e === 'all' ? 'Tous' : `${e}%`}
            </button>
          ))}
        </div>
      </div>

      {/* Error Banner */}
      {isError && (
        <div className="mb-6 bg-red-500/10 border border-red-500/30 rounded-lg p-4 flex items-center justify-between">
          <p className="text-sm text-red-400">
            Erreur lors du chargement des recommandations.{' '}
            {error instanceof Error ? error.message : ''}
          </p>
          <button
            onClick={() => refetch()}
            className="text-sm text-red-300 hover:text-white underline"
          >
            Réessayer
          </button>
        </div>
      )}

      {/* Recommendations Grid */}
      {isLoading ? (
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
          {[1, 2, 3, 4].map((i) => (
            <div key={i} className="bg-gray-800 rounded-xl h-48 animate-pulse" />
          ))}
        </div>
      ) : filteredRecs.length > 0 ? (
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
          {filteredRecs.map((rec) => {
            const { fixtureId, ...recProps } = rec
            const fx = lineupCache[fixtureId]
            const lineup = fx
              ? (rec.team === fx.home_team ? fx.home : fx.away)
              : undefined
            return (
              <RecommendationCard key={rec.id} recommendation={{ ...recProps, lineup }} />
            )
          })}
        </div>
      ) : (
        <div className="bg-gray-800 rounded-xl p-12 text-center">
          <Filter className="w-12 h-12 text-gray-600 mx-auto mb-4" />
          <p className="text-gray-400">Aucune recommandation ne correspond aux filtres</p>
        </div>
      )}

      {/* Pagination — active recs (view all only) */}
      {isViewAll && totalPages > 1 && (
        <div className="flex items-center justify-center gap-4 mt-6">
          <button
            onClick={() => setPage((p) => Math.max(1, p - 1))}
            disabled={page === 1}
            className="px-3 py-1.5 bg-gray-800 text-white rounded-lg disabled:opacity-40 hover:bg-gray-700 transition-colors"
          >
            ←
          </button>
          <span className="text-gray-400 text-sm">
            Page {page} / {totalPages}
          </span>
          <button
            onClick={() => setPage((p) => Math.min(totalPages, p + 1))}
            disabled={page === totalPages}
            className="px-3 py-1.5 bg-gray-800 text-white rounded-lg disabled:opacity-40 hover:bg-gray-700 transition-colors"
          >
            →
          </button>
        </div>
      )}

      {/* Section Expirées */}
      <div className="mt-8">
        <button
          onClick={() => setExpiredOpen(!expiredOpen)}
          className="flex items-center gap-2 text-gray-400 hover:text-white transition-colors mb-4"
        >
          <ChevronDown className={`w-4 h-4 transition-transform ${expiredOpen ? 'rotate-180' : ''}`} />
          <span className="text-sm font-medium">
            Expirées ({isViewAll ? (expiredData?.total ?? 0) : expiredRecs.length})
          </span>
        </button>

        {expiredOpen && (
          expiredRecs.length === 0 ? (
            <p className="text-sm text-gray-500 italic">
              {isViewAll
                ? 'Aucune recommandation expirée.'
                : 'Aucune recommandation expirée pour cette date.'}
            </p>
          ) : (
            <>
              <div className="grid grid-cols-1 lg:grid-cols-2 gap-4 opacity-50">
                {expiredRecs.map((rec) => (
                  <div key={rec.id} className="relative">
                    <div className="absolute top-3 right-3 z-10 px-2 py-0.5 bg-gray-600 text-gray-300 text-xs rounded">
                      Expiré
                    </div>
                    <RecommendationCard recommendation={rec} />
                  </div>
                ))}
              </div>

              {/* Pagination — expired (view all only) */}
              {isViewAll && expiredTotalPages > 1 && (
                <div className="flex items-center justify-center gap-4 mt-4">
                  <button
                    onClick={() => setExpiredPage((p) => Math.max(1, p - 1))}
                    disabled={expiredPage === 1}
                    className="px-3 py-1.5 bg-gray-800 text-white rounded-lg disabled:opacity-40 hover:bg-gray-700 transition-colors"
                  >
                    ←
                  </button>
                  <span className="text-gray-400 text-sm">
                    Page {expiredPage} / {expiredTotalPages}
                  </span>
                  <button
                    onClick={() => setExpiredPage((p) => Math.min(expiredTotalPages, p + 1))}
                    disabled={expiredPage === expiredTotalPages}
                    className="px-3 py-1.5 bg-gray-800 text-white rounded-lg disabled:opacity-40 hover:bg-gray-700 transition-colors"
                  >
                    →
                  </button>
                </div>
              )}
            </>
          )
        )}
      </div>
    </div>
  )
}
```

- [ ] **Step 3 : Vérifier la compilation TypeScript**

```bash
cd /Users/yohan.resin/Ev0/frontend
npx tsc --noEmit 2>&1 | grep -i "error" | head -20
```

Corriger les erreurs de type si présentes. Les erreurs probables :
- `X` non importé depuis lucide-react → vérifié, il est dans l'import
- `fixtureId` manquant dans la map des expiredRecs → normal, expiredRecs n'a pas `fixtureId`

- [ ] **Step 4 : Build frontend pour vérifier**

```bash
cd /Users/yohan.resin/Ev0/frontend
npm run build 2>&1 | tail -20
```

Attendu : build réussi sans erreur.

- [ ] **Step 5 : Commit**

```bash
git add frontend/src/app/dashboard/recommendations/page.tsx
git commit -m "feat: recommendations page — view all par défaut, filtre date opt-in, pagination"
```

---

## Intégration finale

- [ ] **Push et deploy**

```bash
git push origin main
ssh root@213.130.144.204 "cd /etc/dokploy/compose/ev0-compose-z5hvqt/code && git pull origin main && docker compose -p ev0-compose-z5hvqt --env-file .env up -d --build --no-deps backend frontend"
```

- [ ] **Vérifier en prod**
  - Page Recommendations s'ouvre sur View All (toutes les recos actives)
  - Filtres marché/edge fonctionnent sans changer le mode
  - Clic "Filtrer par date" → date picker → badge date affiché, vue date-filtrée
  - Clic ✕ sur badge → retour View All
  - Pagination visible si > 50 recos
