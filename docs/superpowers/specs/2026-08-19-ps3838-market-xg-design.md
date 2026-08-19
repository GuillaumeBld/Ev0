# xG d'équipe depuis PS3838 — design

**Date :** 2026-08-19
**Statut :** validé (design), à planifier

## Problème

Le xG d'équipe qui alimente tout le pricing buteur/passeur est dérivé des cotes
1X2 + totals d'**un seul bookmaker**, choisi par ordre de priorité parmi ceux
disponibles (`_preferred_bookmaker` dans `app/services/market_xg.py`). Or ces
cotes sont rattachées aux matchs par **comparaison de noms d'équipes**, et ce
rapprochement se trompe régulièrement : des cotes d'un autre match se retrouvent
attachées à la mauvaise rencontre.

### Cas constaté le 19/08/2026 — Atlético Madrid vs Málaga CF

| Source | Domicile | Nul | Extérieur |
|---|---|---|---|
| Pinnacle | 1.35 | 5.35 | 9.46 |
| pmu | 1.33 | 4.90 | 8.50 |
| unibet | 1.51 | 2.70 | 6.10 |
| **betclic** (retenu) | **2.58** | **3.03** | **2.83** |

Betclic décrivait un match équilibré là où le marché donne l'Atlético à 1.35.
Le solveur, alimenté par ces cotes, a produit **λ_dom = 1.071 / λ_ext = 1.021**
au lieu de **1.855 / 0.524**. Reproduction vérifiée : rejouer le solveur avec les
seules cotes Betclic redonne exactement 1.071 / 1.021, résidu compris
(0.00227 contre 0.002269 en production).

Conséquences : le modèle donnait **33 % de chances de victoire à Málaga** contre
~12 % pour le marché, une recommandation à **+186 % d'edge** sur Málaga à 8.50,
et l'autopilot l'a approuvée. Les fausses opportunités sont parties sur le canal
`value` (notifications téléphone).

### Pourquoi rien n'a alerté

Le contrôle qualité mesure la **cohérence interne** des cotes (1X2 vs totals vs
BTTS d'une même source). Comme toutes les cotes venaient du même — mauvais —
match, elles sont parfaitement cohérentes : résidu 0.002, ajustement excellent,
`flagged=False`. Le garde-fou ne se demande jamais si c'est le **bon** match.

### Ampleur

Comparaison de chaque bookmaker au consensus sur les matchs à venir : l'écart
médian à la médiane est de 4,5 points de probabilité, et **un match sur trois
dépasse 10 points**. Ces écarts ne sont pas du bruit de marché, ce sont d'autres
rattachements erronés, répartis sur **des sources différentes selon les matchs**
(betclic sur Atlético–Málaga et Monza–Udinese, pmu sur Rennes–Le Mans, unibet sur
Arsenal–Coventry).

### Pourquoi un consensus multi-sources ne suffit pas

Une médiane entre bookmakers a été envisagée puis **écartée sur preuve**. Sur
Real Madrid – Real Sociedad :

| Source | Domicile | Nul | Extérieur | |
|---|---|---|---|---|
| Pinnacle (référence) | 1.386 | 5.11 | 7.13 | |
| betclic | 1.39 | 5.00 | 6.75 | juste |
| pmu | 2.04 | 3.30 | 3.60 | autre match |
| unibet | 1.68 | 2.40 | 5.40 | autre match |

Deux sources sur trois sont fausses et **la seule juste est minoritaire**. Une
médiane aurait écarté la bonne valeur et conservé les deux mauvaises. Le
consensus statistique tolère la maladie au lieu de la soigner.

## Objectif

Une **source de vérité unique et fiable** pour le xG d'équipe, rattachée aux
matchs par **identifiant** et non par nom, de sorte qu'un mauvais rattachement
devienne structurellement impossible.

## Source retenue : PS3838

PS3838 est une déclinaison de la plateforme Pinnacle. Constats vérifiés le
19/08 :

- **Identifiants d'événements identiques à Pinnacle.** Atlético–Málaga porte le
  numéro `1632359428` chez les deux, Málaga–Deportivo `1633731080`.
- **Accessible depuis le VPS** (HTTP 200), là où `guest.api.arcadia.pinnacle.com`
  répond **403** (Cloudflare) depuis l'hôte comme depuis les conteneurs — même
  blocage que FotMob et Sofascore.
- **Cotes en décimal**, pas de conversion depuis le format américain.
- **892 matchs sur une fenêtre de 10 jours** (19/08 → 30/08 au moment du relevé).
- Couverture des fixtures Ev0 à **moins de 3 jours : 91 %** ; à plus de 7 jours,
  la couverture chute — c'est normal, aucun bookmaker n'ouvre ses lignes trois
  semaines à l'avance.

### Contrat de l'API

Pas de page par compétition (contrairement à Betclic/Unibet) : **tout passe par
la catégorie football**, `sportId = 29`.

Deux appels, dont l'union couvre la fenêtre complète :

```
GET https://www.ps3838.com/sports-service/sv/compact/events?sp=29
GET https://www.ps3838.com/sports-service/sv/compact/events?sp=29&mk=0&pa=0
```

Le premier renvoie les matchs imminents (~2 h), le second ceux à partir du
lendemain. Un match à 3 h du coup d'envoi peut n'apparaître dans **aucun des
deux** : constaté à 16 h pour un match de 19 h, présent dans le premier flux à
17 h 39. Les deux appels doivent donc être faits à chaque cycle et fusionnés, et
l'absence d'un match ne doit pas effacer son ancrage existant.

Un `User-Agent` de navigateur est requis. Le paramètre `leagueId` est ignoré par
cet endpoint.

### Structure de la réponse

Dictionnaire dont plusieurs clés (`l`, `n`, …) portent des blocs. Chaque bloc
exploitable est une liste de sports ; `sport[2]` donne les ligues,
`ligue[2]` les événements. Un événement est un tableau compact :

```
[event_id, equipe_dom, equipe_ext, ?, kickoff_ms, ?, ?, ?, periodes]
```

`periodes["0"]` est le match entier (`"1"` = première mi-temps) :

| Index | Contenu |
|---|---|
| `[0]` | handicaps asiatiques — `[hcp_dom, hcp_ext, label, cote_dom, cote_ext, …]` |
| `[1]` | totals — `[label, ligne, cote_over, cote_under, …]` |
| `[2]` | 1X2 — **`[extérieur, domicile, nul, …]`** |

**L'ordre du 1X2 n'est pas (domicile, nul, extérieur).** Vérifié sur trois
matchs, dont deux au centième près contre Pinnacle :

| Match | Brut PS3838 | Lecture |
|---|---|---|
| Marseille – Strasbourg | `['4.590','1.719','4.220']` | dom 1.719 / nul 4.22 / ext 4.59 |
| Real Madrid – Real Sociedad | `['7.130','1.386','5.110']` | dom 1.386 / nul 5.11 / ext 7.13 |
| Atlético – Málaga | `['8.990','1.362','5.260']` | dom 1.362 / nul 5.26 / ext 8.99 |

Se tromper sur cet ordre inverserait domicile et extérieur — exactement la classe
de bug que cette refonte élimine. Un test doit le verrouiller.

**Pas de marché BTTS** dans ce flux.

## Architecture

### 1. Client PS3838

Nouveau module `app/ingestion/ps3838/client.py`. Deux appels, fusion, extraction
des événements en objets typés `(event_id, home, away, kickoff_utc, league,
h2h, totals)`. Rate-limit et retry comme les autres scrapers. Aucune écriture en
base : le client retourne des données, il ne décide de rien.

### 2. Ancrage par identifiant

Migration : `fixtures.ps3838_event_id INT NULL` + index unique.

Résolution **une seule fois par fixture**, avec double vérification :

- les deux équipes correspondent (normalisation accents + tokens),
- **et** le coup d'envoi concorde à ±2 h près.

Un seul des deux critères ne suffit pas. Les non-résolus sont **retournés et
surfacés**, jamais devinés. Une fois posé, l'identifiant n'est plus jamais
recalculé : les cotes sont récupérées **par identifiant**, sans aucune
comparaison de noms au moment du scraping.

### 3. Stockage des cotes

Les cotes PS3838 sont écrites dans `match_odds_snapshots` avec
`bookmaker = 'ps3838'`, comme les autres sources. Le rattachement se fait par
`fixtures.ps3838_event_id`, jamais par nom.

### 4. Calcul du xG

`MarketXgService` ne lit plus que `bookmaker = 'ps3838'`. Betclic, Unibet, PMU et
le Pinnacle relayé par Bzzoiro **ne servent plus au calcul du xG**. Le chemin à
quatre contraintes (L-BFGS-B avec BTTS) devient inatteignable faute de BTTS : on
utilise le chemin à deux contraintes déjà présent (`solve_lambda_t` puis
`solve_lambda_home_from_h2h`, validé par `cross_validate_h2h`) — deux inconnues,
deux équations.

**Généralisation de la ligne de totals.** Le code actuel cherche `over_2.5` en
dur. PS3838 propose la ligne réellement cotée, qui varie (3.0 sur
Marseille–Strasbourg). `solve_lambda_t` doit accepter n'importe quelle ligne
entière ou demi-entière et résoudre λ_total à partir de `P(over ligne)`.

Les lignes quart (`3.25`, `"2.5-3"`) sont ignorées : on retient la ligne
principale entière ou demi-entière la plus proche de 2.5.

### 5. Remontée des échecs

Un match à **moins de 7 jours** du coup d'envoi sans identifiant résolu, ou sans
cotes PS3838 exploitables, est une **anomalie** : PS3838 ouvre ses lignes 10
jours à l'avance. Elle part sur le canal `incidents` avec le nom du match et la
raison.

Au-delà de 7 jours, l'absence est normale et silencieuse.

**Aucun repli sur les books FR pour le xG.** Comportement actuel, conservé tel
quel : si le xG de marché est indisponible, `MarketXgService.compute` renvoie
`None`, `load_match_pricing` renvoie `None` à son tour, et le match **n'est pas
pricé**. Le repli Bzzoiro existe dans le code mais est désactivé. On ne substitue
jamais en silence une source dont on sait qu'elle se trompe.

C'est précisément pourquoi l'alerte à 7 jours est le cœur du dispositif : un
match non ancré est un match **sans recommandations**, ce qui est inacceptable et
doit donc être réparé, pas absorbé. PS3838 ouvrant ses lignes 10 jours à
l'avance, cette situation ne doit jamais survenir dans la fenêtre utile — si elle
survient, c'est un bug à corriger sous 7 jours, avec le nom du match dans
l'alerte.

### 6. Ce qui ne change pas

Betclic, Unibet et PMU **restent inchangés** pour les cotes buteur et passeur :
c'est là que l'utilisateur mise, et une value est l'écart entre le prix juste et
le prix réellement obtenable. Seul l'usage de leurs cotes **1X2/totals pour le
calcul du xG** disparaît.

## Cadence

Le scraper PS3838 tourne sur le même tick que le scheduler de cotes existant. La
résolution des identifiants tourne une fois par jour et à la création d'une
fixture.

## Tests

- **Ordre du 1X2** : fixture JSON réelle → dom/nul/ext correctement lus ; un test
  dédié verrouille l'inversion.
- **Fusion des deux flux** : un match présent dans un seul des deux est retenu ;
  un match absent des deux ne supprime pas l'ancrage existant.
- **Ancrage** : noms concordants + kickoff à ±2 h → résolu ; noms concordants mais
  date différente → non résolu ; accents (« Atlético » vs « Atletico ») → résolu ;
  homonymie sans date concordante → non résolu et surfacé.
- **Ligne de totals** : 2.5, 3.0, 3.5 → λ_total correct ; ligne quart ignorée au
  profit de la ligne principale.
- **Non-régression du solveur** : les cotes Pinnacle d'Atlético–Málaga
  (1.35 / 5.35 / 9.46, total 3.0) redonnent λ ≈ 1.86 / 0.52, et **pas**
  1.07 / 1.02.
- **Incident** : fixture à 3 jours sans ancrage → alerte `incidents` ; fixture à
  20 jours sans ancrage → silence.
- **Isolation** : `MarketXgService` ne lit aucun snapshot dont le bookmaker n'est
  pas `ps3838`.

## Hors périmètre

- Consensus ou médiane entre bookmakers (écarté sur preuve, cf. plus haut).
- Piwi247 (joignable depuis le VPS, non exploré — PS3838 suffit).
- Correction du rapprochement par noms des books FR pour les cotes joueur : le
  problème existe aussi là, mais il ne relève pas de ce chantier.
- La table `team_xg_estimates`, vide depuis toujours (0 ligne) et jamais écrite :
  à traiter séparément.
