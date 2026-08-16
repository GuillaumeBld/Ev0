# Sync effectifs Transfermarkt — design

**Date :** 2026-08-15
**Statut :** validé (design), à planifier

## Problème

Les effectifs (`bzz_players.current_team_api_id` / `loan_team_api_id`) sont
construits **uniquement** à partir de bzzoiro, qui n'est pas fiable pour le
suivi mercato en temps réel. Conséquence mesurée le 15/08/2026 (vs
Transfermarkt) :

- PSG : 26 joueurs réels (TM) vs **33** en base ; Aston Villa : 22 vs **45**.
- Recrues absentes de notre base : Akliouche (→PSG), Garnacho & João Gomes
  (→Villa) — pourtant cotées par les bookmakers.
- Partants encore présents : Gonçalo Ramos, Kang-in Lee, Douglas Luiz,
  Tielemans…
- `sync_players` ne met **jamais** à jour `loan_team` → 1246 joueurs traînent
  un club de prêt potentiellement périmé (et l'effectif priorise ce club via
  `COALESCE(loan_team_api_id, current_team_api_id)`).
- Des joueuses féminines polluent l'effectif masculin.

Impact pricing : une recrue est pricée à son ancien club (ou absente), un
partant occupe une part d'xG indue → buteur/passeur faussés.

## Objectif

Un système durable qui **réconcilie les effectifs avec Transfermarkt** (la
référence transferts), planifié, avec un garde-fou **anti-mort-silencieuse**
strict (aucun échec silencieux, aucun écrasement de donnée sur échec).

## Portée

Clubs des **ligues couvertes** : Ligue 1, Premier League, Bundesliga, La Liga,
Serie A, Ligue des Champions (+ toute compétition qu'on price). ~clubs
rattachés à `canonical_teams`.

## Cadence

Un job APScheduler unique tournant **tous les jours à heure fixe** ; il décide
en interne du périmètre selon la date :
- **Fenêtre mercato** (été ≈ 10 juin → 2 sept, hiver ≈ 1 → 31 janvier) : run
  **complet quotidien**.
- **Hors mercato** : run complet **une fois/semaine** (autres jours : no-op).

Fenêtres définies par constantes configurables.

## Architecture (5 briques)

### 1. Ancrage club
- Migration : ajouter `transfermarkt_club_id INT` (+ index unique) à
  `canonical_teams`.
- Remplissage **automatique** une fois : résolution via les pages compétition
  Transfermarkt de nos ligues (liste des clubs → id TM), matching par nom
  normalisé sur `canonical_teams.name_en/name_fr/aliases`. Les non-résolus sont
  loggués et remontés (garde-fou), jamais devinés.

### 2. Scraper effectif (`transfermarkt_squad.py`)
- Réutilise le client TM de `transfermarkt_career.py` (UA navigateur,
  rate-limit ≥ 2 s, retry/backoff 429/5xx).
- Par club : page `kader/verein/{tm_id}` (effectif courant). Extraction via le
  sélecteur `td.hauptlink a[href*="/profil/spieler/"]` → **nom** ; date de
  naissance + poste depuis les colonnes de la ligne (ou le profil si absent).
- Retourne une liste `(nom, date_naissance, poste, tm_player_id)`.

### 3. Réconciliation (`sync_squads.py`)
- Pour chaque club (résolu TM ↔ bzz_team) :
  - Matcher chaque joueur TM à `bzz_players` par **nom normalisé + date de
    naissance** (résolveur de `transfermarkt_career.py` réutilisé ; pas de faux
    positif sans DOB concordante).
  - Joueur matché → `current_team_api_id = bzz_team`, `loan_team_* = NULL` si le
    prêt n'est plus reflété par TM.
  - **Départs (prudent)** : un joueur rattaché chez nous au club X mais absent de
    l'effectif TM de X n'est détaché que si (a) TM le place dans un autre club
    (réassignation directe), OU (b) il est absent de X sur **2 runs
    consécutifs** (suivi via `squad_sync_runs` / un champ d'absence). Évite un
    faux départ sur glitch de scraping.
  - Foot féminin exclu de fait (page équipe masculine).
- Écritures uniquement si le run du club est **validé** (voir garde-fou).

### 4. Traçabilité (`squad_sync_runs`)
- Table : `id, started_at, finished_at, mode (daily/weekly), clubs_total,
  clubs_ok, clubs_failed, players_updated, players_detached, status
  (ok/partial/failed), detail JSONB`.
- Suivi par club des absences consécutives (pour la règle des 2 runs).

### 5. Garde-fou anti-mort-silencieuse
Principe : **rien ne casse en silence, rien de bon n'est écrasé par du vide.**

- **Validations par club** : HTTP 200, structure attendue (le sélecteur rend un
  compte plausible), **≥ N joueurs** (seuil par type de club, ex. ≥ 15 pour un
  club de l'élite). Un club hors bornes = club **KO** → **aucune écriture** pour
  ce club (dernière donnée conservée).
- **Sentinelle de structure globale** : si > X % des clubs rendent 0/aberrant
  (⇒ TM a changé son HTML), le run entier est **FAILED** → aucune écriture.
- **Surfaçage bruyant obligatoire** sur run FAILED/partial :
  1. Log `ERROR` + run marqué `failed`/`partial` dans `squad_sync_runs`.
  2. **Issue GitHub auto** (via `gh`/API) : titre
     `[squad-sync] échec parsing (N clubs) — {date}`, corps = clubs touchés,
     erreur, extrait HTML capturé. Idempotente (une issue par type d'échec/jour).
  3. **PR auto** sur branche `squad-sync/parse-failure-{date}` contenant :
     - le **fixture HTML capturé** de la page cassée,
     - un **test de régression qui échoue** (`test_parse_regression_{date}`)
       lançant le parseur sur ce fixture et asserant un effectif plausible.
     → le correctif = rendre le test vert ; la PR est le point d'atterrissage
     prêt à corriger. (Le PAT ouvre la PR ; Yohan merge.)
- **Gel de la donnée** : tant qu'un run n'est pas sain, aucune réassignation
  destructive ; on conserve l'état précédent.

## Modèle de données

- `canonical_teams.transfermarkt_club_id INT NULL UNIQUE` (migration).
- `bzz_players` : réutilise `current_team_api_id`, `loan_team_api_id`. Ajout
  possible `tm_absent_streak INT DEFAULT 0` (compteur pour la règle des 2 runs)
  — à trancher en implémentation (peut vivre dans `squad_sync_runs.detail`).
- `squad_sync_runs` (nouvelle table).

## Tests

- Résolution club (nom → tm_id) sur un échantillon connu.
- Parsing d'un fixture kader réel (compte de joueurs, DOB, poste).
- Matching joueur nom+DOB : cas concordant / homonyme DOB différente (rejet).
- Réconciliation : recrue ajoutée, prêt périmé nettoyé, départ prudent (1 run
  ≠ détaché, 2 runs = détaché ; réassigné si TM ailleurs).
- Garde-fou : club sous seuil → aucune écriture ; sentinelle globale → run
  FAILED ; sur FAILED, issue + PR + test de régression générés (mockés en test).

## Hors périmètre (pour l'instant)

- Nouveaux joueurs 100 % inconnus de bzz_players (sans stats) : créés a minima
  (nom+DOB+club) mais sans historique — le pricing les prendra quand ils auront
  des stats. À affiner si besoin.
- Réconciliation des noms d'équipe eux-mêmes (géré ailleurs).
