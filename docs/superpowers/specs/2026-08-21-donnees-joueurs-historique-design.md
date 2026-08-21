# Données joueurs — ingestion par match, historique et compos

**Date :** 2026-08-21
**Statut :** validé (design), à planifier

## Contexte

L'abonnement Bzzoiro est passé en illimité le 21/08/2026. Le plafond de
7 500 requêtes/jour qui dictait l'architecture d'ingestion n'existe plus.

Ce document couvre quatre chantiers liés par une même racine : la façon dont
les joueurs et leurs statistiques entrent dans le système.

## Problème

### 1. Le sélecteur du calculateur ramène les mauvais joueurs

`GET /api/v1/lineups/team-players/{team}` (`backend/app/api/lineups.py:262`)
cherche par sous-chaîne du nom de club et tronque à 100 :

```python
select(BzzPlayer.name)
    .where(BzzPlayer.current_team_name.ilike(f"%{team}%"))
    .order_by(BzzPlayer.name)
    .limit(100)
```

Mesuré en production :

| Recherche | Joueurs retournés par le `ILIKE` |
|---|---|
| `Inter` | 463 (Inter Milan, Inter Miami, Internacional…) |
| `Real Madrid` | 122 (dont Castilla et féminines) |
| `Arsenal` | 117 |

Au-delà de 100, `order_by(name)` tronque alphabétiquement. L'utilisateur reçoit
donc une liste polluée par d'autres clubs **et** amputée de la fin de l'alphabet
du club recherché.

**La colonne interrogée est en outre corrompue.** Bzzoiro expose les équipes
sous **deux espaces d'identifiants distincts**, et la base mélange les deux :

- `bzz_players.current_team_api_id` et `canonical_teams.bzz_team_id` partagent
  un même espace, cohérent ;
- `bzz_teams.api_id` en utilise un autre.

`bzz_players.current_team_name` a été rempli en joignant les deux — d'où des
libellés faux. Les joueurs du Barça portent `current_team_name = "Saint George"`
(un club éthiopien), parce que leur `current_team_api_id` vaut 2817, qui désigne
Barcelone dans un espace et Saint George dans l'autre.

Le filtre du sélecteur porte donc sur une colonne fausse. Rechercher
« Barcelone » ne peut structurellement pas retourner l'effectif du Barça.

En revanche, les identifiants numériques sont fiables — vérifié le 21/08/2026 :

| Club canonique | `bzz_team_id` | Joueurs retournés |
|---|---|---|
| Barcelone | 2817 | Balde, Christensen, Dani Olmo, Eric García |
| Inter Milan | 2697 | Bastoni, Pavard, Bonny |
| Dortmund | 2673 | Emre Can, Nmecha, Svensson |
| Naples | 2714 | Meret, Rrahmani, Buongiorno |

Toute résolution d'équipe doit donc passer par les identifiants, jamais par les
noms d'équipe stockés.

### 2. Seuls 16 % des joueurs ont des statistiques

96 318 joueurs sont rattachés à un club ; 15 421 ont des stats 2025-2026.

La cause est dans `sync_player_stats.py`, dont l'en-tête pose une contrainte
**aujourd'hui fausse** :

> The Bzzoiro /api/player-stats/ endpoint never includes player identity in its
> response. The only correct approach is to query per-player.

Vérification du 21/08/2026 sur `GET /api/player-stats/?event=223384` :

- 44 lignes, `count = 44`, `next = null` — les deux effectifs entiers, sans pagination ;
- 44/44 portent `player: {id, name, short_name, position, positions_detailed, team}` ;
- tous les champs de stats consommés par le sync sont présents.

L'endpoint accepte donc `event=`, et l'identité du joueur est bien renvoyée.
Le sync fait aujourd'hui **une requête par joueur** là où **une requête par
match** suffit — d'où une boucle qui ne termine jamais et une couverture de 16 %.

Effets constatés : Inter 21 joueurs en base dont 12 avec stats ; Real Madrid
86 joueurs rattachés, effectifs jeunes et féminins confondus.

### 3. L'historique s'arrête à juillet 2025

`bzz_events` couvre 2025-07-10 → 2026-09-20. L'API remonte bien plus loin.
Sans profondeur, aucun backtest sérieux n'est possible.

### 4. Les compos arrivent avec jusqu'à 6 heures de retard

`sync_bzzoiro_lineups` tourne toutes les 30 minutes, mais il ne **récupère**
rien : il relit `bzz_events.lineups`, un bloc JSONB alimenté par
`job_sync_bzzoiro_events`, planifié **toutes les 6 heures**
(`app/worker.py:2083`).

Bzzoiro publie les compos environ 1 h avant le coup d'envoi. Avec un
rafraîchissement à 6 h, la compo est captée tard ou pas du tout avant le match.
Le job à 30 minutes donne l'illusion de la fraîcheur.

## Objectif

Que chaque joueur ayant foulé une pelouse dans le périmètre retenu possède ses
statistiques, sur cinq saisons, et que les compos soient disponibles avant le
coup d'envoi et non après.

## Périmètre

**Compétitions** — identifiants Bzzoiro, déjà présents dans
`TARGET_LEAGUE_INTERNAL_ID_LIST` :

| ID | Compétition |
|---|---|
| 1 | Premier League |
| 3 | La Liga |
| 4 | Serie A |
| 5 | Bundesliga |
| 6 | Ligue 1 |
| 7 | Champions League |

**Profondeur** — 5 saisons, de 2021-2022 à 2025-2026.

**Extensibilité** — la liste des compétitions est une donnée de configuration,
jamais une valeur en dur dispersée dans le code. Les ajouts déjà envisagés
existent côté API et devront s'ajouter sans retoucher la logique :
Allsvenskan 26, Eliteserien 54, Danish Superliga 84, Veikkausliiga 55,
Europa League 8, Conference League 83, UEFA Super Cup 90.

**Volume mesuré** — saison 2024-2025 : 381 + 383 + 380 + 308 + 311 + 279 =
**2 042 matchs**. Cinq saisons ≈ **10 200 requêtes** de statistiques, plus
environ 250 requêtes d'énumération.

## Conception

### Chantier 1 — Sélecteur du calculateur

Résoudre le nom d'équipe reçu vers `canonical_teams.bzz_team_id`, puis filtrer
`bzz_players.current_team_api_id` en égalité stricte. Supprimer la limite à
100 : un effectif exact ne la dépasse pas, et la troncature alphabétique est
précisément le défaut à corriger.

La résolution se fait sur `canonical_teams.name_fr` et `name_en`, en repliant
les accents avec le `_fold` existant de `app/ingestion/ps3838/anchor.py`.

**Aucun repli sur les noms d'équipe.** Ni `bzz_teams.name` ni
`current_team_name` ne sont utilisables : ils relèvent de l'espace
d'identifiants erroné. Une équipe non résolue retourne une liste vide. Une
liste vide est un défaut visible qui se corrige en complétant
`canonical_teams` ; une liste peuplée par un autre club est un défaut
invisible qui contamine les compos.

Ce chantier est indépendant des trois autres et ne dépend d'aucune ingestion.

### Chantier 2 — Ingestion des statistiques par match

Réécrire `sync_player_stats` pour parcourir les **matchs** et non les joueurs.

Pour chaque match terminé du périmètre : un appel
`GET /api/player-stats/?event=<api_id>`, puis insertion des lignes retournées.

**Correspondance des identifiants — point critique.** Le champ `player.id` de
la réponse correspond à **`bzz_players.api_id`**, pas à `internal_id`. Vérifié
sur deux joueurs :

| `player.id` renvoyé | `bzz_players.api_id` | `bzz_players.id` | Nom |
|---|---|---|---|
| 594 | 594 | 325895 | Kylian Mbappé |
| 27598 | 27598 | 791201 | Loris Karius |

`bzz_players.api_id` porte la contrainte unique `uq_bzz_players_api_id` : c'est
la clé d'insertion.

Un joueur inconnu de `bzz_players` doit être créé à partir de l'identité fournie
dans la réponse, jamais silencieusement ignoré — c'est le mécanisme même qui
comble les 84 % manquants. La table `bzz_player_match_stats` porte une clé
étrangère vers `bzz_players.api_id` : sans cette création, l'insertion échoue.

**Deux colonnes aujourd'hui vides sont remplies au passage.** Sur 1 135 494
lignes, `team_api_id` et `is_home` ne sont renseignées que 2 153 fois. La
réponse ne porte pas ces champs — le code actuel lit `row["team"]` et
`row["is_home"]`, absents de la réponse, et écrit donc `NULL` à chaque ligne.

L'appel par match les fournit indirectement : `player.team` donne le nom du club
du joueur, `event.home_team` et `event.away_team` ceux des deux camps. Les trois
chaînes viennent de la même réponse et se comparent donc sans ambiguïté, ce qui
donne `is_home`, puis `team_api_id` par lecture de `bzz_events`.

L'en-tête du module doit être réécrit : la contrainte qu'il énonce est fausse et
a dicté toute l'architecture actuelle.

### Chantier 3 — Backfill historique

Une commande d'administration, distincte du job périodique, mais s'appuyant sur
**la même fonction d'ingestion** que le chantier 2. Aucune logique dupliquée.

Énumération des matchs par `GET /api/events/?league=<id>&date_from=…&date_to=…`.

Le paramètre `season=` de l'API est **inopérant** : `?season=2024-2025` renvoie
408 110 événements remontant à 1930. Seul le couple `league` + `date_from` /
`date_to` filtre correctement. Une saison se définit donc par une fenêtre de
dates, du 1er juillet au 30 juin.

La commande doit être reprenable : interrompue, elle redémarre sans retraiter ce
qui est déjà en base, et journalise sa progression par saison et compétition.

### Chantier 4 — Fraîcheur des compos

Réduire l'intervalle de `job_sync_bzzoiro_events`, qui est le véritable goulet.
Deux régimes plutôt qu'un intervalle unique agressif :

- **régime normal** — toutes les 6 heures, cadence actuelle, pour les matchs
  lointains ;
- **régime d'approche** — toutes les 10 minutes pour les matchs dont le coup
  d'envoi est dans les trois heures, jusqu'au coup d'envoi.

Dix minutes laisse au moins cinq occasions de capter une compo publiée à
H−1, tout en restant très en deçà de ce que l'abonnement illimité autorise.

Le job `sync_bzzoiro_lineups` conserve sa cadence : il n'est pas en cause.

## Ordre d'exécution

1. Chantier 1 — indépendant, corrige un défaut visible immédiatement.
2. Chantier 2 — clé de voûte : il fournit le moteur du chantier 3.
3. Chantier 3 — consomme le moteur du chantier 2.
4. Chantier 4 — indépendant, peut être fait à tout moment.

## Hors périmètre

- L'autopilot et le règlement des paris en attente, écartés par l'utilisateur
  pour cette période.
- Les compétitions hors des six retenues — ajoutées plus tard par configuration.
- La rétention des snapshots de cotes (`SNAPSHOT_RETENTION_DAYS = 45`), sujet
  distinct de l'historique des statistiques joueur.
- Toute exploitation analytique de l'historique : backtests, calibration. Ce
  chantier produit la donnée, il ne l'exploite pas.
- Le nettoyage des effectifs pollués (équipes jeunes et féminines rattachées au
  club principal). Le chantier 1 le contourne par l'égalité stricte sur
  l'identifiant ; l'assainissement de fond relève du sync Transfermarkt.

## Tests

**Chantier 1**
- « Inter Milan » ne retourne que l'effectif de l'Inter, ni Inter Miami ni Internacional.
- La résolution passe par `bzz_team_id` : un club dont le `current_team_name`
  stocké est faux retourne malgré tout le bon effectif.
- Un club de plus de 100 joueurs rattachés retourne l'effectif entier, sans troncature.
- Une équipe absente de `canonical_teams` retourne une liste vide, pas une erreur
  et pas l'effectif d'un autre club.
- La résolution ignore les accents : « Seville » trouve « Séville ».

**Chantier 2**
- Une réponse `event=` produit une ligne de statistiques par joueur retourné.
- Un joueur absent de `bzz_players` est créé, et sa ligne de stats écrite.
- La correspondance se fait sur `api_id` : un joueur dont l'`api_id` vaut celui
  renvoyé est retrouvé, même si son `id` interne diffère.
- Rejouer le même match n'ajoute pas de doublon.
- Un match sans statistiques publiées n'écrit rien et ne lève pas d'erreur.
- `is_home` vaut vrai pour un joueur dont le club égale `event.home_team`, faux
  pour l'autre camp, et `team_api_id` est renseigné dans les deux cas.

**Chantier 3**
- L'énumération d'une saison n'utilise que `date_from` / `date_to`, jamais `season=`.
- Une exécution interrompue puis relancée ne retraite pas les matchs déjà ingérés.
- La liste des compétitions vient de la configuration : en ajouter une étend le
  backfill sans modification de code.

**Chantier 4**
- Un match à moins de trois heures du coup d'envoi déclenche le régime d'approche.
- Un match lointain reste en régime normal.
- Aucun match en approche ne laisse le job tourner après le coup d'envoi.
