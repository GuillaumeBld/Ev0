# Cote joueur quand les données manquent — a priori de poste et mélange progressif

**Date** : 6 novembre 2026
**Statut** : conception validée, non implémentée
**Périmètre** : buteur et passeur. Ni le xG d'équipe, ni les marchés d'équipe, ni la détection de value.

---

## Pourquoi

Un joueur sans statistique offensive exploitable ne doit pas recevoir une cote inventée, et ne doit pas non plus être privé de cote. Il doit recevoir **le prix le plus prudent compatible avec ce qu'on sait de lui** : son poste, et le xG attendu de son équipe pour ce match.

### Ce qui se passait avant

Le plancher de λ — 0,001 buteur, 0,01 passeur — existe pour éviter une division par zéro. La cote supersub, qui normalise à 90 minutes en divisant λ par `minutes_attendues / 90`, le multipliait par 90 :

```
passeur :  0,01  / (1/90) = 0,9   →  probabilité 0,593  →  cote 1,69
buteur  :  0,001 / (1/90) = 0,09  →  probabilité 0,086  →  cote 11,62
```

Constaté en production sur 30 jours : 867 recommandations à 1,69 et 362 à 11,62, toutes avec λ au plancher. Aucune ne reposait sur une donnée du joueur.

### Ce qui se passe aujourd'hui

Le correctif du 06/11 (PR #83) rend une **absence** : part du xG d'équipe nulle → probabilité et cote à 0, et les garde-fous avals écartent le joueur. Plus de fausse cote, mais plus de cote du tout.

C'est un arrêt d'hémorragie, pas l'état final. Ce document décrit l'état final.

---

## La cascade

Trois niveaux, du plus informé au moins informé :

| Niveau | Source | Quand |
|---|---|---|
| 1 | Saison en cours (`bzz_player_season_stats`) | assez de minutes |
| 2 | Carrière (`player_career_seasons` + saisons Bzzoiro antérieures) | saison en cours trop mince |
| 3 | A priori du poste | rien d'exploitable sur le joueur |

À **tous** les niveaux, le rythme obtenu est ancré sur le **xG PS3838 de l'équipe pour ce match**. Un attaquant inconnu dans une équipe qui attend 2,4 buts est mieux coté que le même dans une équipe à 0,9. L'ancrage ne change pas selon le niveau.

---

## Pas de seuil : un mélange progressif

Un seuil net créerait une marche — à 449 minutes le poste, à 451 le joueur, deux cotes très différentes pour un match de plus. Le mélange l'évite :

```
poids_joueur = minutes_pondérées / (minutes_pondérées + M)

rythme = poids_joueur × rythme_mesuré  +  (1 − poids_joueur) × a_priori_poste
```

`M` est la quantité de minutes à partir de laquelle le joueur pèse autant que son poste. **Valeur de départ : 900 minutes** (dix matchs pleins), à calibrer (voir plus bas).

Les minutes sont comptées **en minutes, jamais en matchs** : vingt entrées de cinq minutes ne valent pas vingt titularisations, et compter en matchs confondrait les deux.

### Comportement vérifié

Attaquant à 0,60 xG/90 réel, a priori du poste 0,347 :

| Minutes | Poids joueur | Rythme | Cote* |
|---:|---:|---:|---:|
| 0 | 0 % | 0,347 | 3,41 |
| 90 | 9 % | 0,370 | 3,23 |
| 450 | 33 % | 0,431 | 2,85 |
| 900 | 50 % | 0,473 | 2,65 |
| 1 800 | 67 % | 0,516 | 2,48 |
| 5 000 | 85 % | 0,561 | 2,33 |

\* cote à 90 minutes sans ancrage d'équipe, pour isoler l'effet du mélange.

La cote glisse sans marche vers la valeur réelle du joueur, au rythme des minutes jouées.

---

## Trois propriétés à ne pas perdre

### 1. L'a priori est un centre de gravité, pas un plancher

Attaquant **faible**, à 0,12 xG/90 réel :

| Minutes | Rythme | Cote |
|---:|---:|---:|
| 0 | 0,347 | 3,41 |
| 900 | 0,233 | 4,80 |
| 3 000 | 0,172 | 6,32 |
| 5 000 | 0,155 | 6,98 |

Il part de l'a priori puis s'en éloigne **vers le bas**. Si l'a priori était un plancher, un attaquant réellement médiocre resterait coté 3,41 — et produirait de la fausse value à chaque match. Le temps de jeu et les données priment toujours.

### 2. Le mélange neutralise les petits échantillons explosifs

Un remplaçant qui accumule 0,30 xG en 5 minutes affiche **5,40 xG/90** brut, soit une cote de 1,00 — le même défaut que le gardien à 68 xG/90 relevé le 06/11.

Avec le mélange, ses 5 minutes pèsent **0,6 %** : le rythme retenu est 0,375, à peine au-dessus de l'a priori de son poste. Aucun garde-fou spécifique n'est nécessaire, la formule s'en charge.

### 3. Un seul a priori par poste, sans correction de club

Un attaquant inconnu de Manchester City et un attaquant inconnu de Lorient reçoivent le **même** a priori. C'est l'ancrage sur le xG du match qui les sépare : City attend plus de buts, donc son attaquant est mieux coté.

Ajouter une correction de club doublerait cette information et risquerait de la compter deux fois.

---

## A priori mesurés

Mesure du 06/11/2026 sur `bzz_player_season_stats`, saison en cours, joueurs à 450 minutes ou plus, cinq grands championnats et C1 :

| Poste | Joueurs | xG / 90 | xA / 90 | Écart-type xG |
|---|---:|---:|---:|---:|
| Attaquant | 667 | 0,347 | 0,115 | 0,191 |
| Milieu | 1 475 | 0,132 | 0,126 | 0,118 |
| Défenseur | 1 289 | 0,056 | 0,059 | 0,062 |
| Gardien | 308 | 0,035 | 0,006 | 0,056 |

Les gardiens restent exclus du pricing (déjà le cas), la ligne est donnée pour mémoire.

L'écart-type dit quelque chose d'utile : un attaquant inconnu est trois fois plus incertain qu'un défenseur inconnu (0,191 contre 0,062). Un `M` par poste est donc envisageable — à trancher par la calibration, pas a priori.

---

## Calibration

Les valeurs ci-dessus sont un **point de départ mesuré**, pas une vérité. `M` et les a priori se règlent sur l'historique au **score de Brier** : on compare plusieurs valeurs, on garde celle dont les cotes tombent le plus juste.

Rappel du principe : sur tous les joueurs cotés à 20 %, environ 20 % doivent marquer réellement. Le Brier mesure l'écart.

Ce qu'il faut pour calibrer :
- les résultats réels (`match_events`, 11 351 buts et passes)
- les stats par match (`bzz_player_match_stats`, 1,49 M de lignes)
- un rythme **calculé avant le match**, jamais après — sans quoi la mesure se ment à elle-même

**Prérequis bloquant** : 4 899 recommandations sur 22 770 (21,5 %) portent un λ au plancher. Elles doivent être écartées de toute calibration, sinon un cinquième du jeu de référence est de la fiction. Leur sort reste à décider (suppression ou marquage).

---

## Affichage dans le calculateur

Une cote dont la moitié vient du poste ne doit pas s'afficher comme une cote mesurée. Trois niveaux, lisibles d'un coup d'œil, fondés sur le poids du joueur — déjà calculé par la formule :

| Poids joueur | Statut | Lecture |
|---|---|---|
| < 25 % | A priori | la cote vient surtout du poste |
| 25 – 60 % | Partielle | mélange joueur / poste |
| > 60 % | Mesurée | la cote est celle du joueur |

---

## Ce que ce document ne traite pas

**Le câblage de Beta.** Le moteur qui lit déjà la carrière (`beta_pricing.py` + `career_blend.py`) existe, est testé, et n'est appelé que par l'endpoint du calculateur pour affichage. `recommendation_service.py` ne le mentionne jamais — zéro occurrence. Le service de comparaison (`model_snapshot_service.py`) existe mais n'est **appelé par personne**, d'où une table `model_pricing_snapshots` vide.

Autrement dit : la cascade décrite ici recoupe en partie ce que Beta fait déjà. Avant d'implémenter, il faut déterminer si le bon chemin est de brancher Beta ou d'amender Alpha. C'est la première question de la session pricing.

**Le prix après l'annonce du 11.** Les compos arrivent 2 à 3 jours après le coup d'envoi (9 065 matchs mesurés, tous postérieurs). Le second prix est hors d'atteinte sans un chantier d'ingestion dédié.

**Les ~51 000 doublons de performances**, qui gonflent les compteurs de matchs et donc le poids du joueur dans le mélange. À nettoyer avant calibration.
