# Fiche match — données Bzzoiro v2

**Date :** 2026-08-25
**Statut :** validé (design), à planifier

## Problème

### Les compos ne sont ni réelles ni sauvegardées

Le calculateur price avec des compos reconstituées : `sync_bzzoiro_lineups`
relit `bzz_events.lineups`, un bloc alimenté par le sync des événements. Rien
ne distingue une compo **probable** d'une compo **officielle**, et aucune
n'est historisée. Impossible donc de savoir, après coup, avec quelle compo un
prix a été calculé — ni de réutiliser la dernière compo connue d'une équipe.

### Les colonnes prévues pour ça sont vides depuis toujours

`bzz_events` porte déjà `shotmap`, `incidents`, `momentum`,
`average_positions` et `lineups`. Mesure du 25/08/2026 sur les cinq
championnats, matchs terminés :

| | Matchs |
|---|---|
| Terminés | 8 965 |
| Avec compo | **0** |
| Avec carte des tirs | **0** |

Seuls 28 matchs — ceux de la CDM — sont renseignés, par un sync distinct.

La cause : `sync_events` lit `row.get("shotmap")` sur `/api/events/`, qui ne
renvoie pas ce champ. Il écrit donc `NULL` depuis toujours, sans qu'aucun
signal ne l'indique.

### La partie « Matchs » ne montre presque rien

Elle liste les rencontres et leurs cotes. Pas de compo, pas de statistiques,
pas de tirs — alors que la partie CDM 2026 propose déjà tout cela.

## Ce qui rend la correction possible

Bzzoiro expose une **API v2**, déjà utilisée par le code CDM. Vérifiée le
25/08/2026 sur Fulham–Chelsea (`209544`) :

| Point d'accès | Contenu |
|---|---|
| `/api/v2/events/{id}/` | arbitre, stade, météo, forme, confrontations, entraîneurs |
| `/api/v2/events/{id}/lineups/` | **`lineup_status`**, `updated_at`, formation, XI, remplaçants, absents |
| `/api/v2/events/{id}/stats/` | `shotmap`, `momentum`, `average_positions`, `xg_per_minute`, stats par mi-temps |
| `/api/v2/events/{id}/incidents/` | 23 incidents — buts, cartons, périodes |
| `/api/v2/events/{id}/player-stats/` | 40 lignes de statistiques individuelles |

Le shot map porte, pour chacun des 32 tirs : position, xG, xG cadré, pied,
situation, minute, et le point d'impact dans le but.

**`lineup_status` est la pièce maîtresse.** Il vaut `confirmed` quand la compo
est officielle. Associé à `updated_at`, il permet enfin de savoir si un prix a
été calculé sur une compo réelle ou supposée.

Le complément d'après-match vient de `/api/matches/{id}/` (v1) : minute
d'entrée des remplaçants, qui remplace qui, notes, cartons.

## Objectif

Une partie « Matchs » calquée sur celle de la CDM 2026, alimentée par l'API
v2, et des compos officielles **sauvegardées** exploitables par le pricing.

## Périmètre

**Les cinq championnats domestiques** — Premier League, La Liga, Serie A,
Bundesliga, Ligue 1.

**La Ligue des champions est exclue** jusqu'aux tirages de la phase de ligue,
conformément à la décision du 22/08. Elle s'ajoutera sans modification de code.

**Profondeur** : la saison en cours d'abord, puis rattrapage des 8 965 matchs
terminés en tâche de fond.

## Conception

### Étape 1 — « Matchs » devient « Calendrier »

L'entrée actuelle du menu Analyse est renommée, la route passe de
`/dashboard/matches` à `/dashboard/calendrier`. Le contenu ne change pas :
c'est bien un calendrier des rencontres et de leurs cotes.

Une redirection conserve les liens existants plutôt que de les casser.

### Étape 2 — Ingestion des données de match

Un module dédié interroge les points d'accès v2 et remplit les colonnes
existantes de `bzz_events`. Aucune nouvelle table pour ces blocs.

**Deux régimes, dictés par la nature de la donnée :**

- **avant match** — seules les compos évoluent. On interroge `/lineups/`
  jusqu'à ce que `lineup_status` passe à `confirmed`, puis on cesse.
- **après match** — tout le reste devient définitif. On interroge `/stats/`,
  `/incidents/` et `/player-stats/` une fois, et on n'y revient plus.

Cette séparation évite de retélécharger 8 965 fois des données figées.

**Historisation des compos.** Une nouvelle table `match_lineups` conserve
chaque version publiée : match, camp, `lineup_status`, `updated_at`,
formation, joueurs, absents. Une compo probable remplacée par la compo
officielle **ne remplace pas** la précédente — les deux sont conservées.

C'est ce qui permet de répondre après coup à « avec quelle compo ce prix
a-t-il été calculé ? », et de récupérer la dernière compo connue d'une équipe
pour le match suivant.

**Garde-fou.** Un match dont un bloc revient vide n'est pas marqué comme
traité : il sera retenté. Un bloc vide écrit silencieusement est précisément
ce qui a laissé 8 965 matchs sans compo pendant des mois.

### Étape 3 — La fiche match

Une liste, puis une fiche par match reprenant l'organisation CDM : Résumé,
Compos, Stats, Shot map, Stats joueur.

**Les composants CDM sont réutilisés tels quels** — `ShotMap`, `MatchPitch`,
`MatchDetailPanel`, `JerseyCard` existent et fonctionnent. Même code de
lecture sur tout le site.

Le point d'accès de lecture sert la fiche depuis la base, jamais depuis
l'API : la page montre ce qui est archivé, comme le Sanctuaire.

**La compo affichée porte son statut.** Une compo probable est signalée comme
telle, avec l'heure de sa dernière mise à jour. L'utilisateur voit sur quoi il
s'appuie.

### Étape 4 — Branchement sur le pricing

Le résolveur de compos utilisé par le calculateur préfère, dans cet ordre :

1. la compo **officielle** du match, si elle existe ;
2. la compo **probable** la plus récente du match ;
3. la **dernière compo officielle connue** de l'équipe, sur un match précédent ;
4. le comportement actuel, en dernier recours.

Chaque niveau est signalé dans l'interface : pricer sur la dernière compo
connue n'est pas pricer sur la compo du jour, et cela doit se voir.

## Hors périmètre

- La Ligue des champions et les coupes nationales.
- Le direct : `live_websocket` et le suivi minute par minute.
- Toute exploitation analytique du shot map — calibration des xG de marché,
  comparaison modèle/réel. Ce chantier produit la donnée, il ne l'exploite pas.
- La refonte visuelle des composants CDM.
- Les notes de joueurs comme entrée du modèle.

## Tests

**Renommage**
- L'entrée du menu affiche « Calendrier » et pointe sur la nouvelle route.
- L'ancienne route redirige au lieu de rendre une erreur.

**Ingestion**
- Un match dont `/lineups/` rend `confirmed` cesse d'être interrogé avant match.
- Un match dont un bloc revient vide n'est pas marqué traité, et sera retenté.
- Rejouer l'ingestion sur un match déjà complet n'écrit rien de nouveau.
- Une compo probable puis une compo officielle produisent **deux** lignes dans
  `match_lineups`, la première n'étant pas écrasée.
- Un match sans compo publiée n'écrit aucune ligne et ne lève pas d'erreur.

**Fiche match**
- La fiche rend les cinq blocs quand ils existent, et signale ceux qui manquent
  plutôt que d'afficher une page vide.
- Une compo probable est visuellement distinguée d'une compo officielle.
- La fiche lit la base et n'appelle jamais l'API Bzzoiro.

**Pricing**
- Une compo officielle est préférée à une compo probable du même match.
- Sans compo pour le match, la dernière compo officielle de l'équipe est
  utilisée, et l'origine est signalée.
- Sans aucune compo, le comportement actuel est conservé.
