# Archivage des cotes de closing — design

**Date :** 2026-08-20
**Statut :** validé (design), à planifier

## Problème

La bibliothèque des xG de référence (`team_xg_estimates`, cf.
`2026-08-19-ps3838-market-xg-design.md` §6) archive **définitivement** deux
estimations par match : l'ouverture et le closing. Elle stocke `lambda_home`,
`lambda_away`, `fit_residual` et `flagged`.

Elle ne stocke **pas les cotes qui ont produit ces valeurs**.

Or `job_purge_old_snapshots` efface `match_odds_snapshots` au-delà de **45
jours**. Passé ce délai, il ne reste que la conclusion — le λ — sans la preuve.

### Ce que ça coûte

Toute amélioration future de la méthode devient **intestable sur le passé** :

- changer le retrait de marge (question ouverte et documentée : la marge Pinnacle
  pèse sur le favori dans le handicap, l'impact sur λ atteint 4,3 % sur les
  favoris sous 1.40) ;
- changer le solveur, la ligne de totals retenue, le traitement du push ;
- toute idée non encore formulée.

Chacune exigerait de brancher une nouvelle capture puis d'**attendre plusieurs
mois** avant de pouvoir comparer. À chaque idée. C'est le genre de contrainte qui
décourage l'expérimentation, et donc qui fige un modèle.

Avec les cotes archivées, la même question se tranche en une après-midi sur des
centaines de matchs déjà joués.

### L'échéance

La bibliothèque a démarré le **20/08/2026**. Les snapshots correspondants
existent encore : les lignes déjà écrites sont **rattrapables**. La purge
commencera à mordre début octobre. Après, la fenêtre est définitivement fermée
pour les matchs concernés.

## Objectif

Conserver, à côté de chaque estimation archivée, **les cotes brutes qui l'ont
produite** — pour que toute évolution ultérieure de la méthode soit rejouable
sur l'historique.

## Portée

**Dans le périmètre :** les marchés effectivement utilisés par le calcul —
`h2h` (1X2) et `totals` (la ligne retenue), pour les deux phases `opening` et
`closing`.

**Hors périmètre :**

- Les **handicaps asiatiques**. Ils permettraient de reconstruire les
  probabilités sans passer par le 1X2 — seul moyen de trancher la question de la
  répartition de la marge. Mais ils ne sont pas collectés : le client PS3838 les
  lit (`periodes["0"][0]`) et les jette. Les faire descendre jusqu'au stockage
  touche client, scraper et écriture. **Décision séparée, à prendre avant début
  octobre** si on veut garder cette possibilité.
- La trajectoire complète entre ouverture et closing.
- **Toute exploitation** de ces données. Cette spec crée le stock, elle ne le
  consomme pas. Les comparaisons de méthodes, la calibration, les scores de
  Brier : plus tard, sur cette matière.

## Conception

### Ce qu'on stocke

Une colonne `odds JSONB NULL` sur `team_xg_estimates`, portant exactement le
dictionnaire déjà construit au moment du calcul :

```json
{
  "h2h":    {"home": 1.347, "draw": 5.35, "away": 9.46},
  "totals": {"over_3.0": 1.854, "under_3.0": 2.04}
}
```

**Les cotes brutes, telles que le bookmaker les affichait.** Pas les
probabilités dévigées, pas la ligne de totals dupliquée dans une colonne
dédiée : tout cela se recalcule, et stocker du dérivé invite à la divergence le
jour où la formule change. On garde la preuve, pas la conclusion.

`NULL` est autorisé : les lignes écrites avant cette migration en porteront un
tant que le rattrapage n'a pas tourné, et une estimation dont les snapshots
auraient disparu n'est pas rattrapable.

### Où

Sur **la même ligne** que le λ qu'elle justifie. Une ligne = un instant = une
estimation et les cotes qui l'ont produite. Une table séparée n'ajouterait
qu'une jointure sans bénéfice.

### Comment

`_archive` dans `app/services/xg_library.py` reçoit déjà `markets` de
`_snapshot_group` — c'est précisément ce dictionnaire. Il suffit de le persister
au lieu de le laisser filer. **Aucun scraping supplémentaire, aucune requête de
plus.**

Les deux phases sont traitées à l'identique : `capture_opening` et
`capture_closing` passent toutes deux par `_archive`.

### Rattrapage de l'existant

Un script one-shot remplit `odds` pour les lignes déjà archivées, en relisant
les snapshots désignés par `input_snapshot_ids`.

- Idempotent : ne touche que les lignes dont `odds IS NULL`.
- Une ligne dont les snapshots ont disparu est **laissée à `NULL` et comptée**,
  jamais devinée ni reconstruite depuis une autre source.
- Rapport final : rattrapées / impossibles, avec le motif.

**À exécuter dès le déploiement.** Chaque jour d'attente rapproche des lignes de
la purge.

## Ce qui peut mal tourner

**Les snapshots ont déjà disparu.** Seul cas irréparable, et c'est le sujet même
de cette spec. Le rattrapage le compte et le signale ; il ne fabrique rien.

**Le format du dictionnaire change.** Les clés viennent du scraper
(`over_<ligne>`, `home`/`draw`/`away`). Un changement de format rendrait les
archives anciennes illisibles par le code récent. On ne verrouille pas le format
par une contrainte : on archive ce que le calcul a réellement consommé, et un
lecteur futur devra tolérer les variantes. Le test de bouclage (ci-dessous) est
ce qui garantit la cohérence à l'instant de l'écriture.

**La colonne grossit.** Deux lignes par match, quelques centaines d'octets
chacune. À l'échelle du site, négligeable — et sans commune mesure avec la
valeur d'un historique rejouable.

## Tests

- **Conservation** : archiver une estimation stocke exactement les cotes qui ont
  servi au calcul — mêmes marchés, mêmes issues, mêmes valeurs.
- **Bouclage** : recalculer λ depuis les cotes archivées redonne la valeur
  stockée dans la même ligne. C'est le vrai critère de réussite : si la boucle se
  referme, le passé est rejouable.
- **Deux phases** : l'ouverture et le closing portent chacune leurs propres
  cotes, et celles du closing diffèrent de celles de l'ouverture quand le marché
  a bougé.
- **Idempotence du rattrapage** : deux exécutions successives ne modifient rien
  la seconde fois.
- **Snapshots absents** : la ligne reste à `NULL`, est comptée comme
  impossible, et le script ne lève pas.
- **Rétention** : `team_xg_estimates` reste absente de `job_purge_old_snapshots`
  (test déjà en place, à ne pas casser).
