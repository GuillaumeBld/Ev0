# Expansion des données Bzzoiro (championnats + joueurs) — spec

**Date :** 2026-10-21
**Statut :** à valider

## Objectif

Le référentiel équipes (PR #47) couvre les 5 grands championnats : segmentation
stricte 20/20/20/18/18, identifiant **espace A**, `canonical_teams.league_api_id`
+ `season`. On l'**étend à ~45 compétitions** validées pour alimenter la section
« Joueurs » : chaque club rangé dans son **pays** et son **championnat domestique**,
chaque joueur avec ses **stats** — zéro ajout à moitié, zéro rangement faux. C'est
la base de données du futur UI.

## Périmètre retenu (validé)

- **Tier 1 — 20 championnats européens 1re div** : PL(1), La Liga(3), Serie A(4),
  Bundesliga(5), Ligue 1(6), Liga Portugal(2), Eredivisie(10), Pro League BEL(14),
  Süper Lig(11), Scottish Prem(13), Super League SUI(15), Super League GRE(24),
  Ekstraklasa(25), Superliga ROU(23), Eliteserien(54), Allsvenskan(26),
  Superliga DAN(84), Czech First League(99), Parva Liga BUL(22), Veikkausliiga(55).
- **Tier 2 — compétitions UEFA clubs** : Champions League(7), Europa League(8),
  Conference League(83), UEFA Super Cup(90).
- **Tier 3 — 2es divisions européennes** : Championship(12), Segunda(38),
  Serie B(100), 2.Bundesliga(94), Ligue 2(89), Liga Portugal 2(88),
  Serie C A/B/C(101/102/103).
- **Tier 4 — mondial** : MLS(18), Saudi(17), Brésil Serie A(9), Liga Profesional
  ARG(85), Liga MX Apertura+Clausura(19/20), J1(49), Copa Libertadores(32),
  Copa Sudamericana(33), Chinese Super League(52), Primera A COL(80), Botola(53),
  Ligue 1 TUN(47), K League 1(50), A-League(104).
- **Sélections** : Coupe du monde 2026(27), Qualifs CM (UEFA 58, CONMEBOL 59,
  CONCACAF 62, OFC 63), Nations League (UEFA 64, CONCACAF 65), Euro 2024(66),
  Copa America 2024(67).

Réserves connues : **K League 1** sans minutes ; **Euro 2024 / Copa America 2024**
= tournois passés (données historiques, pas de saison en cours).

## Ce qui existe déjà (PR #47) — réutilisé, pas refait

- `canonical_teams.league_api_id` + `season` : l'engagement championnat du club
  (identifiant **espace A**). Le championnat est une **donnée**, pas une déduction.
- Reconstruction par ligue : `GET /api/events/?league=<id>` → `home/away_team_obj`
  distincts = engagés officiels (espace A, promus inclus).
- **Invariant de segmentation** + garde-fou : un écart au compte attendu = **échec**,
  rien commis, ligne fautive nommée.
- `bzz_teams.country` / `bzz_leagues.country` : le pays.
- Pipeline stats : events → `/api/player-stats/?event=` → season stats ; joueurs via
  `/api/players/?team=` (espace A).
- Deux espaces d'identifiants Bzzoiro : **toujours l'espace A** (cf. racine PR #47).

## Conception (extension)

### 1. Registre des compétitions
Nouvelle table/constante `LEAGUE_REGISTRY` : par identifiant interne →
`{nom, pays, tier, kind, format, expected_count, rank}` où
`kind ∈ {domestic_1, domestic_2, uefa_club, world, selection}` et
`format ∈ {round_robin(N), groups, knockout}`. Remplace/complète
`TARGET_LEAGUE_INTERNAL_IDS` / `INTERNATIONAL_LEAGUE_INTERNAL_IDS`. `expected_count`
= compte réglementaire d'engagés (pour l'invariant), renseigné pour les ligues
`round_robin` uniquement.

### 2. Rattachement club → championnat + pays
- Le championnat d'un club = sa ligue **domestique** (`domestic_1`/`domestic_2`) où
  il est engagé. `league_api_id` = cette ligue ; **pays** = `bzz_leagues.country`.
- Les compétitions **UEFA ne définissent PAS** le championnat d'un club : un club de
  C1 garde son `league_api_id` domestique. La C1/EL/Conf fournit ses **matchs**
  (`bzz_events`), pas son engagement championnat.
- Un club présent **uniquement via une qualif UEFA** (championnat domestique hors
  périmètre) → pas de `league_api_id`, rangé « sans championnat », **signalé** —
  jamais de pays inventé.

### 3. Reconstruction par ligue domestique (généralise PR #47)
Pour chaque ligue `domestic_*`, saison en cours : énumérer `/api/events/` → engagés
(espace A) → écrire `league_api_id` + `season` + `country`. Remplacement **en bloc**
des engagements de la saison (un relégué perd son engagement, garde sa ligne/historique).
- **Invariant de segmentation** (round_robin) : compte exact = `expected_count`.
  Écart → **abort de cette ligue**, rien commis, ligue + compte obtenus nommés.
- UEFA / sélections / formats à groupes-knockout : pas d'invariant strict ;
  garde-fou = seuil minimal de clubs + structure attendue.

### 4. Ingestion stats joueurs — complète, pour tout le périmètre
Étendre les jobs worker (`sync_events` + stats) à **toutes** les ligues du registre :
events → `player-stats` par match → agrégation season stats → joueurs (`?team=`) →
`bzz_players` (espace A). Tournois historiques (66, 67) : **backfill one-shot**, hors
sync récurrent.

### 5. Contrôle de complétude « zéro joueur à moitié » (garde-fou)
Passe de vérification après ingestion, par club engagé :
1. events de la saison synchronisés ;
2. chaque match fini a ses player-stats ;
3. chaque joueur de l'effectif (`?team=`) a une ligne de stats (0 minute explicite
   admis ; **absence de ligne = gap**).
Tout gap → consigné dans `data_coverage_runs` + alerte (jamais silencieux). Un club
incomplet est **signalé**, pas affiché à moitié.

### 6. Cadence
- Championnats en cours : sync **quotidien** (registre entier).
- Historiques (Euro/Copa America 2024) : backfill unique.

## Modèle de données
- `canonical_teams.league_api_id` + `season` : **existe**, réutilisé pour toutes les
  ligues.
- `canonical_teams.country` (**nouveau**, dénormalisé) : pays du championnat
  domestique — pour le filtre/rangement UI sans join.
- `leagues` (**nouvelle table référentiel**) : id interne, nom, pays, tier, rank,
  kind, format, expected_count — source ordonnée des championnats pour le UI.
- `data_coverage_runs` (**nouvelle table**) : suivi des passes de complétude
  (ligue, clubs OK/KO, joueurs sans stats, statut).

## Garde-fou / anti-mort-silencieuse
Segmentation (domestiques) **et** complétude (tous clubs/joueurs) : tout écart =
échec consigné + alerte, jamais d'affichage partiel. Conforme à la règle « corrections
définitives » : complet, reconnecté, vérifié bout en bout.

## Hors périmètre
- Coupes nationales (stats partielles), Nigeria, fourre-tout amicaux, exotiques 0%.
- Le **UI lui-même** : cette spec prépare la donnée ; la refonte UI est le chantier suivant.

## Décisions à valider
1. **`country`** : colonne dénormalisée sur `canonical_teams` (reco, simple pour UI)
   ou join `bzz_leagues` à la volée ?
2. **Table `leagues`** référentiel pour l'ordre d'importance du UI : on la crée (reco) ?
3. **Clubs qualif-UEFA** sans championnat domestique couvert : gardés (matchs only,
   signalés — reco) ou exclus ?
