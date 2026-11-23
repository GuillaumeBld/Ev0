# Branchement de tout le site sur la partie Joueurs — Plan d'implémentation

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Le calculateur, les recommandations et toutes les pages du site lisent équipes, compétitions, effectifs, stats, écussons et drapeaux depuis la source unique de la partie Joueurs (Atlas). Les anciens chemins sont supprimés.

**Architecture:** Un module serveur unique `app/services/atlas_source.py` porte ce que la partie Joueurs sait déjà faire (effectif d'un club, fusion des stats toutes compétitions, écussons/pays). Le moteur de cotes (Alpha et Beta), l'API et le front le consomment. Chaque match porte l'identifiant de compétition de l'Atlas (`competition_api_id`) ; l'étiquette `league` en devient une simple dérivée. Chaque cote bookmaker porte l'identifiant du joueur Atlas.

**Tech Stack:** FastAPI + SQLAlchemy async + Alembic (backend/), Next.js 14 + TypeScript (frontend/), pytest.

**Spec:** décisions validées le 23/11/2026, résumées dans « Contexte » ci-dessous (mémoire `project_ev0_roadmap_pricing`).

## Contexte (mesures du 23/11, 196 matchs à 14 jours)

- Équipes reliées : 100 %. Effectifs calculateur = Atlas (4 621 joueurs).
- 21 % des minutes 2026-27 ignorées : `_load_team_players` ne garde qu'UNE ligne `bzz_player_season_stats` par joueur (la compétition au plus de matchs).
- 70/169 équipes sans écusson (table écrite à la main `frontend/src/lib/teamLogos.ts`).
- 127 matchs à venir mal rangés : ids internes Bzzoiro 8 (Europa League) → `la_liga`, 17 (Saudi Pro League) → `premier_league`, 23 (Superliga RO) → `serie_a`. Code corrigé le 14/11, lignes jamais nettoyées.
- Cotes bookmakers buteur/passeur rattachées au joueur par NOM seulement (`player_odds_snapshots.player_name`).

## Global Constraints

- Source unique : aucun repli sur rapprochement de noms d'équipes, liste d'alias, table d'écussons écrite à la main ou drapeau émoji. Les supprimer, pas les garder en secours.
- Jamais d'échec silencieux : tout élément non relié produit un signalement visible ET une alerte `send_alert(message, channel="incidents")` (`app/alerts.py`).
- Jamais de valeur inventée : stat ou écusson absent = `None` côté API, « — » côté écran.
- Alpha gelé = sa MÉTHODE (rythme de la dernière saison jouée), pas ses données : Alpha et Beta reçoivent les mêmes effectifs et stats ; seule différence, Beta mélange la carrière (`career_blend.py`, inchangé).
- Effectif d'un club = `BzzPlayer.current_team_api_id == id` (Transfermarkt seule autorité, cf. `tests/test_effectifs_source_unique.py`). Jamais `loan_team_api_id`.
- Historique et backtest CONSERVÉS : seules les recos des matchs mal rangés sont supprimées.
- Sauvegarde de la base avant toute écriture en prod (`docker exec ev0-compose-z5hvqt-db-1 pg_dump -U ev0 -Fc ev0 > /root/ev0_avant_branchement_YYYYMMDD.dump`).
- Pas de base de test dans ce projet : tests sur fonctions pures + structure du code ; comportement vérifié sur la base réelle (tâche 12).
- Un seul déploiement à la fin, procédure « un seul à la fois » (pas deux merges à moins de 5 min).
- Textes visibles en français, langage foot/paris.

## Review Focus

1. Un joueur transféré en cours de saison a des lignes de stats sous deux clubs : la fusion doit additionner TOUTES ses compétitions de la saison, quel que soit le club → test dans la tâche 6.
2. Deux joueurs de la même équipe partagent le nom de famille (Hernández, Lucas/Théo) : un nom bookmaker abrégé « T. Hernández » ne doit être relié qu'à UN joueur, sinon à aucun → test dans la tâche 8.
3. Un match d'une compétition hors des 6 étiquettes historiques (Europa League) doit s'afficher avec son vrai nom, pas disparaître ni planter le filtre de championnat du front → test dans la tâche 2 et vérification dans la tâche 11.
4. Le script de réparation relancé deux fois ne doit rien supprimer la seconde fois → test dans la tâche 3.
5. Un match sans `home_bzz_team_id` (création manuelle via `POST /fixtures`) : le calculateur répond « équipe non reliée », pas une liste vide muette → test dans la tâche 5.

---

### Task 1: Migration — identifiants Atlas sur les matchs et les cotes

**Files:**
- Create: `backend/alembic/versions/067_competition_et_joueur_atlas.py`
- Modify: `backend/app/models/fixtures.py` (colonne `competition_api_id`)
- Modify: modèle `PlayerOddsSnapshot` (fichier à localiser par `grep -rn "player_odds_snapshots" backend/app/models`) (colonne `player_api_id`)
- Test: `backend/tests/test_migration_067.py`

**Interfaces:**
- Produces: `Fixture.competition_api_id: Mapped[int | None]` (index) ; `PlayerOddsSnapshot.player_api_id: Mapped[int | None]` (index).

- [ ] **Step 1:** Test structurel `test_migration_067_ajoute_les_deux_colonnes` : le module de migration a `down_revision` = révision de `066_atlas_toutes_competitions.py`, et `upgrade()` ajoute `fixtures.competition_api_id` et `player_odds_snapshots.player_api_id`, toutes deux nullables et indexées (même style que `tests/test_player_tm_id_model.py`).
- [ ] **Step 2:** `cd backend && uv run pytest tests/test_migration_067.py -v` → FAIL (module absent).
- [ ] **Step 3:** Écrire la migration + les deux colonnes des modèles. `downgrade()` retire index et colonnes.
- [ ] **Step 4:** Relancer → PASS.
- [ ] **Step 5:** Commit `feat(schema): identifiants Atlas sur les matchs et les cotes bookmakers`.

### Task 2: La compétition d'un match vient de l'Atlas

**Files:**
- Create: `backend/app/services/competitions.py`
- Modify: `backend/app/ingestion/bzzoiro/sync_fixtures_from_bzz.py` (création ET mise à jour)
- Test: `backend/tests/test_competitions.py`

**Interfaces:**
- Consumes: `LEAGUE_REGISTRY` (`app/ingestion/bzzoiro/league_registry.py`), `TARGET_LEAGUE_INTERNAL_IDS` / `INTERNATIONAL_LEAGUE_INTERNAL_IDS` (`app/ingestion/bzzoiro/constants.py`).
- Produces: `league_key_for(competition_api_id: int) -> str` (étiquette historique pour les 6 championnats + compétitions internationales, sinon `f"comp_{competition_api_id}"`) ; `competition_name(competition_api_id: int | None) -> str | None` (nom `LeagueMeta.name`, None si inconnu).

- [ ] **Step 1:** Tests : `league_key_for(1) == "premier_league"`, `league_key_for(7) == "champions_league"`, `league_key_for(8) == "comp_8"`, `league_key_for(17) == "comp_17"`, `league_key_for(23) == "comp_23"` ; `competition_name(8) == "Europa League"`, `competition_name(17) == "Saudi Pro League"`, `competition_name(999999) is None`. Test structurel : `sync_fixtures_from_bzz` n'utilise plus `_LEAGUE_API_ID_TO_KEY` et affecte `competition_api_id` et `league` dans la branche « match existant » comme dans la branche création.
- [ ] **Step 2:** Lancer → FAIL.
- [ ] **Step 3:** Implémenter `competitions.py`. Dans la synchro : à chaque passage, `fixture.competition_api_id = ev.league_api_id` et `fixture.league = league_key_for(ev.league_api_id)`. Le périmètre de synchro reste celui d'aujourd'hui (6 championnats + internationales) ; l'étape 3 de la roadmap l'élargira.
- [ ] **Step 4:** Lancer → PASS, puis `uv run pytest tests/test_fixture_sync.py -v` → PASS.
- [ ] **Step 5:** Commit `feat(calendrier): la compétition d'un match vient de l'Atlas`.

### Task 3: Réparation des matchs mal rangés

**Files:**
- Create: `backend/app/scripts/repair_fixture_competitions.py`
- Test: `backend/tests/test_repair_fixture_competitions.py`

**Interfaces:**
- Consumes: `league_key_for` (tâche 2).
- Produces: `plan_repair(rows: list[FixtureRow]) -> RepairPlan` (pure) avec `FixtureRow(id, league, competition_api_id, bzz_league_api_id, kickoff_utc)` et `RepairPlan(relabel: dict[int, tuple[int, str]], recos_a_supprimer_fixture_ids: set[int])` ; CLI `python -m app.scripts.repair_fixture_competitions [--apply]` (sans `--apply` = simulation qui affiche les comptes).

- [ ] **Step 1:** Tests : un match `league="premier_league"` avec `bzz_league_api_id=17` → relabel `(17, "comp_17")`, et son id dans `recos_a_supprimer_fixture_ids` si coup d'envoi futur ; même cas mais match passé → relabel, recos CONSERVÉES ; match déjà correct (`competition_api_id=1`, `league="premier_league"`, bzz 1) → rien ; plan appliqué puis recalculé sur les lignes corrigées → plan vide (idempotence).
- [ ] **Step 2:** Lancer → FAIL.
- [ ] **Step 3:** Implémenter. Lecture : `fixtures` joint à `bzz_events` sur `external_id = 'bzz_' || api_id`. Application dans une transaction : supprimer les recos (et uniquement elles) des matchs futurs reclassés, puis mettre à jour `competition_api_id` et `league` de TOUS les matchs Bzzoiro (renseigne aussi la nouvelle colonne pour les matchs déjà justes).
- [ ] **Step 4:** Lancer → PASS.
- [ ] **Step 5:** Commit `fix(calendrier): script de réparation des matchs mal rangés`.

### Task 4: Garde-fou quotidien de justesse des compétitions

**Files:**
- Modify: `backend/app/services/coherence_calendrier.py`
- Modify: `backend/app/worker.py` (bloc « Controle de coherence » vers la ligne 1342)
- Test: `backend/tests/test_coherence_calendrier.py`

**Interfaces:**
- Produces: `competition_mal_rangee(matchs: list[MatchRange]) -> list[Violation]` avec `MatchRange(identifiant: int, league: str, competition_api_id: int | None, competition_source: int | None)` ; règle `"competition_mal_rangee"`.

- [ ] **Step 1:** Tests : `competition_source=17`, `competition_api_id=17`, `league="premier_league"` → 1 violation ; `competition_api_id=None` → 1 violation (« compétition non renseignée ») ; ligne juste → aucune ; `resumer()` cite le nom de compétition source (« Saudi Pro League ») dans le motif.
- [ ] **Step 2:** Lancer → FAIL.
- [ ] **Step 3:** Implémenter ; dans le worker, charger les matchs à venir joints à `bzz_events` et ajouter les violations au même message `send_alert(..., channel="incidents")`.
- [ ] **Step 4:** Lancer → PASS.
- [ ] **Step 5:** Commit `feat(garde-fou): alerte quand un match est rangé dans la mauvaise compétition`.

### Task 5: Module source Atlas + effectifs du calculateur

**Files:**
- Create: `backend/app/services/atlas_source.py`
- Modify: `backend/app/api/players.py` (`_merge_season_stats`, `_team_crest_map` déplacés ici, importés depuis `atlas_source`)
- Modify: `backend/app/pricing/team_xg.py` (`_load_team_players`, suppression de `_TEAM_NAME_ALIASES` et des étapes 1-4 de rapprochement par nom)
- Test: `backend/tests/services/test_atlas_source.py`, `backend/tests/pricing/test_team_xg_topdown.py`

**Interfaces:**
- Produces:
  - `merge_season_stats(rows: list[BzzPlayerSeasonStat], season: str) -> dict[str, Any] | None` (ex-`_merge_season_stats`, comportement identique)
  - `club_squad_filter(team_api_id: int) -> ColumnElement[bool]` = `BzzPlayer.current_team_api_id == team_api_id`
  - `@dataclass TeamBadge(name_fr: str | None, crest_url: str | None, api_football_id: int | None, country_iso: str | None)` ; `async team_badges(db, bzz_team_ids: list[int | None]) -> dict[int, TeamBadge]` (un seul SELECT sur `canonical_teams` ; `country_iso` via `app/data/countries.country_iso`)
  - `_load_team_players(db, team, league=None, bzz_team_id=None)` : sans `bzz_team_id` → `[]` + `logger.error` + `alert_bg("Calculateur : équipe non reliée à l'Atlas — {team}", "incidents")`.
  - `assess_inputs` émet un `InputWarning` « équipe non reliée à l'Atlas » quand la liste est vide.

- [ ] **Step 1:** Tests : `merge_season_stats` sur 2 lignes (championnat 900 min / 3 buts / xG 2.5, LDC 300 min / 1 but / xG 0.7) → `minutes_played == 1200`, `goals == 4`, `xg_per_90 == pytest.approx(3.2 / 1200 * 90)` ; `club_squad_filter(42)` compilé ne mentionne pas `loan_team_api_id` ; structurel : `team_xg` ne contient plus `_TEAM_NAME_ALIASES` ni `contains(func.lower(` ; `_load_team_players(db, "X", bzz_team_id=None)` rend `[]` et appelle `alert_bg` (monkeypatch) sans toucher la base.
- [ ] **Step 2:** Lancer → FAIL.
- [ ] **Step 3:** Implémenter ; `api/players.py` réimporte depuis `atlas_source` (aucun changement d'affichage de la partie Joueurs).
- [ ] **Step 4:** `uv run pytest tests/services tests/pricing tests/api/test_players_api.py tests/api/test_players_crest_url.py -v` → PASS.
- [ ] **Step 5:** Commit `refactor(pricing): effectifs et écussons depuis la source Atlas, plus de rapprochement par nom`.

### Task 6: Stats du calculateur = toutes les compétitions de la saison

**Files:**
- Modify: `backend/app/pricing/team_xg.py` (`_load_team_players`, `_load_national_team_players`, `_load_players_by_api_ids`)
- Modify: `backend/app/api/pricing.py` (`PlayerAllocationOut`)
- Test: `backend/tests/pricing/test_team_xg_topdown.py`

**Interfaces:**
- Consumes: `merge_season_stats` (tâche 5).
- Produces: `_player_dict_from_merged(player: BzzPlayer, merged: dict, season: str) -> dict[str, Any]` (pure ; mêmes clés que le dict actuel + `stats_season: str`) ; `PlayerAllocationOut.stats_season: str | None` et `PlayerAllocationOut.stats_stale: bool` (vrai si `stats_season` < saison courante `season_service`).

- [ ] **Step 1:** Tests : `_player_dict_from_merged` sur la fusion de la tâche 5 → `minutes_played == 1200`, `npxg_per_90 == pytest.approx(3.2/1200*90)`, `stats_season == "2026-2027"` ; joueur transféré (lignes sous deux clubs, même saison) → les deux comptent ; structurel : les trois chargeurs n'utilisent plus `matches_played.desc()` pour choisir UNE ligne.
- [ ] **Step 2:** Lancer → FAIL.
- [ ] **Step 3:** Implémenter : charger toutes les lignes de la dernière saison jouée du joueur, les fusionner, construire le dict. La méthode Alpha (dernière saison jouée) est inchangée ; `beta_pricing.py` recharge le pool par les mêmes fonctions, donc reçoit les mêmes données sans modification.
- [ ] **Step 4:** `uv run pytest tests/pricing tests/test_pricing_integration.py tests/test_pricing_market_xg_integration.py -v` → PASS.
- [ ] **Step 5:** Commit `fix(pricing): stats de toutes les compétitions de la saison, plus une seule`.

### Task 7: Écussons et compétition exposés par l'API

**Files:**
- Modify: `backend/app/api/fixtures.py`, `backend/app/api/pricing.py`, `backend/app/api/recommendations.py`, `backend/app/api/history.py`, `backend/app/api/lineups.py`, `backend/app/api/wc2026_matches.py`, `backend/app/api/wc2026_pricing.py`
- Test: `backend/tests/api/test_team_badges_exposes.py`

**Interfaces:**
- Consumes: `team_badges`, `TeamBadge` (tâche 5), `competition_name` (tâche 2).
- Produces: schéma Pydantic `TeamBadgeOut(name_fr, crest_url, api_football_id, country_iso)` ; champs `home_badge: TeamBadgeOut | None`, `away_badge: TeamBadgeOut | None`, `competition_api_id: int | None`, `competition_name: str | None` sur les réponses match de ces 7 routeurs.

- [ ] **Step 1:** Test structurel : chacun des 7 modèles de réponse a `home_badge`, `away_badge`, `competition_name` ; test unitaire du convertisseur `badge_out(TeamBadge | None) -> TeamBadgeOut | None` (None → None).
- [ ] **Step 2:** Lancer → FAIL.
- [ ] **Step 3:** Implémenter ; un appel `team_badges` par requête (lot), jamais par ligne.
- [ ] **Step 4:** Lancer + `uv run pytest tests/api -v` → PASS.
- [ ] **Step 5:** Commit `feat(api): écussons, pays et compétition Atlas sur toutes les réponses match`.

### Task 8: Cotes bookmakers reliées au joueur Atlas

**Files:**
- Create: `backend/app/services/player_identity.py`
- Modify: `backend/app/ingestion/odds_storage.py` (écriture de `player_api_id`)
- Modify: `backend/app/services/recommendation_service.py` (lignes ~139-160 et ~561-590 : jointure par `player_api_id`)
- Test: `backend/tests/services/test_player_identity.py`

**Interfaces:**
- Consumes: `normalize_selection_name` (`app/ingestion/odds.py`).
- Produces: `@dataclass SquadPlayer(api_id: int, name: str)` ; `resolve_odds_player(bookmaker_name: str, squad: list[SquadPlayer]) -> int | None`. Ordre : nom normalisé exact → nom de famille unique dans l'effectif + prénom compatible (initiale, inclusion) → None. `generate_recommendations` relie cote ↔ allocation par `player_api_id` ; cote non reliée = pas de reco + compteur.

- [ ] **Step 1:** Tests : `"Kylian Mbappé"` exact → id ; `"K. Mbappé"` → id ; effectif {Lucas Hernández, Theo Hernández} + `"T. Hernández"` → Theo ; `"Hernández"` seul → None ; `"Mbappe"` sans accent → id ; nom absent → None.
- [ ] **Step 2:** Lancer → FAIL.
- [ ] **Step 3:** Implémenter ; à l'écriture des cotes, charger l'effectif des deux équipes du match (`club_squad_filter`) une fois par match. Le run de génération envoie une alerte `incidents` quand le taux de cotes non reliées d'un match dépasse 10 %, avec les noms. `recommendations.player_id` est renseigné.
- [ ] **Step 4:** `uv run pytest tests/services tests/test_recommendations_status.py tests/test_betclic_passeur.py -v` → PASS.
- [ ] **Step 5:** Commit `feat(cotes): chaque cote bookmaker reliée à son joueur Atlas`.

### Task 9: Règlement par identifiant joueur

**Files:**
- Modify: `backend/app/ingestion/auto_settle.py`
- Test: `backend/tests/test_auto_settle_par_identifiant.py`

**Interfaces:**
- Consumes: `recommendations.player_id` (tâche 8).
- Produces: règlement : si `player_id` renseigné, l'événement (but/passe) est cherché par identifiant joueur ; sinon chemin actuel par nom (anciennes recos du backtest, inchangées).

- [ ] **Step 1:** Tests : reco avec `player_id` dont le nom diffère de la feuille de match → réglée par l'id ; reco sans `player_id` → même verdict qu'aujourd'hui (rejouer les cas de `tests/test_auto_settle_noms_abreges.py`).
- [ ] **Step 2:** Lancer → FAIL.
- [ ] **Step 3:** Implémenter.
- [ ] **Step 4:** `uv run pytest tests/test_auto_settle*.py -v` → PASS.
- [ ] **Step 5:** Commit `feat(reglement): règlement par identifiant joueur quand il est connu`.

### Task 10: Le reste du serveur raisonne par compétition Atlas

**Files:**
- Modify: les 21 usages de `fixture.league` / `Fixture.league` (`backtest/simulator.py`, `api/sanctuary.py`, `api/history.py`, `api/fixtures.py`, `ingestion/bzzoiro/sync_bzzoiro_lineups.py`, `scripts/backfill_substitutions.py`, `pricing/team_xg.py`, `pricing/beta_pricing.py`) ; `app/notifications.py` (nom de compétition dans les messages Telegram)
- Test: `backend/tests/test_competition_usages.py`

**Interfaces:**
- Consumes: `competition_name`, `league_key_for`.
- Produces: regroupements/filtres par `competition_api_id` ; messages Telegram affichant `competition_name`.

- [ ] **Step 1:** Tests : `_format_movement` (notifications) affiche « Europa League » pour un match `competition_api_id=8` ; structurel : aucun `== "premier_league"`-style codé en dur hors `competitions.py` et `constants.py` (grep dans le test).
- [ ] **Step 2:** Lancer → FAIL.
- [ ] **Step 3:** Implémenter usage par usage ; `INTERNATIONAL_LEAGUES` du pricing comparé sur `competition_api_id`.
- [ ] **Step 4:** `uv run pytest -q` (suite complète) → PASS.
- [ ] **Step 5:** Commit `refactor: le serveur raisonne par compétition Atlas`.

### Task 11: Écussons et drapeaux Atlas sur tout le front

**Files:**
- Create: `frontend/src/components/TeamBadge.tsx`
- Modify: `frontend/src/lib/api.ts` (types `TeamBadgeOut`, champs des tâches 6-7), `frontend/src/app/dashboard/calendrier/page.tsx`, `frontend/src/app/dashboard/calculator/page.tsx` (remplacement minimal : la refonte est l'étape 2), `frontend/src/components/RecommendationCard.tsx`, `frontend/src/app/dashboard/recommendations/page.tsx`, `frontend/src/app/dashboard/history/page.tsx`, `frontend/src/app/dashboard/wc2026/**`, `frontend/src/components/wc2026/PricingTable.tsx`, `frontend/src/components/Dashboard.tsx`, `frontend/src/components/lineups/*`
- Delete: `frontend/src/lib/teamLogos.ts`, `frontend/src/components/FlagImg.tsx`
- Réutilise: `frontend/src/components/atlas/ClubCrest.tsx`

**Interfaces:**
- Consumes: `home_badge` / `away_badge` / `competition_name` (tâche 7), `stats_season` / `stats_stale` (tâche 6).
- Produces: `<TeamBadge badge={TeamBadgeOut | null} size?: number />` : `crest_url` → `api_football_id` (via `ClubCrest`) → drapeau `https://flagcdn.com/w40/{country_iso}.png` pour une sélection → sinon initiales de l'équipe (jamais d'image cassée). Nom de joueur cliquable vers sa fiche Atlas (même paramètre d'URL que l'Atlas utilise pour ouvrir `PlayerDetail` dans `app/dashboard/atlas/page.tsx`). Filtres de championnat construits depuis `competition_name` (une compétition inconnue s'affiche, ne disparaît pas).

- [ ] **Step 1:** `grep -rn "teamLogos\|FlagImg\|getTeamId" frontend/src` → liste des usages à remplacer (base de contrôle).
- [ ] **Step 2:** Créer `TeamBadge` ; remplacer chaque usage ; supprimer les deux fichiers.
- [ ] **Step 3:** Calculateur : badge « stats 2025-26 » discret sur les joueurs `stats_stale`.
- [ ] **Step 4:** `cd frontend && npx tsc --noEmit && npm run build` → OK ; le grep de l'étape 1 ne renvoie plus rien.
- [ ] **Step 5:** Commit `feat(front): écussons et drapeaux de l'Atlas partout, table écrite à la main supprimée`.

### Task 13: Moteur de compo — titulaires par identifiant, Alpha ET Beta, xG vraiment réparti

> Ajoutée le 23/11 sur demande de l'utilisateur (« corrige le moteur de compo maintenant »). Constat en prod (Man City – Napoli, 10 titulaires envoyés par équipe) : Beta renvoie exactement ses cotes « effectif complet » ; côté City les cotes Alpha ne bougent pas du tout (Haaland 2,13 → 2,13) alors que côté Napoli elles bougent (De Bruyne 4,45 → 3,39). Les titulaires sont envoyés par NOM (`home_starters: list[str]`) et rapprochés par `_lineup_name_compatible` — contraire à la source unique.

**Files:**
- Modify: `backend/app/api/pricing.py` (`MatchPriceRequest`), `backend/app/pricing/team_xg.py` (`load_match_pricing`, `compute_lineup_allocation`), `backend/app/pricing/beta_pricing.py`, `backend/app/services/recommendation_service.py` (si les recos utilisent la compo officielle), `frontend/src/components/calculator/LineupPricingWidget.tsx`, `frontend/src/app/dashboard/calculator/page.tsx`, `frontend/src/lib/api.ts` (envoi par identifiants)
- Test: `backend/tests/pricing/test_compo.py`

**Interfaces:**
- Produces: `MatchPriceRequest.home_starter_ids: list[int] | None`, `away_starter_ids: list[int] | None` (identifiants `bzz_players.api_id`) ; les champs `home_starters` / `away_starters` (noms) et `_lineup_name_compatible` sont SUPPRIMÉS. `compute_lineup_allocation(players, starter_ids: set[int], team, team_xg)`. `compute_beta_allocations(..., home_starter_ids=None, away_starter_ids=None)` rend aussi les allocations Beta de la compo ; `PlayerAllocationOut` des listes `*_lineup_players` porte des champs `beta_*` calculés sur la compo.

- [ ] **Step 1:** Diagnostiquer pourquoi la compo City ne change rien (superpowers:systematic-debugging) et consigner la cause dans le rapport.
- [ ] **Step 2:** Tests : (a) 10 titulaires d'un club → somme des λ des titulaires = xG de l'équipe à l'arrondi près, et cotes des titulaires plus courtes que celles de l'effectif complet pour un joueur à minutes partielles ; (b) Beta compo ≠ Beta effectif quand la compo exclut des joueurs ; (c) identifiant inconnu de l'effectif → ignoré et signalé (`InputWarning`), pas d'échec ; (d) structurel : plus aucun rapprochement de nom de titulaire.
- [ ] **Step 3:** Lancer → FAIL. **Step 4:** Implémenter (identifiants depuis `bzz_events.lineups` côté compo officielle ; widget du front envoie les `player_id`). **Step 5:** `.venv/bin/python -m pytest tests/pricing -q` + suite complète → PASS ; `cd frontend && npx tsc --noEmit` → OK.
- [ ] **Step 6:** Commit `fix(compo): titulaires par identifiant Atlas, xG réparti pour Alpha et Beta`.

### Task 14: Retrait de la garantie « remboursé si 0-0 »

> Ajoutée le 23/11 : l'utilisateur indique que la garantie n'existe plus chez les books. `ev_p00_guarantee` n'est qu'informatif (aucune décision de reco ne l'utilise).

**Files:**
- Modify: `backend/app/pricing/goalscorer.py` (supprimer `ev_anytime_p00_guarantee`), `backend/app/pricing/team_xg.py` (champ `p00` de `MatchPricingResult`), `backend/app/api/pricing.py` (`MatchPriceResponse.p00`), `backend/app/services/recommendation_service.py` (clés `ev_p00_guarantee` / `p00` du dict extra), `frontend/src/app/dashboard/calculator/page.tsx` (ligne « P(0-0) … (remboursé si 0-0) »), `frontend/src/lib/api.ts` (`p00`)
- Test: tests existants qui référencent `p00` / `ev_anytime_p00_guarantee` (grep) mis à jour.

- [ ] **Step 1:** Test structurel : `grep -rn "p00\|rembours" backend/app frontend/src` ne renvoie plus rien (hors commentaires d'historique des migrations). **Step 2:** FAIL. **Step 3:** Supprimer. Les recos déjà en base gardent leurs anciennes clés (pas de migration de données). **Step 4:** suite complète + `npx tsc --noEmit` → PASS. **Step 5:** Commit `chore: retrait de la garantie remboursé si 0-0, disparue chez les books`.

### Task 12: Mesure de fin + mise en production

**Files:**
- Create: `backend/app/scripts/mesure_branchement_atlas.py`

**Interfaces:**
- Produces: CLI qui affiche, sur les matchs des 14 prochains jours, les mêmes 6 mesures que le jalon 0 (équipes reliées, écart d'effectif, % minutes ignorées, saisons périmées, écussons manquants, matchs mal rangés) + % de cotes bookmakers reliées.

- [ ] **Step 1:** Écrire le script (requêtes SQL du jalon 0, reprises dans le contexte ci-dessus).
- [ ] **Step 2:** Revue finale de branche (superpowers:requesting-code-review), PR, merge, attendre le déploiement automatique Dokploy (un seul).
- [ ] **Step 3:** Sauvegarde base (Global Constraints) ; `docker exec ev0-compose-z5hvqt-worker-1 python -m app.scripts.repair_fixture_competitions` (simulation : attendu ≈ 127 reclassés à venir) puis `--apply`.
- [ ] **Step 4:** `python -m app.scripts.mesure_branchement_atlas` → attendu : mal rangés 0, minutes ignorées 0 %, écussons manquants ≤ 10 (ceux que l'Atlas n'a pas), cotes reliées ≥ 95 %.
- [ ] **Step 5:** Trois matchs témoins (un Big 5, un LDC, un international) : `POST /api/v1/price/match` avant/après comparé joueur par joueur ; écarts expliqués par les compétitions désormais comptées. Rapport à l'utilisateur.
