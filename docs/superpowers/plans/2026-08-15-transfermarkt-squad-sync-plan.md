# Sync effectifs Transfermarkt — plan d'implémentation

> **Pour les workers agentiques :** SUB-SKILL requise — `superpowers:subagent-driven-development` (recommandé) ou `superpowers:executing-plans`, tâche par tâche. Les étapes utilisent des cases `- [ ]`.

**Goal :** Réconcilier quotidiennement (mercato) / hebdomadairement (saison) les effectifs `bzz_players` avec Transfermarkt, avec un garde-fou anti-mort-silencieuse strict.

**Architecture :** Un scraper TM par club (client HTTP existant) → matching nom+DOB → réconciliation `current_team`/prêts avec règle de départ prudente → job APScheduler. Sur échec de scraping/parsing : aucune écriture, + issue GitHub + PR auto (fixture HTML + test de régression rouge).

**Tech Stack :** Python 3.11, SQLAlchemy async, Alembic, httpx, APScheduler, `gh` CLI. Spec : `docs/superpowers/specs/2026-08-15-transfermarkt-squad-sync-design.md`.

## Global Constraints

- **Aucune écriture sur donnée d'effectif si le club (ou le run) est invalidé** par le garde-fou. Dernière donnée valide conservée.
- **Aucun échec silencieux** : tout run FAILED/partial ⇒ log ERROR + ligne `squad_sync_runs` + issue GitHub + PR auto.
- Matching joueur uniquement si **nom normalisé concordant ET date de naissance concordante**. Jamais de rattachement sur le nom seul.
- Portée : clubs des ligues couvertes (PL, Ligue 1, Bundesliga, La Liga, Serie A, UCL + compét's pricées).
- Rate-limit TM ≥ 2 s/requête, UA navigateur, retry/backoff (réutiliser `TransfermarktClient`).
- Départ prudent : détacher un joueur du club X seulement si TM le place ailleurs, OU absent de X sur **2 runs consécutifs**.

---

### Task 1 : Migration base de données

**Files :**
- Create : `backend/alembic/versions/049_transfermarkt_squad_sync.py`
- Modify : `backend/app/models/canonical_teams.py` (+ `transfermarkt_club_id`)
- Create : `backend/app/models/squad_sync.py` (`SquadSyncRun`)
- Modify : `backend/app/models/bzzoiro.py` (`BzzPlayer.tm_absent_streak INT DEFAULT 0`)
- Test : `backend/tests/test_squad_sync_migration.py`

**Interfaces produites :**
- `canonical_teams.transfermarkt_club_id INT NULL UNIQUE`.
- `squad_sync_runs(id, started_at, finished_at, mode, clubs_total, clubs_ok, clubs_failed, players_updated, players_detached, status, detail JSONB)`.
- `bzz_players.tm_absent_streak INT NOT NULL DEFAULT 0` (compteur règle 2 runs, par club courant).

**Étapes :**
- [ ] Écrire le test : après upgrade, les colonnes/table existent (introspection).
- [ ] Écrire la migration 049 (upgrade/downgrade complets, index unique sur `transfermarkt_club_id`).
- [ ] Ajouter les colonnes aux modèles ORM.
- [ ] `alembic upgrade head` en test → vert. Commit.

---

### Task 2 : Résolution + seeding des IDs club Transfermarkt

**Files :**
- Create : `backend/app/ingestion/transfermarkt/resolve_clubs.py`
- Test : `backend/tests/test_tm_resolve_clubs.py`

**Interfaces :**
- Consomme : `TransfermarktClient` de `app/scripts/transfermarkt_career.py` (UA, rate-limit, retry).
- Produit : `async def resolve_and_store_club_ids(session) -> ResolveReport` — remplit `canonical_teams.transfermarkt_club_id` pour les clubs des ligues couvertes ; renvoie `(resolved, unresolved[list[name]])`.

**Méthode :** pour chaque ligue couverte, charger la page compétition TM (`/wettbewerb/.../wettbewerb/<CODE>`) → liste `(nom_club, tm_club_id)` ; matcher au `canonical_teams` par nom normalisé (foldAccents + alias). Codes de compétition TM en constantes (`GB1`=PL, `FR1`=Ligue 1, `L1`=Bundesliga, `ES1`=La Liga, `IT1`=Serie A, `CL`=UCL).

**Étapes :**
- [ ] Test : un fixture HTML de page compétition → extraction `(club, id)` correcte ; matching sur un `canonical_teams` mocké.
- [ ] Implémenter l'extraction + matching normalisé (réutiliser un helper `fold_accents` Python partagé — cf. Task 4).
- [ ] Les non-résolus sont **retournés** (jamais devinés) pour surfaçage.
- [ ] Test vert. Commit.

---

### Task 3 : Scraper effectif d'un club (+ validations)

**Files :**
- Create : `backend/app/ingestion/transfermarkt/squad_scraper.py`
- Test : `backend/tests/test_tm_squad_scraper.py` (avec fixture HTML réel `kader`)

**Interfaces :**
- Produit :
  - `@dataclass TMPlayer(name: str, dob: date | None, position: str | None, tm_player_id: int)`
  - `@dataclass SquadResult(club_id: int, players: list[TMPlayer], status: Literal["ok","empty","structure_error"], raw_html: str)`
  - `async def fetch_club_squad(client, tm_club_id: int) -> SquadResult`

**Parsing :** page `kader/verein/{id}` ; joueurs via `td.hauptlink a[href*="/profil/spieler/"]` (nom) ; DOB + poste depuis les colonnes de la ligne. **Validations** : HTTP 200 ; `hauptlink` non vide ; `MIN_SQUAD = 15` (sinon `status="empty"`) ; sentinelle structure : 0 `hauptlink` sur une page 200 non vide ⇒ `status="structure_error"`.

**Étapes :**
- [ ] Sauver un fixture HTML réel (`tests/fixtures/tm_kader_psg.html`).
- [ ] Test : parse → ≥ 20 joueurs, DOB/poste d'un joueur connu corrects, statut `ok`.
- [ ] Test : fixture tronqué/vide → `structure_error`/`empty` (pas d'exception).
- [ ] Implémenter. Vert. Commit.

---

### Task 4 : Matching joueur TM ↔ bzz_players (nom + DOB)

**Files :**
- Create : `backend/app/ingestion/transfermarkt/player_match.py`
- Test : `backend/tests/test_tm_player_match.py`

**Interfaces :**
- `def fold_accents(s: str) -> str` (NFD + suppression diacritiques + lower ; miroir Python du helper front).
- `async def match_players(session, club_bzz_id, tm_players: list[TMPlayer]) -> MatchReport` où `MatchReport(matched: dict[tm_player_id, bzz_api_id], unmatched: list[TMPlayer])`.

**Règle :** candidat = `bzz_players` dont `fold_accents(name)` ∈ variantes du nom TM ; **retenu seulement si `date_of_birth` == DOB TM**. Homonyme sans DOB concordante ⇒ non matché (surfacé). Joueur TM sans DOB ⇒ match nom-exact-unique seulement, sinon unmatched.

**Étapes :**
- [ ] Tests : concordant (nom+DOB) → matché ; homonyme DOB ≠ → rejeté ; accent (« Dembélé » vs « Dembele ») → matché ; TM sans DOB + nom unique → matché.
- [ ] Implémenter. Vert. Commit.

---

### Task 5 : Réconciliation d'effectif

**Files :**
- Create : `backend/app/ingestion/transfermarkt/sync_squads.py`
- Test : `backend/tests/test_sync_squads.py`

**Interfaces :**
- Consomme : Task 2/3/4.
- Produit : `async def sync_squads(session, client, clubs: list[CanonicalTeam]) -> SquadSyncRun` (persiste la ligne de run).

**Logique par club (uniquement si `SquadResult.status == "ok"`) :**
- Matchés → `current_team_api_id = club.bzz_team_id` ; `loan_team_* = NULL` si prêt non reflété ; `tm_absent_streak = 0`.
- **Départ prudent** : un `bzz_players` rattaché à ce club mais absent des matchés :
  - si TM (autre club du même run) le réassigne → réassigné directement ;
  - sinon `tm_absent_streak += 1` ; si `>= 2` → détaché (current_team ← NULL) ; sinon inchangé.
- Club `status != "ok"` ⇒ **aucune écriture** pour ce club ; incrémente `clubs_failed`.
- Sentinelle globale : si `clubs_failed / clubs_total > 0.30` ⇒ run `status="failed"`, **rollback** de toutes les écritures d'effectif.

**Étapes :**
- [ ] Tests (DB en mémoire/fixtures) : recrue ajoutée ; prêt périmé nettoyé ; départ 1 run → non détaché ; départ 2 runs → détaché ; réassignation si TM ailleurs ; club KO → 0 écriture ; sentinelle > 30 % → rollback total.
- [ ] Implémenter. Vert. Commit.

---

### Task 6 : Garde-fou anti-mort-silencieuse (surfaçage)

**Files :**
- Create : `backend/app/ingestion/transfermarkt/failure_surface.py`
- Test : `backend/tests/test_failure_surface.py`

**Interfaces :**
- `async def surface_failure(run: SquadSyncRun, samples: dict[club→raw_html]) -> None`.

**Comportement (sur `status in {failed, partial}`) :**
1. Log `ERROR` détaillé + run déjà persisté.
2. **Issue GitHub** via `gh issue create` (titre `[squad-sync] échec ({clubs_failed} clubs) — {date}`, corps = clubs, erreurs, extrait). Idempotence : ne pas recréer si une issue ouverte du même jour/type existe (`gh issue list --search`).
3. **PR auto** : branche `squad-sync/parse-failure-{date}` ; commits = fixture HTML capturé (`tests/fixtures/tm_failure_{date}.html`) + test `tests/test_parse_regression_{date}.py` qui lance le parseur sur ce fixture et **assert un effectif plausible** (donc rouge tant que non corrigé) ; `gh pr create`. Idempotent par jour.
- Échec de `gh` lui-même ⇒ log CRITICAL (jamais silencieux), n'interrompt pas le reste.

**Étapes :**
- [ ] Tests : `gh` mocké → issue + PR + fixture + test générés ; idempotence (2e appel même jour → pas de doublon) ; échec `gh` → CRITICAL, pas d'exception propagée.
- [ ] Implémenter. Vert. Commit.

---

### Task 7 : Job planifié (cadence mercato/saison)

**Files :**
- Modify : `backend/app/worker.py` (nouveau `job_sync_squads` + enregistrement)
- Create : `backend/app/ingestion/transfermarkt/schedule.py` (`should_run_today(today) -> RunMode|None`)
- Test : `backend/tests/test_squad_schedule.py`

**Interfaces :**
- `MERCATO_WINDOWS` (constantes : été 10/06→02/09, hiver 01/01→31/01).
- `def should_run_today(today: date, last_weekly: date|None) -> Literal["daily","weekly",None]` : `daily` en fenêtre mercato ; sinon `weekly` si ≥ 7 j depuis le dernier run hebdo, sinon `None`.
- `async def job_sync_squads()` : résout clubs couverts (avec `transfermarkt_club_id`), appelle `sync_squads`, puis `surface_failure` si besoin.

**Enregistrement :** `scheduler.add_job(job_sync_squads, CronTrigger(hour=4, minute=30), id="sync_squads", max_instances=1, coalesce=True)`. Le job décide en interne (mercato/saison) via `should_run_today` — no-op si `None`.

**Étapes :**
- [ ] Tests : date en fenêtre été → `daily` ; hors fenêtre, 8 j depuis hebdo → `weekly` ; hors fenêtre, 3 j → `None`.
- [ ] Implémenter + enregistrer le job. Vert. Commit.

---

### Task 8 : Câblage seeding + première réconciliation (opérationnel)

**Files :**
- Create : `backend/app/scripts/run_squad_sync_once.py` (CLI one-shot : seed IDs club puis 1 run complet)
- Test : couvert par Tasks 2/5 (script = orchestration mince)

**Étapes :**
- [ ] Script : `resolve_and_store_club_ids` → `sync_squads` sur tous les clubs couverts → imprime le rapport (résolus/non, recrues, départs, prêts nettoyés).
- [ ] Exécuter sur la prod après déploiement (docker exec), vérifier PSG/Villa alignés sur Transfermarkt. Commit.

---

## Auto-revue

- Couverture spec : ancrage club (T1/T2), scraper (T3), matching (T4), réconciliation + départ prudent + prêts (T5), garde-fou issue+PR (T6), cadence (T7), mise en service (T8). ✔
- `fold_accents` : défini une fois (T4), réutilisé (T2). Cohérent avec le front (`foldAccents`).
- Pas d'écriture sur échec : garanti par le statut club (`ok`) et la sentinelle globale (rollback). ✔
