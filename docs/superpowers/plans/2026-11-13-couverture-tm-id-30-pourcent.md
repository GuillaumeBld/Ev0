# Couverture tm_id : les 30 % restants + rendre durable le travail du 13/11

**Date** : 13/11/2026 · **Statut** : exécuté le jour même (code + passage immédiat en prod)

## Point de départ

- Univers Atlas : 28 360 joueurs. 19 887 ont un tm_id (70 %).
- Le 13/11, trois rattrapages ont été faits **à la main en prod**. S'ils restent manuels, ils ne se répètent pas :
  - clubs de coupe d'Europe (pages « participants » TM) ;
  - sélections nationales (lecture des listes, tm_id seulement) ;
  - clubs aux noms différents (écrits en base, eux sont durables).
- ~8 500 joueurs de l'univers n'ont toujours pas de tm_id. Cause principale : le joueur **est** sur la page effectif TM de son club, mais son nom diffère entre Bzzoiro et TM (diminutif, nom composé, translittération). Le matching actuel exige le nom complet identique.

## Amélioration 1 — reconnaître par date de naissance + club

**Pourquoi** : c'est le plus gros gisement (~8 500 joueurs), et l'information est déjà dans la page effectif lue chaque jour.

**Règle** (seconde passe, après le matching par nom, qui reste prioritaire) :
- joueur TM non reconnu par son nom, avec une date de naissance connue ;
- candidats = joueurs Bzzoiro **déjà rattachés à ce club**, nés **le même jour exact** ;
- garde-fou nom : au moins un mot du nom en commun (≥ 3 lettres, accents/casse ignorés) ou similarité ≥ 0,6 — écarte le cas « nouvelle recrue née le même jour qu'un joueur parti » ;
- exactement **un** candidat, pas déjà pris par un autre joueur TM de la page → reconnu. Sinon on ne devine pas.

**Fichiers** : `player_match.py` (paramètre `bzz_team_id` optionnel), `sync_squads.py` (le passe), tests `test_tm_player_match.py`.

**Effet de bord voulu** : un joueur reconnu est déjà dans ce club → pas de changement de club, seulement le tm_id en plus.

## Amélioration 2 — clubs de coupe d'Europe dans la résolution automatique

**Pourquoi** : chaque saison, de nouveaux clubs entrent en coupe d'Europe (tours préliminaires compris). Sans ça, ils n'ont jamais d'effectif synchronisé.

**Règle** : la résolution des clubs lit aussi les pages « participants » de CL, EL, UCOL et de leurs qualifications (CLQ, ELQ, ECLQ), saison courante calculée (bascule au 1er juillet). Même matching strict qu'aujourd'hui.

**Fichiers** : `resolve_clubs.py`, tests `test_tm_resolve_clubs.py`.

## Amélioration 3 — sélections nationales, chaque semaine

**Pourquoi** : les listes changent à chaque rassemblement ; le passage du 13/11 était ponctuel.

**Règle** :
- identifiants des sélections lus sur le classement FIFA de TM (9 pages) ;
- rapprochement par nom + table d'alias (USA/United States, Cabo Verde/Cape Verde, Côte d'Ivoire/Ivory Coast…) ;
- lecture de la liste de chaque sélection, matching joueurs habituel, **écriture du tm_id uniquement** ;
- **jamais** de `transfermarkt_club_id` sur une sélection : la synchro quotidienne déplacerait le club actuel des joueurs vers leur sélection.

**Fichiers** : nouveau `ingestion/transfermarkt/national_teams.py`, job hebdomadaire dans `worker.py` (mardi 03:30 UTC), tests.

## Passage immédiat en prod

1. Amélioration 1 : une synchro effectifs complète avec la nouvelle règle (~790 clubs, ~30 min), en parallèle du remplissage carrière en cours.
2. Les nouveaux tm_id sont pris automatiquement par le remplissage en cours.
3. PR à fusionner **après** la fin du remplissage (le déploiement redémarre le worker).

## Critères de réussite

- Tests backend verts.
- Nombre de joueurs de l'univers avec tm_id en hausse nette après le passage (mesuré avant/après).
- Zéro changement de club parasite : `players_updated` du passage ≈ celui d'une synchro normale.
