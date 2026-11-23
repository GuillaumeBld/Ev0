# Étape C1 — Calendrier de toutes les compétitions + xG de tous les matchs

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Tous les matchs de clubs connus de l'Atlas entrent dans le calendrier d'Ev0 avec un statut juste, et chaque match proposé par PS3838 reçoit son xG.

**Architecture:** Le périmètre de synchro (`SYNCED_COMPETITION_IDS`) est dérivé de `LEAGUE_REGISTRY` au lieu d'une liste écrite à la main. La synchro des statuts apprend « reporté / annulé / mi-temps ». L'accrochage PS3838 accepte un match quand UNE équipe est reconnue avec certitude et que l'horaire colle, sous garde-fous. Le worker ne calcule plus de cotes pour les matchs sans aucune cote de book (rien à recommander). Le calculateur charge 14 jours et range les compétitions par importance.

**Tech Stack:** FastAPI, SQLAlchemy async, PostgreSQL, APScheduler (worker), Next.js 14.

**Spec:** roadmap `memory/project_ev0_roadmap_pricing.md` (étape C) + état du scraper du 23/11 (conversation). Pas de document de conception séparé : l'utilisateur a demandé de démarrer directement ; les choix ci-dessous marqués « choix de l'agent » sont à valider à la relecture du plan.

## Global Constraints

- Jamais de devinette : un accrochage ambigu reste non accroché et apparaît dans l'alerte existante des matchs sans xG ; jamais d'échec silencieux.
- Choix de l'agent : périmètre = compétitions de l'Atlas de type `domestic_1`, `domestic_2`, `uefa_club`, `world`, `cup` + internationales déjà synchronisées. Les championnats féminins (`women`) restent HORS calendrier.
- Ne jamais relancer `repair_fixture_competitions` tel quel (27 matchs Bundesliga de mai étiquetés 35 = Copa do Brasil chez Bzzoiro).
- Recos et Telegram : aucun changement de règle. Les recos 1N2 lisent `FR_H2H_BOOKMAKERS`, les recos joueurs exigent des cotes joueurs : un match sans book ne produit rien (verrouillé par test, tâche 4).
- Textes visibles en français, langage foot/paris.
- Tests : `cd backend && .venv/bin/python -m pytest -q` ; front : `cd frontend && npx tsc --noEmit && npm run build`.
- Un seul déploiement à la fin (PR préremplie, l'utilisateur merge ; `gh` du Mac déconnecté).

## Review Focus

1. Un club joue deux matchs dans la fenêtre de ±2 h d'un autre match (championnat + coupe le même jour, rare mais Bzzoiro a des doublons) : l'accrochage par une seule équipe doit refuser, pas choisir → test tâche 3.
2. Une réserve ou une équipe féminine (« Braga B », « Braga W ») joue à la même heure que l'équipe première : jamais accrochée à la place de l'équipe première → test tâche 3.
3. Un match reporté puis reprogrammé par Bzzoiro (statut `notstarted` à une nouvelle date) : le statut repasse à `scheduled` et le coup d'envoi suit → test tâche 1.
4. Un match passé en `postponed` qui avait déjà une reco : la reco ne reste pas « en attente » figée → test tâche 1 (passe par `job_expire_recommendations`, vérifier qu'il couvre les matchs reportés).
5. Une journée chargée (≈ 150 matchs un samedi) dans le calculateur : la liste reste lisible, les compétitions principales en tête → vérification tâche 5.

---

### Task 1: Statuts reporté / annulé / mi-temps synchronisés

**Files:**
- Modify: `backend/app/ingestion/bzzoiro/sync_fixture_status.py:40-44` (table `_BZZ_TO_FIXTURE_STATUS`) et la boucle de mise à jour
- Modify (si nécessaire après lecture) : `backend/app/worker.py` `job_expire_recommendations`
- Test: `backend/tests/ingestion/test_sync_fixture_status_statuts.py`

**Interfaces:**
- Produces: `_BZZ_TO_FIXTURE_STATUS` complète : `finished→finished`, `inprogress|live|1st_half|2nd_half|halftime→live`, `postponed→postponed`, `canceled|cancelled→cancelled`, `notstarted→scheduled`.

- [ ] **Step 1:** Lire sur la base les statuts Bzzoiro réellement présents (`select distinct status from bzz_events`) et compléter la table avec chacun ; consigner la liste dans le test.
- [ ] **Step 2:** Tests (fonctions pures ou session simulée comme les tests existants du module) : événement `postponed` → fixture `postponed` ; `canceled` → `cancelled` ; `1st_half` → `live` ; fixture `postponed` dont l'événement redevient `notstarted` à une nouvelle date → `scheduled` + coup d'envoi mis à jour ; statut inconnu (`unresolved`) → fixture inchangée ET ligne de log warning.
- [ ] **Step 3:** Lancer → FAIL.
- [ ] **Step 4:** Implémenter. La requête de chargement inclut déjà les fixtures non `finished` : vérifier qu'un `postponed` est relu (sinon il ne redeviendrait jamais `scheduled`).
- [ ] **Step 5:** Vérifier que `job_expire_recommendations` expire les recos `pending/approved` d'un match `postponed` ou `cancelled` ; sinon l'ajouter, avec un test.
- [ ] **Step 6:** `.venv/bin/python -m pytest -q tests/ingestion` → PASS. Commit `fix(statuts): matchs reportés, annulés et en cours suivis depuis Bzzoiro`.

### Task 2: Calendrier = toutes les compétitions de clubs de l'Atlas

**Files:**
- Modify: `backend/app/services/competitions.py` (`SYNCED_COMPETITION_IDS`)
- Test: `backend/tests/test_competitions.py`

**Interfaces:**
- Consumes: `LEAGUE_REGISTRY`, `KIND_*` de `app/ingestion/bzzoiro/league_registry.py`.
- Produces: `SYNCED_COMPETITION_IDS: frozenset[int]` = ids historiques + internationales + `{cid for cid, m in LEAGUE_REGISTRY.items() if m.kind in CALENDRIER_KINDS}` ; `CALENDRIER_KINDS = frozenset({KIND_DOMESTIC_1, KIND_DOMESTIC_2, KIND_UEFA_CLUB, KIND_WORLD, KIND_CUP})`.

- [ ] **Step 1:** Tests : 8, 17, 23, 83 ∈ périmètre ; aucun id de `kind == KIND_WOMEN` ; toutes les anciennes valeurs `{1,3,4,5,6,7,27,31,64,65,90}` y restent ; `league_key_for(17) == "comp_17"` inchangé.
- [ ] **Step 2:** Lancer → FAIL. **Step 3:** Implémenter (supprime les constantes Europa/Conference ajoutées le 23/11, devenues redondantes).
- [ ] **Step 4:** `.venv/bin/python -m pytest -q` (suite complète : le garde-fou quotidien et le rapport de santé lisent ce périmètre) → PASS. Commit `feat(calendrier): toutes les compétitions de clubs de l'Atlas`.

### Task 3: Accrochage PS3838 par une équipe sûre + horaire

**Files:**
- Modify: `backend/app/ingestion/ps3838/anchor.py` (`match_event`)
- Test: `backend/tests/ingestion/ps3838/test_anchor_une_equipe.py`

**Interfaces:**
- Produces: `match_event(fixture, events, alias_map=None) -> Ps3838Event | None` — même signature. Ordre : (1) les deux équipes reconnues (règle actuelle) ; (2) sinon, accrochage par une équipe.

Règle (2), toutes conditions requises :
- exactement UN événement à ±2 h dont l'équipe à domicile (resp. extérieure) est reconnue par `_same_team` pour la même position ;
- cette équipe reconnue ne figure dans AUCUN autre événement à ±12 h (un club ne joue pas deux fois) ;
- l'autre équipe de l'événement passe le test de vraisemblance faible `_plausible(a, b)` : un token de l'un est préfixe d'un token de l'autre sur ≥ 4 lettres (`heart`/`hearts`), OU les initiales des tokens de l'un forment l'autre (`KuPS` ← « Kuopion Palloseura », en ignorant la casse, préfixe de 2 lettres par mot accepté) ;
- mêmes marqueurs de variante des deux côtés (garde-fou existant `_variant_markers`).

- [ ] **Step 1:** Tests : « Heart of Midlothian – AS Monaco » ↔ « Hearts – Monaco » → accroché ; « Sporting Braga – Kuopion Palloseura » ↔ « Braga – KuPS » → accroché ; même cas mais un second événement « Braga – X » à +3 h → None ; « Braga B – KuPS » seul candidat pour « Sporting Braga – Kuopion Palloseura » → None ; autre équipe sans aucune vraisemblance (« Braga – Benfica » pour « Sporting Braga – Kuopion Palloseura ») → None ; non-régression : les tests existants de `anchor.py` passent.
- [ ] **Step 2:** Lancer → FAIL. **Step 3:** Implémenter `_plausible(a: str, b: str) -> bool` et la règle (2) dans `match_event` (la liste complète `events` est déjà passée).
- [ ] **Step 4:** `.venv/bin/python -m pytest -q tests/ingestion/ps3838` → PASS. Commit `fix(ps3838): accrochage par une équipe sûre et l'horaire, noms courts compris`.

### Task 4: Le worker ne calcule que les matchs qui ont des cotes de books

**Files:**
- Modify: `backend/app/services/recommendation_service.py` (`process_scraped_fixtures`, avant `load_match_pricing`)
- Test: `backend/tests/services/test_process_sans_books.py`

**Interfaces:**
- Produces: dans `process_scraped_fixtures`, un match n'ayant NI cote joueur (`player_odds_snapshots`) NI cote 1N2 d'un bookmaker de `FR_H2H_BOOKMAKERS` est sauté avant `load_match_pricing` ; compteur `stats["sans_books"]`.

- [ ] **Step 1:** Tests (session simulée comme `tests/services/test_player_identity.py::_run_process`) : match avec seulement des cotes `ps3838` → `load_match_pricing` jamais appelé, aucune reco, `stats["sans_books"] == 1` ; match avec une cote joueur `unibet` → appelé ; match avec seulement une cote 1N2 `betclic` → appelé.
- [ ] **Step 2:** Lancer → FAIL. **Step 3:** Implémenter avec UNE requête groupée pour tous les matchs du lot (jamais une par match).
- [ ] **Step 4:** `.venv/bin/python -m pytest -q tests/services` → PASS. Commit `perf(recos): pas de calcul pour les matchs sans cote de book`.

### Task 5: Calculateur — 14 jours, compétitions rangées par importance

**Files:**
- Modify: `backend/app/api/_badges.py` (`badge_fields_for_fixtures`) + modèles de réponse match : champs `competition_rank: int | None` (= `LEAGUE_REGISTRY[cid].rank`) et `competition_kind: str | None` (= `.kind`)
- Modify: `backend/app/api/fixtures.py` (`limit` : `le=2000`)
- Modify: `frontend/src/lib/api.ts` (`competition_rank?: number | null`, `competition_kind?: string | null`), `frontend/src/app/dashboard/calculator/page.tsx`, `frontend/src/components/calculator/MatchRail.tsx`
- Test: `backend/tests/api/test_competition_rank.py`

**Interfaces:**
- Produces: `competition_rank` et `competition_kind` sur chaque match renvoyé (None si compétition hors registre).

- [ ] **Step 1:** Test API : un match `competition_api_id=1` → `competition_rank == LEAGUE_REGISTRY[1].rank`, `competition_kind == "domestic_1"` ; `competition_api_id=7` → `competition_kind == "uefa_club"` ; `competition_api_id=None` → les deux None.
- [ ] **Step 2:** FAIL → implémenter → PASS.
- [ ] **Step 3:** Front : chargement `from_date` = aujourd'hui, `to_date` = aujourd'hui + 13 jours, `limit: 2000`. Groupes de `MatchRail` triés par `competition_rank` croissant (sans rang : en fin, puis alphabétique ; « Compétition inconnue » toujours dernière). Bascule « Principales / Toutes » au-dessus de la liste : « Principales » = coupes d'Europe (`kind == uefa_club`, exposé par l'API en `competition_kind`) + championnats de rang ≤ 12 (Big 5, Portugal, Pays-Bas, Turquie, Belgique, Suisse, Écosse, Grèce), affichée par défaut ; la recherche porte toujours sur toutes. Le premier groupe reste ouvert.
- [ ] **Step 4:** `npx tsc --noEmit && npm run build` → OK. Commit `feat(calculateur): 14 jours, compétitions principales en tête`.

### Task 6: Mesure de couverture xG + mise en production

**Files:**
- Modify: `backend/app/scripts/mesure_branchement_atlas.py` (ligne 8 ajoutée)

**Interfaces:**
- Produces: ligne « 8. xG à 7 jours : N/M matchs ; sans xG : X non accrochés (dont Y absents de PS3838), Z accrochés sans cote » + liste par compétition.

- [ ] **Step 1:** Test pur de la fonction de comptage (même style que `tests/scripts/test_mesure_branchement_atlas.py`). Implémenter.
- [ ] **Step 2:** Revue de branche (superpowers:requesting-code-review), corrections, suite complète + build.
- [ ] **Step 3:** PR préremplie → l'utilisateur merge → attendre « Docker Compose Deployed » dans le dernier log Dokploy.
- [ ] **Step 4:** Sur le serveur : sauvegarde base ; `sync_fixtures_from_bzz` (compter les créations) ; `sync_fixture_status_from_bzz` ; `job_resolve_ps3838_anchors` ; passage PS3838 forcé sur tous les matchs accrochés à venir.
- [ ] **Step 5:** `python -m app.scripts.mesure_branchement_atlas` → attendu : mal rangés 0 ; xG à 7 jours = 100 % des matchs proposés par PS3838 ; liste nominative du reste. Rapport à l'utilisateur.
