# Recommendations — View All Design Spec

**Date:** 2026-04-01
**Status:** Approved

## Problème

La page Recommendations est actuellement centrée sur la date : `selectedDate` est initialisé à aujourd'hui et sert de filtre principal. Si on veut voir toutes les recommandations à venir (sur plusieurs jours), il faut naviguer date par date. Il n'y a pas de vue consolidée.

## Solution

Rendre le filtre date secondaire. Le comportement par défaut devient "View All" — toutes les recommandations actives triées chronologiquement, paginées 50/page. Le filtre date reste disponible mais uniquement comme opt-in explicit. Les filtres marché et edge restent actifs dans les deux modes.

## Périmètre

- **Vue par défaut** : View All (toutes les recos actives, paginées)
- **Filtre date** : opt-in uniquement, bascule vers la vue date-filtrée (pas de pagination)
- **Filtres marché/edge** : orthogonaux — actifs dans les deux modes, ne changent pas le mode
- **Section expirées** : même logique, view all par défaut (paginée), date-filtrée si date active
- **Auto-refresh** : 10s conservé
- **Pas de changement** : RecommendationCard, approve/reject, lineup fetch, collapsible expirées

## Design détaillé

### 1. État frontend

```typescript
// Avant
const [selectedDate, setSelectedDate] = useState<string>(new Date().toISOString().split('T')[0])

// Après
const [selectedDate, setSelectedDate] = useState<string | null>(null)  // null = View All
const [page, setPage] = useState(1)
```

Reset de `page` à 1 à chaque changement de `selectedDate`, `marketFilter` ou `edgeFilter`.

### 2. Filtre date — UI

- Remplace le date picker actuel par un bouton "Filtrer par date"
- Quand `selectedDate !== null` : affiche un badge `"12 avr ✕"` ; clic sur ✕ → `setSelectedDate(null)`, `setPage(1)`
- Quand `selectedDate === null` : bouton neutre "Filtrer par date" qui ouvre le date picker

### 3. Query keys

```typescript
// Actives
['recommendations', selectedDate, marketFilter, edgeFilter, page]

// Expirées
['recommendations-expired', selectedDate, page]
```

`page` n'est inclus dans la query que si `selectedDate === null` (en mode date, pas de pagination côté API).

### 4. Pagination — UI

- Visible uniquement quand `selectedDate === null`
- Contrôles : `← Page précédente` / `Page X / Y` / `Page suivante →`
- Désactivé sur première/dernière page selon le cas
- Affiché en bas de chaque section (actives + expirées)

### 5. Backend — `/api/v1/recommendations`

`target_date` devient optionnel.

**Sans `target_date`** (View All) :
```sql
SELECT * FROM recommendations
WHERE status IN ('pending', 'approved')
  AND [filtres marché/edge]
ORDER BY kickoff_utc ASC
LIMIT page_size OFFSET (page - 1) * page_size
```

**Avec `target_date`** (comportement actuel conservé) :
- Filtre sur la date du kickoff, pas de pagination

**Paramètres query** :
- `target_date` : optionnel, format `YYYY-MM-DD`
- `market_type` : optionnel, `'goalscorer' | 'assist'`
- `min_edge` : optionnel, float
- `page` : optionnel, défaut 1 (ignoré si `target_date` fourni)
- `page_size` : optionnel, défaut 50 (ignoré si `target_date` fourni)

**Réponse** :
```typescript
// Mode View All
{
  items: Recommendation[],
  total: number,
  page: number,
  page_size: number,
  pages: number,
}

// Mode date (rétrocompatible — même structure qu'aujourd'hui)
{
  items: Recommendation[],
  total: number,      // total du jour
  page: 1,
  page_size: total,   // tout retourné en une fois
  pages: 1,
}
```

### 6. Backend — `/api/v1/recommendations/expired`

Même logique :
- Sans `target_date` : toutes les expirées, `ORDER BY kickoff_utc DESC`, paginées
- Avec `target_date` : expirées du jour, comportement actuel

### 7. Fichiers modifiés

| Fichier | Changement |
|---------|-----------|
| `backend/app/api/recommendations.py` | `target_date` optionnel + pagination + réponse enrichie |
| `frontend/src/app/dashboard/recommendations/page.tsx` | `selectedDate` → `null` par défaut, `page` state, pagination UI, filtre date opt-in |
| `frontend/src/lib/api.ts` | Params `page`/`page_size` optionnels, type réponse paginée |

## Tests

| Test | Description |
|------|-------------|
| `test_recommendations_no_date_returns_all` | Sans `target_date` → toutes les recos actives, triées par `kickoff_utc` |
| `test_recommendations_pagination` | `page=1&page_size=2` → 2 items, `total` correct, `pages` correct |
| `test_recommendations_with_date_no_pagination` | Avec `target_date` → comportement actuel, `pages=1` |
| `test_recommendations_expired_no_date` | Sans `target_date` → toutes expirées, triées DESC |
| `test_market_edge_filter_in_view_all` | Filtres marché/edge s'appliquent en view all |
