# Live Updates & Match Filtering — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Rendre les décisions (approve/reject) persistantes et visibles en temps réel par tous les utilisateurs, et filtrer les matchs passés du calculateur et de la page Matches.

**Architecture:** Deux axes indépendants. (1) Le backend inclut le champ `status` dans la réponse `GET /recommendations` ; le frontend lit ce champ pour initialiser l'état des cartes, invalide le cache après mutation, et poll toutes les 10s. (2) Le backend ajoute un filtre `upcoming_only` à `GET /fixtures` ; le calculateur l'utilise ; la page Matches remplace son dropdown par deux sections fixes.

**Tech Stack:** FastAPI/SQLAlchemy (backend), Next.js 14 App Router + React Query v5 + Tailwind (frontend), pytest (tests backend).

---

## Contexte à lire avant de commencer

- Design : `docs/plans/2026-03-12-live-updates-design.md`
- Backend API recommendations : `backend/app/api/recommendations.py`
- Backend API fixtures : `backend/app/api/fixtures.py`
- Frontend card : `frontend/src/components/RecommendationCard.tsx`
- Frontend recs page : `frontend/src/app/dashboard/recommendations/page.tsx`
- Frontend matches page : `frontend/src/app/dashboard/matches/page.tsx`
- Frontend calculator page : `frontend/src/app/dashboard/calculator/page.tsx`
- Frontend API client : `frontend/src/lib/api.ts`

---

## Task 1 : Backend — champ `status` dans la réponse recommendations

**Fichiers :**
- Modifier : `backend/app/api/recommendations.py`
- Test : `backend/tests/test_recommendations_status.py` (créer)

### Step 1 : Écrire le test qui échoue

Créer `backend/tests/test_recommendations_status.py` :

```python
"""Tests for status field in recommendations API response."""

from app.api.recommendations import Recommendation


class TestRecommendationStatusField:
    def test_recommendation_has_status_field(self):
        """Recommendation model must expose a status field."""
        rec = Recommendation(
            id=1,
            fixture_id="ext-123",
            fixture_name="PSG vs Lyon",
            kickoff_utc="2026-03-15T20:00:00+00:00",
            player_name="Mbappe",
            team="PSG",
            market_type="goalscorer",
            fair_odds=3.50,
            best_bookmaker="Betclic",
            best_odds=4.00,
            edge=0.14,
            classification="VALUE",
            confidence=0.72,
            explanation={},
            status="pending",
        )
        assert rec.status == "pending"

    def test_recommendation_status_defaults_to_pending(self):
        """If no status provided, default is 'pending'."""
        rec = Recommendation(
            id=1,
            fixture_id="ext-123",
            fixture_name="PSG vs Lyon",
            kickoff_utc="2026-03-15T20:00:00+00:00",
            player_name="Mbappe",
            team="PSG",
            market_type="goalscorer",
            fair_odds=3.50,
            best_bookmaker="Betclic",
            best_odds=4.00,
            edge=0.14,
            classification="VALUE",
            confidence=0.72,
            explanation={},
        )
        assert rec.status == "pending"
```

### Step 2 : Vérifier que le test échoue

```bash
cd backend
.venv/bin/pytest tests/test_recommendations_status.py -v
```

Résultat attendu : `FAILED` — `Recommendation.__init__() got an unexpected keyword argument 'status'`

### Step 3 : Implémenter

Dans `backend/app/api/recommendations.py`, modifier le modèle `Recommendation` (vers la ligne 40) :

**Avant :**
```python
class Recommendation(BaseModel):
    """A betting recommendation."""

    id: int
    fixture_id: str
    fixture_name: str
    kickoff_utc: str
    player_name: str
    team: str
    market_type: MarketType
    fair_odds: float
    best_bookmaker: str
    best_odds: float
    edge: float
    classification: Classification
    confidence: float
    explanation: dict
```

**Après :**
```python
class Recommendation(BaseModel):
    """A betting recommendation."""

    id: int
    fixture_id: str
    fixture_name: str
    kickoff_utc: str
    player_name: str
    team: str
    market_type: MarketType
    fair_odds: float
    best_bookmaker: str
    best_odds: float
    edge: float
    classification: Classification
    confidence: float
    explanation: dict
    status: str = "pending"
```

Ensuite, dans la fonction `get_recommendations` (vers la ligne 176), modifier la section "Transform to response models" pour inclure le statut DB. Trouver ce bloc :

```python
    # Transform to response models
    recommendations = []
    for rec in raw_recs:
        db_id = rec.get("_db_id")
        if db_id is None:
            continue  # Skip recs without a DB id (fixture not found)
        recommendations.append(
            Recommendation(
                id=db_id,
                fixture_id=rec.get("fixture_id", ""),
                fixture_name=rec.get("fixture_name", ""),
                kickoff_utc=str(rec.get("kickoff_utc", "")),
                player_name=rec.get("player_name", ""),
                team=rec.get("team", ""),
                market_type=rec.get("market_type", "goalscorer"),
                fair_odds=rec.get("fair_odds", 0.0),
                best_bookmaker=rec.get("best_bookmaker", ""),
                best_odds=rec.get("market_odds", 0.0),
                edge=rec.get("edge", 0.0),
                classification=rec.get("classification", "NO_VALUE"),
                confidence=rec.get("confidence", 0.5),
                explanation=rec.get("explanation", {}),
            )
        )
```

Remplacer par :

```python
    # Load current statuses from DB for all rec ids
    db_ids = [rec["_db_id"] for rec in raw_recs if rec.get("_db_id")]
    status_map: dict[int, str] = {}
    if db_ids:
        status_rows = await db.execute(
            select(RecommendationModel.id, RecommendationModel.status).where(
                RecommendationModel.id.in_(db_ids)
            )
        )
        status_map = {row.id: row.status for row in status_rows}

    # Transform to response models
    recommendations = []
    for rec in raw_recs:
        db_id = rec.get("_db_id")
        if db_id is None:
            continue  # Skip recs without a DB id (fixture not found)
        recommendations.append(
            Recommendation(
                id=db_id,
                fixture_id=rec.get("fixture_id", ""),
                fixture_name=rec.get("fixture_name", ""),
                kickoff_utc=str(rec.get("kickoff_utc", "")),
                player_name=rec.get("player_name", ""),
                team=rec.get("team", ""),
                market_type=rec.get("market_type", "goalscorer"),
                fair_odds=rec.get("fair_odds", 0.0),
                best_bookmaker=rec.get("best_bookmaker", ""),
                best_odds=rec.get("market_odds", 0.0),
                edge=rec.get("edge", 0.0),
                classification=rec.get("classification", "NO_VALUE"),
                confidence=rec.get("confidence", 0.5),
                explanation=rec.get("explanation", {}),
                status=status_map.get(db_id, "pending"),
            )
        )
```

Vérifier que l'import `select` est bien présent en tête de fichier (il l'est déjà).

### Step 4 : Vérifier que les tests passent

```bash
cd backend
.venv/bin/pytest tests/test_recommendations_status.py -v
```

Résultat attendu : `2 passed`

### Step 5 : Vérifier que les tests existants passent toujours

```bash
cd backend
.venv/bin/pytest tests/ -x -q
```

Résultat attendu : tout vert.

### Step 6 : Commit

```bash
git add backend/app/api/recommendations.py backend/tests/test_recommendations_status.py
git commit -m "feat: include status field in GET /recommendations response"
```

---

## Task 2 : Backend — filtre `upcoming_only` dans `GET /fixtures`

**Fichiers :**
- Modifier : `backend/app/api/fixtures.py`
- Test : `backend/tests/test_fixtures_upcoming_only.py` (créer)

### Step 1 : Écrire le test qui échoue

Créer `backend/tests/test_fixtures_upcoming_only.py` :

```python
"""Tests for upcoming_only filter logic in fixtures endpoint."""

from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock

from app.api.fixtures import _apply_upcoming_only_filter
from app.models.fixtures import Fixture


class TestUpcomingOnlyFilter:
    def test_function_exists(self):
        """_apply_upcoming_only_filter must exist."""
        assert callable(_apply_upcoming_only_filter)

    def test_returns_stmt_unchanged_when_false(self):
        """When upcoming_only=False, statement is returned as-is."""
        stmt = MagicMock()
        result = _apply_upcoming_only_filter(stmt, upcoming_only=False)
        assert result is stmt

    def test_calls_where_when_true(self):
        """When upcoming_only=True, .where() is called on the statement."""
        stmt = MagicMock()
        stmt.where.return_value = stmt
        result = _apply_upcoming_only_filter(stmt, upcoming_only=True)
        stmt.where.assert_called_once()
        assert result is stmt
```

### Step 2 : Vérifier que le test échoue

```bash
cd backend
.venv/bin/pytest tests/test_fixtures_upcoming_only.py -v
```

Résultat attendu : `FAILED` — `ImportError: cannot import name '_apply_upcoming_only_filter'`

### Step 3 : Implémenter

Dans `backend/app/api/fixtures.py`, ajouter la fonction helper juste avant `list_fixtures` (vers la ligne 74) :

```python
def _apply_upcoming_only_filter(stmt, upcoming_only: bool):
    """Filter fixtures to only those with kickoff in the future."""
    if not upcoming_only:
        return stmt
    return stmt.where(Fixture.kickoff_utc > datetime.now(UTC))
```

Puis modifier la signature de `list_fixtures` pour ajouter le param :

**Avant :**
```python
@router.get("/fixtures", response_model=FixturesResponse)
async def list_fixtures(
    db: AsyncSession = Depends(get_db),
    league: str | None = Query(None),
    status: str | None = Query(None),
    from_date: date | None = Query(None),
    to_date: date | None = Query(None),
    limit: int = Query(50, le=200),
):
```

**Après :**
```python
@router.get("/fixtures", response_model=FixturesResponse)
async def list_fixtures(
    db: AsyncSession = Depends(get_db),
    league: str | None = Query(None),
    status: str | None = Query(None),
    from_date: date | None = Query(None),
    to_date: date | None = Query(None),
    limit: int = Query(50, le=200),
    upcoming_only: bool = Query(False),
):
```

Et appliquer le filtre à la fin de la construction du statement, juste avant `.limit(limit)`. Trouver ce bloc dans `list_fixtures` :

```python
    if to_date:
        stmt = stmt.where(
            Fixture.kickoff_utc <= datetime.combine(to_date, datetime.max.time(), tzinfo=UTC)
        )
```

Ajouter après :

```python
    stmt = _apply_upcoming_only_filter(stmt, upcoming_only)
```

### Step 4 : Vérifier que les tests passent

```bash
cd backend
.venv/bin/pytest tests/test_fixtures_upcoming_only.py -v
```

Résultat attendu : `3 passed`

### Step 5 : Vérifier que les tests existants passent

```bash
cd backend
.venv/bin/pytest tests/ -x -q
```

### Step 6 : Commit

```bash
git add backend/app/api/fixtures.py backend/tests/test_fixtures_upcoming_only.py
git commit -m "feat: add upcoming_only param to GET /fixtures"
```

---

## Task 3 : Frontend — RecommendationCard lit le status depuis les props

**Fichiers :**
- Modifier : `frontend/src/lib/api.ts` (interface `Recommendation`)
- Modifier : `frontend/src/components/RecommendationCard.tsx`
- Modifier : `frontend/src/app/dashboard/recommendations/page.tsx`

Pas de tests automatisés frontend. Vérification manuelle décrite au Step 4.

### Step 1 : `api.ts` — ajouter `status` à l'interface `Recommendation`

Dans `frontend/src/lib/api.ts`, trouver l'interface `Recommendation` (ligne ~38) :

```typescript
export interface Recommendation {
  id: string
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
  classification: 'VALUE' | 'NO_VALUE' | 'AVOID'
  confidence: number
  explanation: Record<string, any>
  error?: string | null
}
```

Ajouter `status` :

```typescript
export interface Recommendation {
  id: string
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
  classification: 'VALUE' | 'NO_VALUE' | 'AVOID'
  confidence: number
  explanation: Record<string, any>
  status?: string
  error?: string | null
}
```

### Step 2 : `RecommendationCard.tsx` — 3 changements

**a) Interface locale — ajouter `status`**

Trouver :
```typescript
interface Recommendation {
  id: number
  player: string
  team: string
  opponent: string
  market: string
  fairOdds: number
  bestOdds: number
  bookmaker: string
  edge: number
  confidence: number
  kickoff: string
  explanation?: Record<string, any>
}
```

Remplacer par :
```typescript
interface Recommendation {
  id: number
  player: string
  team: string
  opponent: string
  market: string
  fairOdds: number
  bestOdds: number
  bookmaker: string
  edge: number
  confidence: number
  kickoff: string
  explanation?: Record<string, any>
  status?: 'pending' | 'approved' | 'rejected'
}
```

**b) Initialiser le state depuis rec.status**

Trouver :
```typescript
  const [status, setStatus] = useState<'pending' | 'approved' | 'rejected'>('pending')
```

Remplacer par :
```typescript
  const [status, setStatus] = useState<'pending' | 'approved' | 'rejected'>(
    (rec.status as 'pending' | 'approved' | 'rejected') ?? 'pending'
  )
```

**c) Invalider le cache recommendations après mutation**

Trouver :
```typescript
    onSuccess: (_data, newStatus) => {
      setStatus(newStatus)
      queryClient.invalidateQueries({ queryKey: ['history'] })
      queryClient.invalidateQueries({ queryKey: ['dashboard-stats'] })
    },
```

Remplacer par :
```typescript
    onSuccess: (_data, newStatus) => {
      setStatus(newStatus)
      queryClient.invalidateQueries({ queryKey: ['history'] })
      queryClient.invalidateQueries({ queryKey: ['dashboard-stats'] })
      queryClient.invalidateQueries({ queryKey: ['recommendations'] })
    },
```

### Step 3 : `recommendations/page.tsx` — 2 changements

**a) Ajouter `refetchInterval` à la query principale**

Trouver :
```typescript
  const { data, isLoading, isError, error, refetch } = useQuery({
    queryKey: ['recommendations', selectedDate, marketFilter, edgeFilter],
    enabled: !!selectedDate,
    queryFn: async () => {
```

Remplacer par :
```typescript
  const { data, isLoading, isError, error, refetch } = useQuery({
    queryKey: ['recommendations', selectedDate, marketFilter, edgeFilter],
    enabled: !!selectedDate,
    refetchInterval: 10_000,
    queryFn: async () => {
```

**b) Passer `status` dans le mapping des recs**

Trouver le `.map()` dans `queryFn` :
```typescript
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
```

Remplacer par :
```typescript
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
        status: (rec.status as 'pending' | 'approved' | 'rejected') ?? 'pending',
      }))
```

### Step 4 : Vérification manuelle

Lancer le frontend en local (si possible) ou deployer et vérifier :
1. Approuver une rec → carte verte
2. Recharger la page → carte reste verte (status lu depuis DB)
3. Attendre 10s → polling silencieux sans clignotement

### Step 5 : Commit

```bash
git add frontend/src/lib/api.ts frontend/src/components/RecommendationCard.tsx frontend/src/app/dashboard/recommendations/page.tsx
git commit -m "feat: persist recommendation status across reloads and add 10s polling"
```

---

## Task 4 : Frontend — Calculateur filtre les matchs passés

**Fichiers :**
- Modifier : `frontend/src/lib/api.ts`
- Modifier : `frontend/src/app/dashboard/calculator/page.tsx`

### Step 1 : `api.ts` — ajouter `upcoming_only` à `getFixtures`

Trouver :
```typescript
export async function getFixtures(params?: {
  league?: string
  status?: string
  from_date?: string
  to_date?: string
  limit?: number
}): Promise<FixturesResponse> {
  const { data } = await api.get('/api/v1/fixtures', { params })
  return data
}
```

Remplacer par :
```typescript
export async function getFixtures(params?: {
  league?: string
  status?: string
  from_date?: string
  to_date?: string
  limit?: number
  upcoming_only?: boolean
}): Promise<FixturesResponse> {
  const { data } = await api.get('/api/v1/fixtures', { params })
  return data
}
```

### Step 2 : `calculator/page.tsx` — passer `upcoming_only: true`

Trouver (vers la ligne 241) :
```typescript
    getFixtures({ status: 'scheduled', limit: 200 })
```

Remplacer par :
```typescript
    getFixtures({ status: 'scheduled', limit: 200, upcoming_only: true })
```

### Step 3 : Vérification manuelle

Naviguer vers `/dashboard/calculator`. Les matchs dont le coup d'envoi est passé ne doivent plus apparaître dans le dropdown, même si leur status DB est encore `scheduled`.

### Step 4 : Commit

```bash
git add frontend/src/lib/api.ts frontend/src/app/dashboard/calculator/page.tsx
git commit -m "feat: filter past fixtures from calculator dropdown"
```

---

## Task 5 : Frontend — Page Matches : deux sections fixes

**Fichiers :**
- Modifier : `frontend/src/app/dashboard/matches/page.tsx`

### Step 1 : Lire le fichier complet

Lire `frontend/src/app/dashboard/matches/page.tsx` entièrement avant de modifier.

### Step 2 : Implémenter les deux sections

Remplacer le contenu de `MatchesPage` par la version ci-dessous. Les changements clés :
- Supprimer le state `filter` et le dropdown
- Ajouter state `finishedOpen` (collapsible pour la section terminés)
- Deux queries React Query distinctes : `['fixtures-upcoming']` et `['fixtures-finished']`
- Section "à venir" toujours visible, section "terminés" collapsible

```typescript
export default function MatchesPage() {
  const queryClient = useQueryClient()
  const router = useRouter()
  const [showAddForm, setShowAddForm] = useState(false)
  const [finishedOpen, setFinishedOpen] = useState(false)

  const { data: upcomingData, isLoading: upcomingLoading } = useQuery({
    queryKey: ['fixtures-upcoming'],
    queryFn: () => getFixtures({ status: 'upcoming', upcoming_only: true, limit: 100 }),
  })

  const { data: finishedData, isLoading: finishedLoading } = useQuery({
    queryKey: ['fixtures-finished'],
    queryFn: () => getFixtures({ status: 'finished', limit: 100 }),
    enabled: finishedOpen,
  })

  const upcomingMatches = (upcomingData?.fixtures ?? []).map(fixtureToMatch)
  const finishedMatches = (finishedData?.fixtures ?? []).map(fixtureToMatch)
```

Garder le reste du composant identique (mutations `addMutation`, `deleteMutation`, formulaire d'ajout, rendu des cartes), mais remplacer le bloc de rendu principal par :

```tsx
      {/* Matchs à venir */}
      <div className="mb-8">
        <h2 className="text-lg font-semibold text-white mb-4">
          Matchs à venir
          <span className="ml-2 text-sm font-normal text-gray-400">({upcomingMatches.length})</span>
        </h2>
        {upcomingLoading ? (
          <div className="space-y-3">
            {[1, 2, 3].map((i) => (
              <div key={i} className="bg-gray-800 rounded-xl h-20 animate-pulse" />
            ))}
          </div>
        ) : upcomingMatches.length === 0 ? (
          <div className="bg-gray-800 rounded-xl p-8 text-center text-gray-400">
            Aucun match à venir
          </div>
        ) : (
          <div className="space-y-3">
            {upcomingMatches.map((match) => (
              <MatchCard key={match.id} match={match} onDelete={...} onNavigate={...} />
            ))}
          </div>
        )}
      </div>

      {/* Matchs terminés — collapsible */}
      <div>
        <button
          onClick={() => setFinishedOpen(!finishedOpen)}
          className="flex items-center gap-2 text-gray-400 hover:text-white transition-colors mb-4"
        >
          <ChevronDown className={`w-4 h-4 transition-transform ${finishedOpen ? 'rotate-180' : ''}`} />
          <span className="text-sm font-medium">Matchs terminés</span>
        </button>

        {finishedOpen && (
          finishedLoading ? (
            <div className="space-y-3">
              {[1, 2, 3].map((i) => (
                <div key={i} className="bg-gray-800 rounded-xl h-20 animate-pulse" />
              ))}
            </div>
          ) : finishedMatches.length === 0 ? (
            <p className="text-sm text-gray-500 italic">Aucun match terminé.</p>
          ) : (
            <div className="space-y-3 opacity-75">
              {finishedMatches.map((match) => (
                <MatchCard key={match.id} match={match} onDelete={...} onNavigate={...} />
              ))}
            </div>
          )
        )}
      </div>
```

> **Note :** Le composant `MatchCard` n'existe pas encore — les matchs sont actuellement rendus inline dans la page. Extraire le rendu d'un match en sous-composant `MatchCard` ou garder le JSX inline (copier-coller le bloc existant dans chaque map). Choisir la solution la plus simple : garder inline pour éviter de créer une abstraction inutile.

> **Important :** Ajouter `ChevronDown` aux imports depuis `lucide-react` si pas déjà présent.

### Step 3 : Invalidation après suppression

Dans `deleteMutation.onSuccess`, invalider les deux nouvelles query keys :

```typescript
  const deleteMutation = useMutation({
    mutationFn: deleteFixture,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['fixtures-upcoming'] })
      queryClient.invalidateQueries({ queryKey: ['fixtures-finished'] })
    },
  })
```

### Step 4 : Vérification manuelle

1. Page Matches affiche "Matchs à venir" avec les matchs futurs
2. Les matchs passés ne sont pas dans "à venir"
3. Cliquer "Matchs terminés" → charge et affiche les matchs terminés
4. Supprimer un match → les deux sections se mettent à jour

### Step 5 : Commit

```bash
git add frontend/src/app/dashboard/matches/page.tsx
git commit -m "feat: split matches page into upcoming and finished sections"
```

---

## Task 6 : Push et deploy

### Step 1 : Vérifier que tous les tests backend passent

```bash
cd backend
.venv/bin/pytest tests/ -x -q
```

### Step 2 : Push

```bash
git push
```

### Step 3 : Rebuild backend sur VPS

```bash
ssh -i ~/.ssh/id_ed25519 root@213.130.144.204 \
  "cd /etc/dokploy/compose/ev0-compose-z5hvqt/code && \
   docker compose -p ev0-compose-z5hvqt --env-file .env up -d --build --no-deps backend"
```

### Step 4 : Rebuild frontend sur VPS

```bash
ssh -i ~/.ssh/id_ed25519 root@213.130.144.204 \
  "cd /etc/dokploy/compose/ev0-compose-z5hvqt/code && \
   docker compose -p ev0-compose-z5hvqt --env-file .env up -d --build --no-deps frontend"
```

### Step 5 : Vérifier les logs

```bash
ssh -i ~/.ssh/id_ed25519 root@213.130.144.204 \
  "docker logs ev0-compose-z5hvqt-backend-1 --tail 20"
```

Résultat attendu : aucune erreur au démarrage.
