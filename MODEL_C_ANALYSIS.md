# Analyse — Modèle C + Agent Flat
> **Rédigé par Guillaume + Claude Code le 2026-03-18. À lire avant ta prochaine session.**
> Il y a un bug en production (section CRITIQUE) à corriger avant le prochain run du worker.

**Période :** 2026-03-03 → 2026-03-16 (mode paper)
**Périmètre :** Toutes les recommandations goalscorer settlées, mise plate de 10 € sur chaque rec

---

## Résultats globaux

```
settlées | victoires | défaites | void | taux_victoire | pnl_net | cote_moy | edge_moy
---------+-----------+----------+------+---------------+---------+----------+---------
      77 |        12 |       52 |   13 |         18.8% | -217.30 |     7.05 |    19.8%
```

La probabilité moyenne du modèle est de 22.3 % mais le taux de victoire réel est de 18.8 % → surestimation systématique.

---

## CRITIQUE (bug en production) — Bug sur les minutes introduit le 2026-03-17

Un run d'ingestion défaillant le 2026-03-17 a inséré **143 lignes pour 63 joueurs** avec le total de minutes sur la saison dans `minutes_played` mais `matches_played=1`. Le moteur de pricing calcule `expected_minutes = minutes_played / matches_played` — le prochain run de génération de recommandations va donc calculer 1 000 à 2 500 minutes attendues par match pour ces joueurs.

**Impact :** `mins_ratio = expected_minutes / 90` devient un **multiplicateur ×15 à ×28** sur le lambda. Chaque joueur concerné sera pricé comme quasi-certain de marquer :

```
joueur                   | position | minutes_corrompues | xg_par_90 | prob_gonflée
-------------------------+----------+--------------------+-----------+-------------
Pascal Struijk           | DF       |               2348 |     0.097 |        92%  (réelle : ~2%)
Trai Hume                | DF       |               2489 |     0.059 |        81%  (réelle : ~1%)
Ibrahima Konaté          | DF       |               2404 |     0.039 |       ~65%  (réelle : ~1%)
Jhon Arias               | FW       |               1117 |     0.272 |        97%  (réelle : ~27%)
Yoane Wissa              | FW       |                421 |     0.417 |        86%  (réelle : ~35%)
```

Les données valides pour les 63 joueurs existent à `as_of_utc = 2026-03-16` (25–30 matchs chacun). La `latest_subq` dans `get_recommendations_for_date` ne prend que la date la plus récente par `(player_id, league)` — les lignes corrompues du 17 mars écrasent donc complètement les données valides du 16 mars.

**Correctif immédiat :** Supprimer les lignes corrompues avant le prochain run du worker :
```sql
DELETE FROM player_stats
WHERE as_of_utc::date = '2026-03-17'
  AND matches_played = 1
  AND minutes_played > 120;
-- 143 lignes supprimées
```

**Correctif de fond :** Ajouter une vérification dans le script d'ingestion : `SI minutes_played / matches_played > 120 → rejeter la ligne`. Ajouter également une contrainte ou un trigger en DB.

---

## Problème 1 — Paris en double sur le même joueur (bug encodage des noms)

Le système génère **deux recommandations distinctes pour le même joueur** quand Betclic et Unibet orthographient le nom différemment (accents, abréviations). La contrainte unique `(fixture_id, player_name, market_type)` ne les attrape pas car les chaînes diffèrent.

**Doublons confirmés (même joueur, même match) :**

```
domicile         | extérieur          | nom1               | nom2               | résultat1 | résultat2 | pnl
-----------------+--------------------+--------------------+--------------------+-----------+-----------+-----
Lyon             | Paris FC           | Nicolas Tagliafico | Nicolás Tagliafico | perdu     | perdu     | -20
Atletico Madrid  | Tottenham Hotspur  | Joao Palhinha      | João Palhinha      | perdu     | perdu     | -20
Atletico Madrid  | Tottenham Hotspur  | Micky Van de Ven   | Micky van de ven   | perdu     | perdu     | -20
Newcastle United | Barcelona          | Joe Willock        | Joseph Willock     | perdu     | perdu     | -20
Newcastle United | Barcelona          | Jules Kounde       | Jules Koundé       | void      | void      |   0
```

**Total gaspillé sur les doublons : -80 PnL** (4 paires perdues en double).

**Correctif :** Normaliser les noms de joueurs avant l'insertion — supprimer les accents (`unidecode`), mettre en minuscules, supprimer la ponctuation. Appliquer cette normalisation à la vérification de contrainte unique.

---

## Problème 2 — Le modèle se trompe dans la plage 12–30 % de probabilité

L'intégralité des pertes vient des milieux et des milieux offensifs. Les paris avec `fair_probability` entre 12 % et 30 % affichent un **taux de victoire de 0 %** sur 31 paris settlés.

```
tranche_prob                    | paris | victoires | taux_victoire | prob_modèle |    pnl
--------------------------------+-------+-----------+---------------+-------------+-------
<12%  (défenseurs/rare scoreurs)|     8 |         1 |         12.5% |       0.101 |  +35.00
12-20% (milieux)                |    16 |         0 |          0.0% |       0.150 | -160.00  ← zéro victoire
20-30% (milieux offensifs)      |    15 |         0 |          0.0% |       0.243 | -150.00  ← zéro victoire
30-40% (attaquants)             |    14 |         4 |         28.6% |       0.349 |   -4.80
40%+   (top attaquants)         |     8 |         5 |         62.5% |       0.449 |  +47.50  ← rentable
```

**Les paris à haute probabilité (>40 %) sont rentables (+47.50).** Tout ce qui est en dessous de 30 % est une perte sèche.

**Correctifs possibles :**
- Filtre dur : ne sortir que les recs où `fair_probability >= 0.35`
- Ajouter un filtre sur le poste : exclure les défenseurs et milieux défensifs
- Recalibrer le facteur `conversion_rate` spécifiquement pour les joueurs sous 30 %

---

## Problème 3 — Cause racine : CALIBRATION_SCALE supprimé sans recalibration

**Fichier :** `backend/app/services/recommendation_service.py`, ligne 30

```python
CALIBRATION_SCALE = 1.0  # Top-down model; old 0.62 was calibrated for bottom-up xg_per_90
```

L'ancien modèle bottom-up utilisait `CALIBRATION_SCALE = 0.62` pour déflater les probabilités. Lors du passage au modèle top-down (basé sur `fixture_strength`), le facteur a été remis à 1.0 — **mais aucune nouvelle calibration n'a été faite**. Cela gonfle chaque probabilité d'un facteur `1/0.62 ≈ 1.6×`.

**Impact :** Un milieu avec `xg_per_90=0.12`, 75 min, `fixture_strength=1.5` :
- Modèle actuel : `λ=0.15 → P=13.9%`
- Avec l'ancienne calibration : `13.9% × 0.62 = 8.6%` → cote fair ~11.6, le marché offre 9-11, quasiment pas d'edge → **filtré**
- À 13.9 %, l'edge contre des cotes marché de 9-11 semble positif → **classé VALUE à tort**

C'est la raison exacte pour laquelle la tranche 12-30 % (milieux, milieux offensifs) a 0 victoire.

**Correctif :** Recalculer `CALIBRATION_SCALE` empiriquement à partir des données settlées. Avec l'échantillon actuel : taux de victoire réel = 18.8 %, probabilité moyenne du modèle = 22.3 % → facteur de calibration = 18.8/22.3 ≈ **0.84**. La vraie correction devrait bucketer par poste/plage de probabilité, car les recs à haute probabilité (>40 %) semblent déjà bien calibrées.

---

## Problème 4 — Données corrompues dans player_stats

La table `player_stats` présente des problèmes de qualité sérieux :

```sql
-- Dan Burn (CB Newcastle) : npxg_per_90 = 54.7 (devrait être ~0.007)
Dan Burn | D S | Newcastle | xg_per_90=0.007 | npxg_per_90=54.7 | matches=3

-- Micky van de Ven (CB Tottenham) : npxg_per_90 = 86.6 (devrait être ~0.093)
Micky van de Ven | DF | Tottenham | xg_per_90=0.093 | npxg_per_90=86.6 | matches=5
```

Des valeurs `npxg_per_90` à 54+ sont physiquement impossibles (max atteignable en 90 min : ~3-4 xG). Il s'agit probablement d'un bug de conversion d'unité — le total cumulé de la saison stocké à la place du taux par 90 min. Le code de pricing utilise `xg_per_90` pour le lambda (pas `npxg_per_90`), donc ça n'affecte pas directement le pricing, mais ça corrompt le seuil `npxg_total` à la ligne 158.

De plus, chaque joueur a **10 à 15 lignes en double** dans `player_stats` (ex. : Micky van de Ven : 15 lignes, Palhinha : 11 lignes, Maitland-Niles : 11 lignes).

**Correctif :** Ajouter une contrainte unique sur `(player_id, league, as_of_utc)` dans `player_stats` et nettoyer les doublons. Ajouter aussi un plafond : `npxg_per_90 > 3.0 → NULL`.

---

## Problème 5 — L'edge n'a aucune valeur prédictive

Un edge plus élevé ne signifie pas un meilleur taux de victoire. La tranche 20–30 % d'edge est la pire.

```
tranche_edge | paris | victoires | taux_victoire |    pnl
-------------+-------+-----------+---------------+--------
0-10%        |    11 |         2 |         18.2% |  -47.80
10-20%       |    16 |         4 |         25.0% |  -58.00
20-30%       |    17 |         1 |          5.9% | -144.50  ← pire résultat
30-40%       |    13 |         2 |         15.4% |  +22.00
40%+         |     4 |         1 |         25.0% |   -4.00
```

Aucune relation monotone — le calcul d'edge reflète actuellement la sur-confiance du modèle, pas une vraie inefficience de marché. Tant que la calibration des probabilités n'est pas corrigée (Problème 3), l'edge n'est pas un filtre fiable.

---

## Problème 6 — Concentration de paris par match

Le modèle génère 6 à 9 recommandations par match, ce qui peut représenter 60 à 90 € d'exposition sur une seule rencontre (agent flat). Quand les matchs UCL ne produisent aucun buteur parmi nos joueurs, c'est une hémorragie multi-paris.

**Pires matchs (semaine du 9–12 mars) :**

```
domicile         | extérieur          | compétition      | recs | victoires |  pnl
-----------------+--------------------+------------------+------+-----------+------
Newcastle United | Barcelona          | champions_league |    9 |         1 |  -52
Auxerre          | Strasbourg         | ligue_1          |    7 |         0 |  -70
Lyon             | Paris FC           | ligue_1          |    7 |         1 |  -40
Atletico Madrid  | Tottenham Hotspur  | champions_league |    6 |         0 |  -60
```

Semaine du 9 mars seule : **-249 PnL**, presque entièrement dû aux matchs UCL avec beaucoup de recs à faible probabilité.

**Correctif :** Plafonner les recommandations par match (ex. : top 3 par edge/fair_prob) ou plafonner l'exposition flat par match.

---

## Tendance PnL hebdomadaire (Modèle C, goalscorer uniquement)

```
semaine     | paris | victoires |    pnl
------------+-------+-----------+--------
2026-03-02  |    23 |         6 |  +17.00   ← bon départ
2026-03-09  |    38 |         4 | -249.30   ← semaine UCL catastrophique
2026-03-16  |     0 |         0 |    0.00   (settlement en cours)
```

La première semaine positive était portée par des picks à haute probabilité. Le modèle a décroché quand les matchs UCL ont introduit un grand volume de recs à probabilité moyenne sur des défenseurs/milieux.

---

## Récapitulatif — Correctifs par priorité

| Priorité | Problème | Fichier | Impact PnL |
|----------|----------|---------|------------|
| **CRITIQUE** | Supprimer les 143 lignes corrompues du 2026-03-17 | DB : `DELETE … WHERE as_of_utc::date='2026-03-17' AND matches_played=1 AND minutes_played>120` | Le prochain run va pricer 63 joueurs à 80–97 % de probabilité |
| P0 | Ajouter une vérification d'ingestion : rejeter si `minutes/matches > 120` | scripts d'ingestion | Évite la récurrence |
| P0 | Restaurer `CALIBRATION_SCALE ≈ 0.84` | `recommendation_service.py:30` | -310 (toute la tranche 12–30 %) |
| P0 | Nettoyer les doublons player_stats + contrainte unique `(player_id, league, as_of_utc)` | table `player_stats` | Pricing non déterministe |
| P1 | Normaliser les noms de joueurs avant insertion en DB (supprimer accents) | `worker.py:821` | -80 confirmés |
| P2 | Filtre intermédiaire : exclure `fair_probability < 0.35` | `selector.py` ou `worker.py` | -310 sur la tranche 12–30 % |
| P2 | Plafonner les recs par match (max 3 par fair_prob) | `selector.py:apply_exposure_limits` | Réduit les blowouts UCL |
| P3 | Plafonner `npxg_per_90 > 3.0 → NULL` à l'ingestion | scripts d'ingestion | Intégrité des données |
