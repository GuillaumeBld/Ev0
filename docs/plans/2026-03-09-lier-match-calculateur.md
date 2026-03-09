# Lier Match → Calculateur — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Cliquer sur un match dans l'onglet Matchs ouvre le Calculateur avec ce match pré-sélectionné et le pricing chargé automatiquement.

**Architecture:** (1) Rendre la zone principale de chaque MatchCard cliquable via `router.push`. (2) Le Calculateur lit `?match=` via `useSearchParams` et auto-sélectionne le fixture dès que la liste est chargée.

**Tech Stack:** Next.js 14 App Router, `next/navigation` (`useRouter`, `useSearchParams`), React `useEffect`

---

### Task 1 : Calculator lit le paramètre `?match=` dans l'URL

**Files:**
- Modify: `frontend/src/app/dashboard/calculator/page.tsx`

**Step 1 : Extraire le composant principal + ajouter Suspense**

Next.js 14 exige un `<Suspense>` autour de tout composant qui appelle `useSearchParams`.
Renommer l'export par défaut `CalculatorPage` → `CalculatorInner`, créer un nouveau `CalculatorPage` qui wrap dans `<Suspense>`.

Ajouter dans les imports :
```ts
import { useSearchParams } from 'next/navigation'
import { Suspense } from 'react'
```

Remplacer `export default function CalculatorPage()` par `function CalculatorInner()`.

Ajouter en bas du fichier :
```tsx
export default function CalculatorPage() {
  return (
    <Suspense>
      <CalculatorInner />
    </Suspense>
  )
}
```

**Step 2 : Lire le paramètre et auto-sélectionner le fixture**

Dans `CalculatorInner`, après les déclarations de state, ajouter :
```ts
const searchParams = useSearchParams()
const matchParam = searchParams.get('match')
```

Modifier le `useEffect` qui charge les fixtures (ligne ~235) pour qu'après `setFixtures`, si `matchParam` est présent et le fixture existe, on appelle `setSelectedFixtureId` :
```ts
useEffect(() => {
  setLoadingFixtures(true)
  getFixtures({ status: 'scheduled', limit: 200 })
    .then((res) => {
      setFixtures(res.fixtures)
      if (matchParam) {
        const id = Number(matchParam)
        const found = res.fixtures.find(f => f.id === id)
        if (found) setSelectedFixtureId(id)
      }
    })
    .catch(() => setFixtures([]))
    .finally(() => setLoadingFixtures(false))
}, [matchParam])
```

---

### Task 2 : MatchCard entièrement cliquable

**Files:**
- Modify: `frontend/src/app/dashboard/matches/page.tsx`

**Step 1 : Ajouter `useRouter`**

Ajouter dans les imports :
```ts
import { useRouter } from 'next/navigation'
```

**Step 2 : Rendre la zone principale cliquable**

Dans `MatchCard`, ajouter `const router = useRouter()`.

Sur le `<div className="p-4">` principal, ajouter un `onClick` qui navigue, tout en protégeant les boutons d'action avec `e.stopPropagation()` :

```tsx
<div
  className="p-4 cursor-pointer"
  onClick={() => router.push(`/dashboard/calculator?match=${match.id}`)}
>
```

Sur le `<button>` de suppression (Trash2), ajouter :
```tsx
onClick={(e) => { e.stopPropagation(); onDelete(match.id) }}
```

Supprimer le `<Link>` ChevronRight (devenu redondant) et remplacer par un simple `<span>` visuel.

---
