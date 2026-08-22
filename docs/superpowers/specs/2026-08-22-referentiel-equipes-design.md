# Référentiel des équipes — reconstruction

**Date :** 2026-08-22
**Statut :** validé (design), à planifier

## Problème

Trois symptômes visibles côté utilisateur, une seule racine.

### Symptôme 1 — les filtres mélangent les championnats

Le filtre Serie A remonte « Alcione Milano » ; le filtre Ligue des champions
remonte « Inter Club d'Escaldes » (Andorre). Le vrai **Milan est absent
d'Italie**.

### Symptôme 2 — des clubs entiers sont introuvables

Sur les 96 clubs des cinq championnats en 2026-2027, seuls 56 sont reconnus :

| Championnat | Clubs | Reconnus |
|---|---|---|
| Premier League | 20 | 18 |
| Ligue 1 | 18 | 14 |
| Bundesliga | 18 | 9 |
| La Liga | 20 | 9 |
| Serie A | 20 | 6 |

Manquent notamment les promus — Coventry City et Hull City en Premier League,
Paris FC, Le Mans et Troyes en Ligue 1 — et l'essentiel de la Serie A.

### Symptôme 3 — des effectifs étrangers

Ajax rend des joueurs irakiens, Benfica des Rwandais, Celtic des Argentins,
Feyenoord des Omanais. Tous les clubs du Big 5 sont corrects ; tous les
fautifs sont des clubs de Ligue des champions hors Big 5.

## Racine

**Bzzoiro expose ses équipes sous deux espaces d'identifiants, et la base
mélange les deux.**

| Usage | Espace |
|---|---|
| `/api/events/` → `home_team_obj.id` | **A** |
| `/api/players/?team=` | **A** |
| `/api/player-stats/?event=` | **A** |
| `bzz_players.current_team_api_id` | B (hérité) |
| `canonical_teams.bzz_team_id` | B (hérité) |
| `bzz_teams.api_id` | encore autre |

Les deux espaces coïncident par hasard en Ligue 1 et en Premier League, d'où
14/18 et 18/20 reconnus. Ils divergent ailleurs, d'où 6/20 en Serie A.

Deux conséquences en cascade :

1. **`bzz_players.current_team_name` est faux pour 886 joueurs sur 2 401** (37 %)
   des clubs canoniques — il a été rempli en joignant les deux espaces. Les
   joueurs du Barça y portent « Saint George ».

2. **Le championnat d'une équipe n'est stocké nulle part.** `canonical_teams`
   n'a aucune colonne de compétition. `_get_team_dominant_leagues`
   (`app/api/players.py:510`) le *déduit* en regroupant les joueurs **par
   `current_team_name`** — la colonne fausse — puis en prenant la compétition
   majoritaire. D'où les mélanges de championnats.

## Ce qui rend la correction possible

L'espace A est le seul qui fonctionne partout. Vérifié le 22/08/2026 :

| `?team=` | Effectif rendu | Club |
|---|---|---|
| 63 | Rabiot, Saelemaekers, Jashari | AC Milan |
| 77 | Bastoni, Bonny | Inter |
| 62 | Buongiorno, Meret, Rrahmani | Napoli |
| 203 | Amenda, Thomas-Asante | Coventry City (promu) |

`GET /api/players/?team=<id espace A>` rend donc l'effectif exact, promus
compris. Le même identifiant sert déjà au calendrier et aux statistiques de
match. Il devient la clé unique du référentiel.

## Objectif

Un référentiel d'équipes où chaque club porte **son identifiant d'espace A et
son championnat**, et où tout filtre résout par identifiant — jamais par nom.

## Conception

### Une colonne de championnat sur `canonical_teams`

Ajout de `league_api_id` (identifiant interne Bzzoiro : 1, 3, 4, 5, 6) et de
`season`, la paire décrivant l'engagement du club pour la saison en cours.

Le championnat devient une **donnée**, plus une déduction. `_get_team_dominant_leagues`
disparaît : sa raison d'être était l'absence de cette colonne.

Un club change de division entre saisons ; la paire `(league_api_id, season)`
l'exprime sans réécrire l'histoire.

### Reconstruction du référentiel

Pour chaque championnat du périmètre et la saison en cours, énumérer les
matchs via `GET /api/events/?league=<id>&date_from=…&date_to=…`, et collecter
les `home_team_obj` / `away_team_obj` distincts. Chaque objet porte `id` et
`name` : c'est la liste officielle des engagés, promus inclus.

Pour chaque club ainsi trouvé :

- si `canonical_teams` porte déjà ce nom, **corriger** son `bzz_team_id` vers
  l'espace A et renseigner son championnat ;
- sinon, **créer** l'entrée.

Aucune entrée n'est supprimée : un club relégué garde sa ligne et son
historique, il perd seulement son engagement pour la saison courante.

### Reconstruction des effectifs

Pour chaque club du référentiel, `GET /api/players/?team=<bzz_team_id>` donne
l'effectif. On écrit `current_team_api_id` (espace A) et `current_team_name`
(le nom rendu par l'API, cohérent par construction).

**Le sync Transfermarkt n'est pas remplacé.** Il réconcilie les départs et
arrivées entre deux passages ; il continuera de le faire, mais sur une base
saine. Sa logique de garde-fou (départ confirmé sur deux runs) reste inchangée.

### Les filtres résolvent par identifiant

Partout où un championnat ou une équipe est filtré, la résolution passe par
`canonical_teams.bzz_team_id` et `canonical_teams.league_api_id`. Aucun filtre
ne groupe par nom d'équipe.

## Périmètre

**Inclus** — les cinq championnats domestiques : Premier League (1), La Liga
(3), Serie A (4), Bundesliga (5), Ligue 1 (6).

**Exclu pour l'instant — la Ligue des champions (7).** Les tirages de la phase
de ligue n'ont pas eu lieu ; la liste des engagés n'est pas arrêtée. C'est
précisément là que se trouvent les mappings les plus faux (Ajax, Benfica,
Celtic, Feyenoord). Ils seront corrigés par le même mécanisme une fois les
tirages connus, sans modification de code.

## Hors périmètre

- Le nettoyage rétroactif de `bzz_player_season_stats` : les lignes hors
  périmètre existantes (15 124 pour 2025-2026) ne gênent pas une fois les
  filtres résolus par identifiant.
- Les alertes `IMPLAUSIBLE_NPXG` : elles viennent de joueurs à très peu de
  minutes, sujet distinct.
- L'échec Telegram 400 du récapitulatif autopilot.
- Toute modification du sync Transfermarkt lui-même.

## Tests

- **Référentiel** : les 96 clubs des cinq championnats 2026-2027 sont présents
  avec un championnat renseigné ; Coventry City, Hull City, Paris FC, Le Mans
  et Troyes en font partie.
- **Identifiants** : `AC Milan` porte 63, `Inter` 77, `Napoli` 62 — l'espace A.
- **Championnats** : aucun club n'est rattaché à un championnat où il ne joue
  pas ; « Alcione Milano » et « Inter Club d'Escaldes » n'apparaissent dans
  aucun des cinq.
- **Effectifs** : l'effectif d'un club rend les joueurs de ce club, vérifié sur
  un promu (Coventry City) et sur un club dont le mapping était faux (Milan).
- **Cohérence des noms** : plus aucun joueur d'un club du référentiel ne porte
  un `current_team_name` étranger à son club.
- **Relégué** : un club présent la saison passée et absent cette saison
  conserve sa ligne et son historique, sans engagement pour la saison courante.
- **Idempotence** : rejouer la reconstruction ne crée pas de doublon et ne
  modifie rien.
