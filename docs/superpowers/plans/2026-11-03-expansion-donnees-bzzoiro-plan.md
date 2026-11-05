# Expansion données Bzzoiro — plan d'implémentation

> **Sub-skill :** `superpowers:subagent-driven-development`. Étapes en cases `- [ ]`.

**Goal :** Étendre le référentiel équipes + les stats joueurs des 5 grands
championnats (PR #47) à ~45 compétitions (tous championnats européens + compét's
UEFA clubs + grands championnats mondiaux + sélections), avec rangement pays +
championnat domestique et garantie « zéro joueur sans stats ».

**Architecture :** réutilise l'appareil PR #47 (`rebuild_team_registry.py` :
`EFFECTIFS_REGLEMENTAIRES`, `enumerer_engages` en **espace A**, garde-fou
abort-on-écart) ; on l'étend via un **registre unique** des compétitions, on
ajoute pays + table `leagues` + contrôle de complétude. Spec :
`docs/superpowers/specs/2026-10-21-expansion-donnees-bzzoiro-design.md`.

## Global Constraints
- **Identifiant espace A partout** (events/players/stats) — jamais l'espace B hérité.
- **Segmentation = garde-fou dur** : pour une ligue `round_robin`, l'énumération doit
  rendre exactement `expected_count` ; tout écart → abort de cette ligue, **rien commis**,
  ligue + compte nommés.
- **Championnat d'un club = sa ligue DOMESTIQUE** ; les compét's UEFA ne fixent jamais
  `league_api_id`. Club qualif-UEFA sans domestique couvert → signalé, **pas de pays inventé**.
- **Zéro ajout à moitié** : un club dont l'effectif/les stats sont incomplets est
  **signalé** (`data_coverage_runs`), jamais affiché partiellement. Aucun échec silencieux.
- Décisions verrouillées : `canonical_teams.country` dénormalisée ; table `leagues` créée ;
  clubs qualif-UEFA gardés + signalés.

---

### Task 1 — Registre des compétitions + migration
**Files :** Create `backend/app/ingestion/bzzoiro/league_registry.py` ; Create migration
`alembic/versions/0NN_expansion_leagues.py` ; Modify `app/models/canonical_teams.py` ;
Create `app/models/leagues.py`, `app/models/data_coverage.py` ; Test `tests/test_league_registry.py`.
- `LEAGUE_REGISTRY: dict[int, LeagueMeta]` — les ~45 ids validés, chacun
  `{name, country, tier(1..4|selection), kind(domestic_1|domestic_2|uefa_club|world|selection),
  format(round_robin|groups|knockout), expected_count|None, rank}`.
- Migration : `canonical_teams.country VARCHAR(100) NULL` ; table `leagues` (id interne PK,
  name, country, tier, kind, format, expected_count, rank) ; table `data_coverage_runs`
  (id, started_at, finished_at, league_api_id, clubs_total, clubs_ok, clubs_incomplets,
  joueurs_sans_stats, status, detail JSONB). `canonical_teams.league_api_id`/`season` existent déjà.
- Seed `leagues` depuis `LEAGUE_REGISTRY`.
- [ ] Test : registre bien typé, les 45 ids présents, `expected_count` non nul ssi `round_robin`.
- [ ] Migration up/down ; modèles ; seed. Vert. Commit.

### Task 2 — Généraliser la reconstruction du référentiel
**Files :** Modify `backend/app/scripts/rebuild_team_registry.py` ; Test `tests/test_rebuild_registry_expansion.py`.
- Remplacer `EFFECTIFS_REGLEMENTAIRES` (big-5 en dur) par une dérivation du registre :
  les ligues `kind in (domestic_1, domestic_2)` et `format == round_robin` → `{id: expected_count}`.
- Dans `rebuild`, écrire aussi **`country`** (depuis `bzz_leagues.country` de la ligue, ou le
  champ `country` du registre) en plus de `league_api_id` + `season`.
- Ligues UEFA / sélections / `groups|knockout` : **pas** dans la boucle de segmentation stricte
  (elles ne fixent pas l'engagement championnat) — traitées en Task 4.
- Garde-fou abort-on-écart + remplacement en bloc : inchangés.
- [ ] Tests : ligue round_robin au bon compte → engagée ; écart → abort sans écriture ;
  `country` posé ; club en double championnat → abort.
- [ ] Implémenter. Vert. Commit.

### Task 3 — Ingestion events + stats joueurs sur tout le périmètre
**Files :** Modify `app/ingestion/bzzoiro/sync_events.py` (liste des ligues = registre) ;
Modify `app/scripts/backfill_player_stats.py` ; Modify `sync_players` ; Test `tests/test_expansion_ingestion.py`.
- `sync_events` + sync player-stats (`/api/player-stats/?event=`) + agrégation season stats +
  joueurs (`/api/players/?team=`, espace A) → étendus à **toutes** les ligues du registre
  (hors sélections historiques, cf. Task 6).
- [ ] Tests (fixtures/SQLite réel) : pour une ligue donnée, events→match-stats→season-stats
  chaînés ; joueurs rattachés en espace A.
- [ ] Implémenter. Vert. Commit.

### Task 4 — Rattachement UEFA / qualif + pays
**Files :** Modify `rebuild_team_registry.py` (ou module dédié `attach_european.py`) ;
Test `tests/test_attach_european.py`.
- Les clubs vus en UEFA (7/8/83) conservent leur `league_api_id` **domestique** (posé en Task 2).
- Club présent **uniquement** via qualif UEFA (domestique hors périmètre) → `league_api_id=NULL`,
  `country=NULL`, consigné « sans championnat » (jamais de pays deviné).
- `country` dénormalisé rempli pour tous les clubs à `league_api_id` non nul.
- [ ] Tests : club C1 déjà en Serie A garde league=4 ; club qualif-only → NULL + signalé.
- [ ] Implémenter. Vert. Commit.

### Task 4b — Écussons corrects pour tous les clubs (api_football_id)
**Files :** Create `app/ingestion/apifootball/resolve_crests.py` ; Modify
`frontend/src/lib/teamLogos.ts` + `frontend/src/app/dashboard/players/page.tsx` ;
Test `tests/test_resolve_crests.py`.
- **Racine** : les écussons viennent du front qui mappe **nom → id API-Football** en dur
  (big-5/UCL seulement) → écussons faux (noms erronés) et **absents** pour tous les nouveaux
  clubs. Bzzoiro ne fournit pas d'écusson.
- **Fix** : peupler `canonical_teams.api_football_id` pour **chaque club engagé** en matchant
  sur API-Football par **nom + pays (+ ligue)** (clé `API_FOOTBALL_KEY` déjà dans l'env worker).
  Le front lit l'écusson depuis `api_football_id` de la base (URL `media.api-sports.io/.../{id}.png`),
  **plus** par le dictionnaire de noms.
- **Garde-fou** : club non résolu de façon univoque → `api_football_id=NULL` + écusson
  placeholder + **signalé** (jamais de faux écusson). Revue des écussons big-5/UCL actuels
  (correction des mauvais au passage).
- [ ] Tests : match nom+pays univoque → id posé ; ambiguïté → NULL+signalé ; le front tombe
  sur le placeholder si NULL.
- [ ] Implémenter. Vert. Commit.

### Task 5 — Contrôle de complétude (garde-fou « zéro joueur à moitié »)
**Files :** Create `app/ingestion/bzzoiro/coverage_check.py` ; Test `tests/test_coverage_check.py`.
- `async def check_coverage(session) -> CoverageReport` : par club engagé → events saison
  synchronisés ? chaque match fini a des player-stats ? chaque joueur de l'effectif a une ligne
  de stats (0 min admis ; **absence de ligne = gap**) ?
- Gaps → ligne `data_coverage_runs` (clubs_incomplets, joueurs_sans_stats, detail) + log + alerte.
  Jamais silencieux ; un club incomplet est **signalé**, pas affiché à moitié.
- [ ] Tests : club complet → OK ; club avec match sans stats → incomplet signalé ; joueur sans
  ligne → compté. Jamais d'exception propagée.
- [ ] Implémenter. Vert. Commit.

### Task 6 — Job planifié + backfill historique + orchestration
**Files :** Modify `app/worker.py` (job étendu au registre) ; Create
`app/scripts/build_full_referentiel.py` (one-shot) ; Test `tests/test_expansion_schedule.py`.
- Sync quotidien : events+stats+rebuild+coverage sur les ligues **en cours** du registre.
- Tournois historiques (Euro 2024=66, Copa America 2024=67) : **backfill one-shot** (pas de récurrent).
- `build_full_referentiel.py` : orchestre registre→events/stats→rebuild→attach→coverage, imprime
  le rapport (par ligue : engagés vs attendu, clubs/joueurs incomplets).
- [ ] Tests : sélection des ligues en cours vs historiques ; orchestration mockée.
- [ ] Implémenter + enregistrer le job. Vert. Commit.

### Task 7 — Mise en service + vérification prod
**Files :** —
- [ ] Déployer (migration au boot) ; lancer `build_full_referentiel.py` sur la prod.
- [ ] Vérifier : **segmentation exacte par ligue** (chaque championnat = son compte), `country`
  rempli, rapport `data_coverage_runs` sans club incomplet (ou liste des gaps à traiter).

## Auto-revue
- Couverture spec : registre (T1), segmentation étendue (T2), ingestion stats (T3), pays+UEFA (T4),
  complétude (T5), cadence+backfill (T6), mise en service (T7). ✔
- Garde-fous : segmentation (T2) + complétude (T5) = aucun affichage partiel, aucun échec silencieux.
