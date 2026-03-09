# Historique : Onglets Approuvés + AutoFlat — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Refondre la page historique en deux onglets distincts : "Approuvés" (paris manuellement validés, settlement manuel, badge Running gris) et "AutoFlat" (toutes les recos en flat 10€, résultat calculé depuis MatchEvents).

**Architecture:** (1) Backend : modifier `GET /history` pour n'afficher que les recos `approved`, ajouter `GET /history/autoflat` qui calcule les résultats depuis MatchEvents pour toutes les recos. (2) Frontend : page histoire avec deux onglets, colonne P&L avec badge "Running" gris quand result=null, boutons de settlement toujours disponibles quand result=null.

**Tech Stack:** FastAPI, SQLAlchemy async, Next.js 14, React Query, TanStack Query

---

### Task 1 : Backend — modifier GET /history pour "Approuvés" uniquement

**Files:**
- Modify: `backend/app/api/history.py`

**Step 1 : Restreindre la query aux recos `approved`**

Dans `get_history()`, remplacer le filtre `where` :
```python
# Avant
.where(
    or_(
        Recommendation.status.in_(["executed", "approved"]),
        Recommendation.result.isnot(None),
    )
)
# Après
.where(Recommendation.status == "approved")
```

**Step 2 : Supprimer le filtre `status` par résultat et adapter**

Remplacer la section de filtrage par résultat :
```python
if status and status != "all":
    if status == "running":
        stmt = stmt.where(Recommendation.result.is_(None))
    elif status in ("won", "lost", "void"):
        stmt = stmt.where(Recommendation.result == status)
```

**Step 3 : Corriger `display_status` pour renvoyer "running" quand result=null**

```python
display_status = rec.result if rec.result else "running"
```

**Step 4 : Corriger `pnl` — ne pas renvoyer 0.0 pour les running**

```python
pnl=rec.pnl if rec.result else None,
```

---

### Task 2 : Backend — nouveau endpoint GET /history/autoflat

**Files:**
- Modify: `backend/app/api/history.py`

Ajouter avant le endpoint `/stats` :

```python
@router.get("/history/autoflat", response_model=HistoryResponse)
async def get_autoflat_history(
    db: AsyncSession = Depends(get_db),
    limit: int = Query(500, le=1000),
):
    """All recommendations treated as 10€ flat bets, result computed from MatchEvents."""
    from app.models.match_events import MatchEvent

    _market_to_event = {
        "goalscorer": "goal",
        "anytime_score": "goal",
        "assist": "assist",
        "anytime_assist": "assist",
    }

    stmt = (
        select(Recommendation, Fixture)
        .join(Fixture, Recommendation.fixture_id == Fixture.id)
        .order_by(Recommendation.generated_utc.desc())
        .limit(limit)
    )
    result = await db.execute(stmt)
    rows = result.all()

    bets = []
    for rec, fixture in rows:
        fixture_name = f"{fixture.home_team} vs {fixture.away_team}"

        # Compute result from MatchEvents if fixture is finished and no result stored
        computed_result = rec.result
        computed_pnl = rec.pnl

        if computed_result is None and fixture.status == "finished":
            event_type = _market_to_event.get(rec.market_type, rec.market_type)
            ev = await db.execute(
                select(MatchEvent).where(
                    MatchEvent.fixture_id == fixture.id,
                    MatchEvent.player_name == rec.player_name,
                    MatchEvent.event_type == event_type,
                ).limit(1)
            )
            won = ev.scalar_one_or_none() is not None
            computed_result = "won" if won else "lost"
            computed_pnl = round(10.0 * (rec.best_odds - 1) if won else -10.0, 2)

        display_status = computed_result if computed_result else "running"

        bets.append(
            HistoryItem(
                id=rec.id,
                date=str(rec.generated_utc.date()) if rec.generated_utc else "",
                fixture_name=fixture_name,
                player_name=rec.player_name,
                market_type=rec.market_type,
                best_odds=rec.best_odds,
                edge=rec.edge,
                best_bookmaker=rec.best_bookmaker,
                status=display_status,
                result=computed_result,
                pnl=computed_pnl,
                stake=10.0,
            )
        )

    return HistoryResponse(count=len(bets), bets=bets)
```

---

### Task 3 : Frontend — api.ts ajouter getAutoflatHistory

**Files:**
- Modify: `frontend/src/lib/api.ts`

Après `getHistory` :

```ts
export async function getAutoflatHistory(): Promise<HistoryResponse> {
  const { data } = await api.get('/api/v1/history/autoflat')
  return data
}
```

---

### Task 4 : Frontend — refonte de la page historique

**Files:**
- Modify: `frontend/src/app/dashboard/history/page.tsx`

**Step 1 : Ajouter type `'running'` à BetStatus et import**

```ts
type BetStatus = 'won' | 'lost' | 'running' | 'void'
import { getHistory, getAutoflatHistory } from '@/lib/api'
```

**Step 2 : Ajouter state pour l'onglet actif**

```ts
const [activeTab, setActiveTab] = useState<'approved' | 'autoflat'>('approved')
```

**Step 3 : Deux queries React Query**

```ts
const { data: approvedData, isLoading: loadingApproved } = useQuery({
  queryKey: ['history-approved', statusFilter],
  queryFn: () => getHistory(statusFilter !== 'all' ? { status: statusFilter } : {}),
  enabled: activeTab === 'approved',
})

const { data: autoflatData, isLoading: loadingAutoflat } = useQuery({
  queryKey: ['history-autoflat'],
  queryFn: () => getAutoflatHistory(),
  enabled: activeTab === 'autoflat',
})

const data = activeTab === 'approved' ? approvedData : autoflatData
const isLoading = activeTab === 'approved' ? loadingApproved : loadingAutoflat
```

**Step 4 : Remplacer le filtre "Tous/Gagnes/Perdus/En cours" par onglets**

Ajouter les onglets en haut :

```tsx
{/* Tabs */}
<div className="flex gap-1 bg-gray-800 rounded-lg p-1 mb-6 w-fit">
  {(['approved', 'autoflat'] as const).map((tab) => (
    <button
      key={tab}
      onClick={() => setActiveTab(tab)}
      className={clsx(
        'px-4 py-2 rounded-md text-sm font-medium transition-colors',
        activeTab === tab ? 'bg-brand-600 text-white' : 'text-gray-400 hover:text-white'
      )}
    >
      {tab === 'approved' ? 'Approuvés' : 'AutoFlat'}
    </button>
  ))}
</div>
```

Remplacer les filtres de statut existants par ceux-ci (seulement dans l'onglet Approuvés) :

```tsx
{activeTab === 'approved' && (
  <div className="flex gap-2 mb-4">
    {(['all', 'running', 'won', 'lost'] as const).map((status) => (
      <button
        key={status}
        onClick={() => setStatusFilter(status)}
        className={clsx(
          'px-3 py-1.5 rounded-lg text-sm transition-colors',
          statusFilter === status ? 'bg-brand-600 text-white' : 'bg-gray-800 text-gray-400 hover:text-white'
        )}
      >
        {status === 'all' ? 'Tous' : status === 'running' ? 'Running' : status === 'won' ? 'Gagnés' : 'Perdus'}
      </button>
    ))}
  </div>
)}
```

**Step 5 : Badge Running gris dans StatusBadge**

```tsx
function StatusBadge({ status }: { status: BetStatus }) {
  switch (status) {
    case 'won':
      return <CheckCircle className="w-5 h-5 text-green-400 mx-auto" />
    case 'lost':
      return <XCircle className="w-5 h-5 text-red-400 mx-auto" />
    case 'running':
      return <span className="px-2 py-0.5 rounded text-xs bg-gray-600 text-gray-300">Running</span>
    default:
      return <span className="text-gray-500">-</span>
  }
}
```

**Step 6 : Colonne P&L — afficher "—" quand result=null (Running)**

Dans la cellule P&L :
```tsx
<td className={clsx(
  'px-4 py-3 text-sm text-right font-medium',
  bet.pnl != null && bet.pnl > 0 ? 'text-green-400' :
  bet.pnl != null && bet.pnl < 0 ? 'text-red-400' : 'text-gray-500'
)}>
  {bet.status === 'running' ? '—' : `${(bet.pnl ?? 0) >= 0 ? '+' : ''}${(bet.pnl ?? 0).toFixed(2)}EUR`}
</td>
```

**Step 7 : Boutons de settlement toujours visibles quand result=null**

Dans la cellule Résultat, remplacer la condition `bet.status === 'pending'` par `bet.status === 'running'` :
```tsx
{bet.status === 'running' ? (
  <ResultActions
    betId={bet.id}
    onSettle={(result) => settleMutation.mutate({ id: bet.id, result })}
    isLoading={settleMutation.isPending && settleMutation.variables?.id === bet.id}
  />
) : (
  <StatusBadge status={bet.status} />
)}
```

**Step 8 : Adapter `historyItemToBet` pour le nouveau type**

```ts
function historyItemToBet(item: HistoryItem): HistoricalBet {
  return {
    id: item.id.toString(),
    date: item.date,
    fixture: item.fixture_name,
    player: item.player_name,
    market: item.market_type as 'goalscorer' | 'assist',
    odds: item.best_odds,
    stake: item.stake ?? 10,
    edge: item.edge,
    status: (item.status || 'running') as BetStatus,
    pnl: item.pnl ?? null,
  }
}
```

Et mettre à jour l'interface `HistoricalBet` :
```ts
interface HistoricalBet {
  ...
  pnl: number | null
}
```

**Step 9 : Stats — exclure les "running" du P&L total**

```ts
const stats = {
  total: allBets.length,
  won: allBets.filter(b => b.status === 'won').length,
  lost: allBets.filter(b => b.status === 'lost').length,
  running: allBets.filter(b => b.status === 'running').length,
  totalPnl: allBets.reduce((sum, b) => sum + (b.pnl ?? 0), 0),
}
```

Et dans les StatBox :
```tsx
<StatBox label="Running" value={stats.running} color="gray" />
```

---

### Task 5 : Déploiement

**Step 1 : Copier les fichiers**
```bash
VPS="root@213.130.144.204"
scp backend/app/api/history.py "$VPS:/etc/dokploy/compose/ev0-compose-z5hvqt/code/backend/app/api/history.py"
scp frontend/src/lib/api.ts "$VPS:/etc/dokploy/compose/ev0-compose-z5hvqt/code/frontend/src/lib/api.ts"
scp frontend/src/app/dashboard/history/page.tsx "$VPS:/etc/dokploy/compose/ev0-compose-z5hvqt/code/frontend/src/app/dashboard/history/page.tsx"
```

**Step 2 : Redémarrer backend**
```bash
ssh root@213.130.144.204 "cd /etc/dokploy/compose/ev0-compose-z5hvqt/code && docker compose -p ev0-compose-z5hvqt --env-file .env up -d --force-recreate --no-build backend"
```

**Step 3 : Rebuilder frontend**
```bash
ssh root@213.130.144.204 "cd /etc/dokploy/compose/ev0-compose-z5hvqt/code && docker compose -p ev0-compose-z5hvqt --env-file .env up -d --build frontend 2>&1 | tail -5"
```
