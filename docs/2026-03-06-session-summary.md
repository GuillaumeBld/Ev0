# Résumé de session — 6 mars 2026

> **Note pour l'agent de Guillaume :** Ce document résume toutes les modifications apportées au projet Ev0 lors de la session de développement du 6 mars 2026. Présente ce contenu à Guillaume quand il demande un résumé de la journée.

---

## Vue d'ensemble

Grosse session de maintenance et de corrections post-déploiement du scraper Betclic gRPC. Le site était tombé à plusieurs reprises — voici tout ce qui a été diagnostiqué et corrigé, dans l'ordre chronologique.

---

## 1. Crash CPU à 98% — Scraper Betclic (blackboxprotobuf)

**Symptôme :** Le worker saturait le CPU à 98%, rendant le site inaccessible.

**Cause :** `blackboxprotobuf.decode_message()` entrait dans une boucle infinie sur certains matchs Betclic. `asyncio.wait_for` + `run_in_executor` ne peuvent pas interrompre un thread Python bloqué.

**Fix (commit `4310e86`) :** Remplacement complet de `blackboxprotobuf` par un parser protobuf artisanal (~50 lignes, pur Python) :
- Fonctions `_proto_varint` et `_proto_fields` qui lisent le protobuf linéairement — impossible de boucler
- Les cotes sont décodées directement en IEEE 754 little-endian : `struct.unpack("<d", 8_bytes)`
- Fichier : `backend/app/ingestion/betclic_grpc_scraper.py`

---

## 2. Redéploiement raté après push GitHub

**Symptôme :** Dokploy a déclenché un rebuild automatique qui a supprimé l'image backend. Le site était down.

**Cause :** Dokploy's auto-deploy (`--build`) entre en conflit avec les conteneurs redémarrés manuellement.

**Fix :** Rebuild manuel des images backend + worker, puis `docker compose up -d --no-build`. Tous les 6 conteneurs remis en ligne.

**Rappel pour l'avenir :** Pour déployer sans risque, toujours utiliser :
```bash
cd /etc/dokploy/compose/ev0-compose-z5hvqt/code
docker compose -p ev0-compose-z5hvqt build backend worker
docker compose -p ev0-compose-z5hvqt --env-file .env up -d --no-build
```

---

## 3. MissingGreenlet — Odds non stockées en DB

**Symptôme :** `sqlalchemy.exc.MissingGreenlet: greenlet_spawn has not been called` lors du stockage des odds. Les sélections étaient matchées (45/50 fixtures) mais aucune n'était enregistrée.

**Cause :** Dans `job_snapshot_direct_odds` (worker.py), un `session.rollback()` en cas de doublon expirait **tous** les objets ORM du session. L'itération suivante tentait `f.league` sur un objet expiré → lazy load → MissingGreenlet (impossible en async).

**Fix (commit `bfe0e59`) :** Remplacement du `session.rollback()` par `async with session.begin_nested()` (savepoint). Un doublon rollback uniquement sa propre transaction, les objets restent valides.

- Fichier : `backend/app/worker.py`, fonction `job_snapshot_direct_odds`

---

## 4. Timeout sur la liste des matchs — Fixture list

**Symptôme :** La page des matchs (et le calculateur) ne s'ouvrait plus. La requête `/api/v1/fixtures?status=scheduled` timeout.

**Cause :** `selectinload(Fixture.odds_snapshots)` chargeait **30 000 à 37 000 odds** par fixture en mémoire (accumulation sur plusieurs jours × scraping toutes les 3h). Pour 50 fixtures, c'est >1,5M de lignes.

**Fix (commit `1dae06f`) :** Remplacement du `selectinload` par un subquery `COUNT(*)` :
```python
odds_count_subq = select(OddsSnapshot.fixture_id, func.count().label("cnt")).group_by(...).subquery()
```
La liste retourne `odds=[]` (vide) avec juste le compte. Les odds détaillées sont chargées uniquement sur demande (endpoint `/fixtures/{id}/odds`).

- Fichier : `backend/app/api/fixtures.py`

---

## 5. Calculateur xG — Rechargement à chaque frappe

**Symptôme :** Impossible de saisir un nombre décimal dans les champs xG override. Chaque chiffre tapé déclenchait un rechargement de la page.

**Cause :** `homeXgOverride` et `awayXgOverride` étaient dans les deps de `useCallback(fetchPricing)`, qui lui-même était dans les deps du `useEffect`. Chaque frappe → nouveau `fetchPricing` → `useEffect` déclenché → appel API.

**Fix (commit `89d504f`) :**
- Les valeurs xG sont lues via `useRef` (pas de re-render dependency)
- Le `useEffect` ne se déclenche plus sur les xG — seulement sur le changement de match et de tireur de penalty
- Ajout d'un bouton **"Recalculer avec ces xG"** avec spinner intégré
- Sensibilité de la molette passée de `step=0.1` à `step=0.05`

- Fichier : `frontend/src/app/dashboard/calculator/page.tsx`

---

## 6. Erreur 422 — Approve/Reject d'une recommandation

**Symptôme :** Message "Erreur: Request failed with status code 422" en approuvant ou rejetant une recommandation.

**Cause racine (en deux temps) :**

**6a.** Le type `recommendation_id` dans le backend était `int`, mais le frontend envoyait des UUID (`"26fdf45a-..."`). FastAPI ne peut pas convertir un UUID en int → 422.

**6b.** (cause plus profonde) Le endpoint `GET /recommendations` générait des recs à la volée avec des `uuid.uuid4()` aléatoires à chaque appel. Ces UUIDs n'existaient pas en DB (la DB stocke des IDs integers). Les deux étaient complètement déconnectés.

**Fix (commit `ef06063`) :**
- Le `GET /recommendations` persiste maintenant chaque recommandation en DB lors de la génération (upsert par `fixture_id + player_name + market_type` pour la date du jour)
- Retourne les integer IDs de la DB
- Le `PATCH /recommendations/{id}` trouve le bon enregistrement et met à jour le statut (approved/rejected)

- Fichiers : `backend/app/api/recommendations.py`, `frontend/src/components/RecommendationCard.tsx`, `frontend/src/app/dashboard/recommendations/page.tsx`

---

## État du système en fin de session

| Service | Statut | CPU |
|---------|--------|-----|
| backend | ✅ Up | 0.18% |
| worker | ✅ Up | ~1% |
| frontend | ✅ Up | 0% |
| db | ✅ Up | 0.15% |
| redis | ✅ Up | 0.42% |
| db-backup | ✅ Up | 0% |

**DB :** 922 397 odds snapshots, 875 fixtures, 38+ recommandations stockées.

**Betclic gRPC :** 7 matchs scrapés par cycle (Ligue 1 + PL + UCL), ~40-76 sélections par match, sans CPU hang.

---

## Commits du jour

| Hash | Description |
|------|-------------|
| `4310e86` | fix: replace blackboxprotobuf with hand-written proto parser |
| `bfe0e59` | fix: use savepoint instead of rollback (MissingGreenlet) |
| `1dae06f` | fix: use subquery COUNT instead of selectinload (timeout fixtures) |
| `89d504f` | fix: xG inputs + Recalculer button + step 0.05 |
| `1dae06f` | fix: recommendation_id type + persist recs to DB on GET |
| `ef06063` | fix: persist dynamic recommendations to DB, integer IDs |
