# Recommandations Expirées — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Marquer automatiquement les recommandations `pending` comme `expired` dès que leur match commence, et les afficher dans une section collapsible sur la page recommendations.

**Architecture:** (1) Job worker toutes les 5 min qui expire les recos dont le kickoff est passé. (2) Endpoint API `GET /api/v1/recommendations/expired` qui lit directement en DB. (3) Frontend : section "Expirées" collapsible sous la liste principale.

**Tech Stack:** FastAPI, SQLAlchemy async, APScheduler IntervalTrigger, Next.js 14, React Query

---

### Task 1 : Job worker — expirer les recos au coup d'envoi

**Files:**
- Modify: `backend/app/worker.py`

**Step 1 : Ajouter la fonction `job_expire_recommendations`**

Insérer après `job_autopilot_settle` (vers ligne 943) :

```python
async def job_expire_recommendations():
    """Every 5 min: expire pending recommendations whose fixture has kicked off."""
    from app.db import async_session
    from app.models.recommendations import Recommendation
    from app.models.fixtures import Fixture

    now = datetime.now(UTC)
    async with async_session() as session:
        result = await session.execute(
            select(Recommendation)
            .join(Fixture, Recommendation.fixture_id == Fixture.id)
            .where(
                Recommendation.status == "pending",
                Fixture.kickoff_utc <= now,
            )
        )
        recs = result.scalars().all()
        for rec in recs:
            rec.status = "expired"
        if recs:
            await session.commit()
            logger.info("Expired %d recommendations", len(recs))
```

**Step 2 : Ajouter le job au scheduler dans `create_scheduler()`**

Ajouter après le bloc `autopilot_settle` (avant `return scheduler`) :

```python
# Expire recommendations: Every 5 minutes
scheduler.add_job(
    job_expire_recommendations,
    IntervalTrigger(minutes=5),
    id="expire_recommendations",
    name="Expire pending recommendations past kickoff",
    replace_existing=True,
)
```

---

### Task 2 : API — endpoint GET /recommendations/expired

**Files:**
- Modify: `backend/app/api/recommendations.py`

**Step 1 : Ajouter l'endpoint avant la route PATCH**

Insérer avant le handler PATCH (chercher `@router.patch`) :

```python
@router.get("/expired", response_model=RecommendationsResponse)
async def get_expired_recommendations(
    db: AsyncSession = Depends(get_db),
    target_date: date | None = Query(None, description="Date (default: today)"),
) -> RecommendationsResponse:
    """Get expired recommendations for a given date (past kickoff, still pending at expiry)."""
    effective_date = target_date or date.today()
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
        )
        for rec, fix in rows
    ]
    return RecommendationsResponse(recommendations=recommendations, error=None)
```

**Step 2 : Autoriser `expired` dans le PATCH**

Dans le handler PATCH, trouver :
```python
valid_statuses = {"pending", "approved", "rejected", "executed"}
```
Remplacer par :
```python
valid_statuses = {"pending", "approved", "rejected", "executed", "expired"}
```

---

### Task 3 : Frontend — fonction API + section Expirées

**Files:**
- Modify: `frontend/src/lib/api.ts`
- Modify: `frontend/src/app/dashboard/recommendations/page.tsx`

**Step 1 : Ajouter `getExpiredRecommendations` dans api.ts**

Après `getRecommendation` :

```ts
export async function getExpiredRecommendations(params?: { date?: string }) {
  const queryParams: Record<string, string> = {}
  if (params?.date) queryParams.target_date = params.date
  const { data } = await api.get('/api/v1/recommendations/expired', { params: queryParams })
  return data
}
```

**Step 2 : Ajouter le second query dans recommendations/page.tsx**

Ajouter dans les imports :
```ts
import { getRecommendations, getExpiredRecommendations } from '@/lib/api'
import { ChevronDown } from 'lucide-react'
```

Ajouter un state et un query pour les expirées, après le query existant :
```ts
const [expiredOpen, setExpiredOpen] = useState(false)

const { data: expiredData } = useQuery({
  queryKey: ['recommendations-expired', selectedDate],
  enabled: !!selectedDate,
  queryFn: async () => {
    const response = await getExpiredRecommendations({ date: selectedDate })
    const recs: ApiRecommendation[] = response.recommendations || []
    return recs.map((rec) => ({
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
    }))
  },
})

const expiredRecs = expiredData || []
```

**Step 3 : Ajouter la section "Expirées" dans le JSX**

Après la fermeture du bloc recommendations grid (`</div>` final avant `</div>`), ajouter :

```tsx
{/* Section Expirées */}
{expiredRecs.length > 0 && (
  <div className="mt-8">
    <button
      onClick={() => setExpiredOpen(!expiredOpen)}
      className="flex items-center gap-2 text-gray-400 hover:text-white transition-colors mb-4"
    >
      <ChevronDown className={`w-4 h-4 transition-transform ${expiredOpen ? 'rotate-180' : ''}`} />
      <span className="text-sm font-medium">Expirées ({expiredRecs.length})</span>
    </button>

    {expiredOpen && (
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4 opacity-50">
        {expiredRecs.map((rec) => (
          <div key={rec.id} className="relative">
            <div className="absolute top-3 right-3 z-10 px-2 py-0.5 bg-gray-600 text-gray-300 text-xs rounded">
              Expiré
            </div>
            <RecommendationCard key={rec.id} recommendation={rec} />
          </div>
        ))}
      </div>
    )}
  </div>
)}
```

---

### Task 4 : Déploiement

**Step 1 : Copier les fichiers modifiés sur le VPS**
```bash
VPS="root@213.130.144.204"
scp backend/app/worker.py "$VPS:/etc/dokploy/compose/ev0-compose-z5hvqt/code/backend/app/worker.py"
scp backend/app/api/recommendations.py "$VPS:/etc/dokploy/compose/ev0-compose-z5hvqt/code/backend/app/api/recommendations.py"
scp frontend/src/lib/api.ts "$VPS:/etc/dokploy/compose/ev0-compose-z5hvqt/code/frontend/src/lib/api.ts"
scp frontend/src/app/dashboard/recommendations/page.tsx "$VPS:/etc/dokploy/compose/ev0-compose-z5hvqt/code/frontend/src/app/dashboard/recommendations/page.tsx"
```

**Step 2 : Redémarrer backend (pas besoin de rebuild)**
```bash
ssh root@213.130.144.204 "
  cd /etc/dokploy/compose/ev0-compose-z5hvqt/code
  docker compose -p ev0-compose-z5hvqt --env-file .env up -d --force-recreate --no-build backend worker
"
```

**Step 3 : Rebuilder et redémarrer frontend**
```bash
ssh root@213.130.144.204 "
  cd /etc/dokploy/compose/ev0-compose-z5hvqt/code
  docker compose -p ev0-compose-z5hvqt --env-file .env up -d --build frontend
"
```
