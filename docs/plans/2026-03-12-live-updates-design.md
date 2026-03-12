# Live Updates & Match Filtering — Design

**Date:** 2026-03-12

## Objectif

1. **Recommandations temps réel** : les décisions (approve/reject) persistent au rechargement et sont visibles par tous les utilisateurs en ≤ 10 secondes.
2. **Filtrage matchs par date** : les matchs passés n'apparaissent plus dans le calculateur ni dans la section "à venir" de la page Matches.

---

## Partie 1 : Recommandations temps réel

### Cause racine

- `GET /recommendations` ne renvoie pas le champ `status` (approved/rejected/pending).
- `RecommendationCard` initialise toujours son état local à `'pending'` hardcodé.
- Après mutation, seuls `['history']` et `['dashboard-stats']` sont invalidés — pas `['recommendations', ...]`.
- Aucun polling → Guillaume ne voit pas les décisions de Yohan en temps réel.

### Changes backend

**`backend/app/api/recommendations.py`**

- Ajouter `status: str` au modèle Pydantic `Recommendation` (valeur par défaut `"pending"`).
- Dans `GET /recommendations` : après avoir généré et sauvé les recs, charger leurs statuts actuels depuis la DB (par `id`) et les attacher à la réponse.

### Changes frontend

**`frontend/src/components/RecommendationCard.tsx`**

- Ajouter `status: 'pending' | 'approved' | 'rejected'` à l'interface `Recommendation`.
- Initialiser `useState` depuis `rec.status` au lieu de `'pending'` hardcodé.
- Dans `mutation.onSuccess` : invalider aussi `queryClient.invalidateQueries({ queryKey: ['recommendations'] })`.

**`frontend/src/app/dashboard/recommendations/page.tsx`**

- Ajouter `refetchInterval: 10_000` à la query recommendations principale.
- Passer `status` depuis l'API vers chaque `RecommendationCard`.

---

## Partie 2 : Filtrage des matchs passés

### Cause racine

- Le calculateur charge `status=scheduled` mais un match dont le coup d'envoi est passé peut rester `scheduled` si le worker n'a pas encore mis à jour son statut.
- La page Matches affiche un dropdown all/upcoming/finished sans section dédiée "terminés".

### Changes backend

**`backend/app/api/fixtures.py`**

- Ajouter query param `upcoming_only: bool = False`.
- Quand `upcoming_only=True` : ajouter filtre `Fixture.kickoff_utc > datetime.now(UTC)`.

### Changes frontend

**`frontend/src/lib/api.ts`**

- Ajouter `upcoming_only?: boolean` au type des params de `getFixtures`.

**`frontend/src/app/dashboard/calculator/page.tsx`**

- Passer `upcoming_only: true` dans l'appel `getFixtures({ status: 'scheduled', upcoming_only: true })`.

**`frontend/src/app/dashboard/matches/page.tsx`**

- Supprimer le dropdown de filtre (all/upcoming/finished).
- Remplacer par deux sections fixes :
  - **Matchs à venir** : `getFixtures({ status: 'upcoming', upcoming_only: true })` — triés kickoff asc.
  - **Matchs terminés** : `getFixtures({ status: 'finished' })` — triés kickoff desc, collapsible.
- Chaque section affiche son propre état de chargement.
