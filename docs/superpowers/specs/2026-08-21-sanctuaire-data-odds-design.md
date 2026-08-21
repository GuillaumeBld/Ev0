# Sanctuaire data/odds — design

**Date :** 2026-08-21
**Statut :** validé (design), à planifier

## Problème

La bibliothèque des xG (`team_xg_estimates`) archive définitivement, pour chaque
match, l'ouverture et la clôture : les λ des deux équipes et les cotes brutes
qui les ont produits.

Elle est **invisible**. Aucun écran, aucun point d'accès en lecture — la seule
mention de la table dans l'API est une *suppression*, quand on efface un match.

Une donnée qu'on ne peut pas regarder finit par être oubliée, et personne ne
s'aperçoit qu'elle est fausse. Aujourd'hui, la seule façon de consulter 56
estimations archivées est d'interroger la base à la main.

## Objectif

Une page qui rend la bibliothèque consultable, et qui répond à une question :
**de combien le marché a-t-il changé d'avis entre l'ouverture de la ligne et le
coup d'envoi, et dans quel sens ?**

Ce n'est **pas** un écran d'exécution. Les recommandations sont opérationnelles
et éphémères — des paris à prendre, qui expirent au coup d'envoi. Le Sanctuaire
est un outil d'analyse et d'archive, sans vocation à être joué. Les deux ne se
mélangent pas.

## Emplacement

Entrée **« Sanctuaire »** dans la section *Analyse* du menu, entre Matchs et
Équipes. Route `/dashboard/sanctuaire`.

## Ce que la page montre

Une carte par match, triée par **coup d'envoi décroissant** : le récent et
l'imminent en haut, l'historique en dessous.

Chaque carte porte les deux équipes avec le nul entre elles, puis deux bandes —
**cote**, puis **xG**. Dans chaque case :

- la valeur de **clôture** en gros, en **jaune** ;
- la valeur d'**ouverture** en petit dessous, en **bleu**.

Le bleu et le jaune sont le code de lecture de toute la page. Ils ne servent à
rien d'autre.

**Le nul n'a pas de xG** : c'est une issue, pas une équipe. La case reste vide.

Maquette validée : traitement « sobre » — la clôture en avant, l'ouverture
dessous, sans piste colorée ni écart chiffré.

## D'où viennent les chiffres

**Exclusivement de `team_xg_estimates`.** Aucun calcul à la volée, aucune lecture
des snapshots de cotes.

La page montre ce qui est archivé, ni plus ni moins. Si l'archive est fausse ou
incomplète, la page le montre — c'est délibéré : elle sert aussi de contrôle sur
la bibliothèque elle-même.

## Filtres

Quatre filtres, combinables, appliqués **côté serveur** (la table grossira).

### Équipe

Recherche par nom, sur les **deux côtés** — domicile comme extérieur.

Insensible aux accents : « Alaves » doit trouver « Deportivo Alavés ». Le dépôt
possède déjà un pliage de caractères dans `app/ingestion/ps3838/anchor.py`
(`_fold`), qui traite aussi les lettres non décomposables (`ø`, `æ`, `ł`) —
le réutiliser plutôt qu'en écrire un autre.

### Compétition

Liste déroulante alimentée par les ligues **réellement présentes dans la
bibliothèque**, pas par une liste figée. PS3838 publie de la Liga au championnat
féminin colombien : sans ce filtre, les grands championnats se noient.

### État de l'archive

Deux modes :

- **tout** — y compris les matchs n'ayant que leur ouverture ;
- **avec clôture** — seulement les archives complètes.

Les deux usages sont différents : parcourir ce qui arrive, ou analyser ce qui est
complet.

### Amplitude du mouvement

Seuil en pourcentage : ne garder que les matchs dont le marché a bougé d'au
moins X % entre l'ouverture et la clôture.

**Définition retenue : le plus grand mouvement relatif parmi les trois cotes du
1X2.** Autrement dit « de combien le marché a-t-il repricé ce match ». On
retient le maximum et non la moyenne, parce qu'un seul camp qui décroche est
précisément le signal recherché.

C'est le seul filtre qui fait un travail d'analyse au lieu de réduire une liste :
il fait remonter les matchs où une information est arrivée tard — blessure,
composition, météo.

**Interaction imposée : choisir une amplitude force l'état « avec clôture ».**
Un match sans clôture n'a pas de mouvement. La page l'affiche explicitement
plutôt que de renvoyer une liste vide sans explication.

## Ce qui manquera souvent, et c'est normal

La clôture n'existe qu'après le coup d'envoi. La page passera donc l'essentiel
de son temps avec des cases de clôture vides : mention discrète « en attente »,
ouverture toujours visible.

Un match sans **aucune** ligne dans la bibliothèque n'apparaît pas du tout — la
page reflète l'archive, elle ne liste pas le calendrier.

## Hors périmètre

- Filtre par période. Il n'y a que deux jours de données ; il deviendra utile
  dans quelques semaines et s'ajoutera sans rien casser.
- Pagination. 56 lignes aujourd'hui ; à revoir au-delà de quelques centaines.
- Export, tri par colonne, recherche plein texte.
- Piste colorée et écart chiffré (traitement 2 des maquettes, écarté).
- Toute exploitation analytique — comparaison de méthodes, calibration, score de
  Brier. La page donne à voir ; elle ne calcule pas.

## Tests

- **Point d'accès** : rend les deux phases quand elles existent, une seule quand
  la clôture manque ; une bibliothèque vide donne une liste vide, pas une erreur.
- **Tri** : coup d'envoi décroissant, les matchs sans clôture correctement
  intercalés selon leur date et non relégués.
- **Filtre équipe** : trouve des deux côtés ; « Alaves » trouve
  « Deportivo Alavés » ; une recherche sans résultat rend une liste vide.
- **Filtre compétition** : la liste proposée ne contient que des ligues
  présentes dans la bibliothèque.
- **Filtre état** : « avec clôture » exclut bien les archives incomplètes.
- **Filtre amplitude** : le calcul retient le **maximum** des trois mouvements
  du 1X2, pas la moyenne ; un seuil sélectionné force l'état « avec clôture » ;
  un match dont une seule cote a beaucoup bougé est retenu.
- **Combinaison** : deux filtres actifs se cumulent au lieu de s'annuler.
- **Affichage** : la case du nul ne porte jamais de xG.
