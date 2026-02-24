# Plan de Refonte du Pricing Engine — Top-Down Match-Centric

> **Ce document est le plan de référence pour l'agent qui va implémenter la refonte.**
> **ÉTAPE 1 (OBLIGATOIRE)** : Avant de coder quoi que ce soit, tu DOIS d'abord expliquer ce plan à Yohan (l'opérateur) en termes non-techniques, répondre à ses questions, et obtenir sa validation. Seule la section "Explication pour Yohan" et "POC — Proof of Concept" doivent être discutées avec lui. La section "Implémentation" est pour toi seul.

---

## Explication pour Yohan (à lui présenter AVANT de coder)

### Le problème actuel

Le calculateur Ev0 ne permet pas de parier en l'état. Pourquoi ? Parce que le modèle utilise les **xG individuels de chaque joueur** (sa moyenne sur la saison) pour estimer la probabilité qu'il marque. C'est comme dire "Mbappé a 0.65 xG/90 donc il a 48% de chances de marquer dans CHAQUE match" — c'est faux.

En réalité, la probabilité de marquer dépend du **contexte du match** :
- Contre qui l'équipe joue (défense solide vs défense fragile)
- Combien de buts l'équipe va probablement marquer au total
- Le fait de jouer à domicile ou à l'extérieur

**Résultat du backtest** : le modèle actuel prédit des probabilités 2-4x trop hautes → ROI de -37%.

### La nouvelle approche

Conforme à la documentation officielle Ev0 (docs/01-architecture.md et 03-modeling.md), on passe en mode **Top-Down** :

1. **On part du match** : combien de buts l'équipe va marquer dans CE match précis
   - Calculé automatiquement à partir de la force d'attaque de l'équipe × la faiblesse défensive de l'adversaire × l'avantage domicile
   - L'opérateur peut override cette valeur avec le Team xG du marché (Pinnacle)

2. **On distribue entre les joueurs** : chaque joueur reçoit sa part du total
   - Part = sa contribution historique aux xG de l'équipe (ex: Mbappé = 28% des xG du PSG)
   - Penalties traités séparément (seul le tireur reçoit le bonus)

3. **On calcule la cote Ev0** : probabilité → cote fair → comparer avec bookmaker → edge

### La stratégie (tirée de la doc officielle)

> "Le marché (Pinnacle) est efficient sur le nombre de buts total d'une équipe, mais souvent absent ou inefficace sur les buteurs individuels. En calant le total de l'équipe sur la Vérité Marché, on élimine le biais de prédiction du match pour ne chasser que l'inefficience de la répartition (Market Share) entre les joueurs."

En clair : on ne cherche pas à prédire combien de buts une équipe va marquer mieux que le marché. On utilise CE QUE LE MARCHÉ DIT (ou notre estimation), et on cherche les joueurs mal pricés dans la répartition.

### POC — Proof of Concept (résultats validés)

Trois matchs testés avec le nouveau modèle :

**PSG (home) vs Marseille (away) — match déséquilibré :**
- PSG match xG estimé : 2.58 | OM : 0.94

| Joueur | P(but) | Cote Ev0 | Cote bookmaker typique | Match ? |
|--------|--------|----------|------------------------|---------|
| Mbappé (pen taker) | 53% | 1.89 | 1.80-2.00 | OUI |
| Dembélé | 36% | 2.78 | 2.80-3.20 | OUI |
| Barcola | 30% | 3.33 | 3.50-4.00 | OUI |
| Greenwood (OM) | 26% | 3.85 | 4.00-5.00 | OUI |

**Man City (home) vs Crystal Palace (away) — très déséquilibré :**
- City match xG : 2.83 | Palace : 0.80

| Joueur | P(but) | Cote Ev0 | Cote bookmaker | Match ? |
|--------|--------|----------|----------------|---------|
| Haaland (pen) | 65% | 1.54 | 1.40-1.55 | OUI |
| Foden | 28% | 3.57 | 3.25-4.00 | OUI |
| Mateta (Palace) | 24% | 4.17 | 5.00-6.00 | OUI |

**Nantes (home) vs Strasbourg (away) — équilibré bas de tableau :**
- Nantes : 1.15 | Strasbourg : 1.21

| Joueur | P(but) | Cote Ev0 |
|--------|--------|----------|
| Meilleur FW Nantes | 25% | 4.00 |
| 2e FW | 16% | 6.25 |
| Milieu | 9% | 11.1 |

**Conclusion** : les cotes Ev0 sont systématiquement cohérentes avec les cotes réelles des bookmakers, dans les 3 types de matchs.

### Ce qui va changer pour Yohan en tant qu'opérateur

1. **Calculateur** : Au lieu de bouger des sliders manuels, il sélectionne un match → voit tous les joueurs avec leurs cotes
2. **Override** : Il peut entrer le Team xG du marché (ex: Pinnacle) pour encore plus de précision
3. **Recommandations** : Plus réalistes, plus calibrées, cotes dans les bonnes fourchettes
4. **Backtest** : Devrait montrer une amélioration significative (predicted ≈ actual)

### Questions pour Yohan

Avant d'implémenter, Yohan doit valider ces points :
1. Est-ce que cette approche correspond à ta vision du modèle ?
2. Pour les penalties : est-ce qu'on a un moyen fiable de savoir qui est le tireur de penalties par équipe ? (config manuelle ? donnée automatique ?)
3. Y a-t-il des joueurs ou matchs spécifiques sur lesquels tu voudrais tester le modèle ?
4. Le calculateur frontend doit-il aussi afficher les assists dans le même tableau ou dans un onglet séparé ?

---

## Implémentation (pour l'agent uniquement)

### Architecture

```
Stage 1: Team Match xG
  team_match_xG = attack_strength × defense_weakness × home_factor × league_avg

  attack_strength = team_avg_xG / league_avg_xG
  defense_weakness = opponent_avg_xGA / league_avg_xGA
  home_factor = 1.22 (home) ou 1.0 (away)

  Données sources:
    team_avg_xG = SUM(PlayerStats.xg WHERE team) / team_matches
    team_avg_xGA = buts encaissés depuis Fixtures terminés
    league_avg = calculé à la volée

Stage 2: Player Allocation (Open Play + Penalties)
  lambda_open_play = team_xG × (1-pen_ratio) × player_npxg_share × (mins/90)
  lambda_penalty = 0.78 × pens_per_match × is_pen_taker
  lambda_total = lambda_open_play + lambda_penalty
  P(score) = 1 - exp(-lambda_total)

  Bayesian shrinkage si matches < 30:
    share = factor × actual + (1-factor) × position_prior
    factor = min(matches/30, 1.0)

Stage 2b: Assist (Smart Weights par profil)
  xa_share = player.xa / team_total_xa
  creation_score = Σ(weight × stat/league_avg) par profil (MF/W/FW)
  lambda_assist = team_xG × xa_share × clamp(creation_score, 0.5, 2.0) × (mins/90)
```

### Fichiers à modifier/créer

| Fichier | Action |
|---------|--------|
| `app/pricing/team_xg.py` | **NOUVEAU** : estimation team xG, allocation joueurs, data loading |
| `app/pricing/goalscorer.py` | **RÉÉCRITURE** : remplacer par Top-Down open play + penalties |
| `app/pricing/assist.py` | **RÉÉCRITURE** : remplacer par Top-Down Smart Weights |
| `app/api/pricing.py` | Ajouter endpoint `POST /api/v1/price/match` |
| `app/services/recommendation_service.py` | Utiliser `load_match_pricing()` au lieu de l'ancien pipeline |
| `app/backtest/simulator.py` | Utiliser le même pipeline Top-Down |
| `frontend/src/app/dashboard/calculator/page.tsx` | **RÉÉCRITURE** : sélecteur match → tableau joueurs |
| `frontend/src/lib/api.ts` | Ajouter `priceMatch()` + types TypeScript |
| `tests/test_team_xg.py` | **NOUVEAU** : tests estimation team xG |
| `tests/test_pricing_goalscorer.py` | Adapter à la nouvelle signature |
| `tests/test_pricing_assist.py` | Adapter / nouveau |

### Step 1 — `app/pricing/team_xg.py` (nouveau)

Dataclasses : `TeamMatchEstimate`, `PlayerAllocation`

Fonctions :
- `compute_team_stats(db, league, season) -> dict[team, stats]`
  - Calcule avg xG et avg xGA pour chaque équipe
  - xG depuis `PlayerStats`, xGA depuis `Fixture` résultats
- `estimate_team_match_xg(attack_strength, defense_weakness, league_avg, is_home) -> float`
  - Formule Dixon-Coles simplifiée
- `compute_player_shares(players, team, team_total_npxg, team_total_xa) -> list[PlayerShare]`
  - npxg_share et xa_share par joueur, avec Bayesian shrinkage
- `allocate_to_players(team_xg, shares, pen_taker) -> list[PlayerAllocation]`
  - Calcule lambda open play + penalty pour chaque joueur
- `load_match_pricing(db, fixture, home_xg_override, away_xg_override) -> MatchPricingResult`
  - Fonction d'orchestration : charge tout, estime, distribue

### Step 2 — `app/pricing/goalscorer.py` (réécriture)

Nouvelle signature :
```python
def calculate_goalscorer_price(
    team_match_xg: float,
    player_npxg_share: float,
    expected_minutes: float = 90.0,
    is_penalty_taker: bool = False,
    team_pen_ratio: float = 0.10,
    expected_pens_per_match: float = 0.10,
) -> GoalscorerPriceResult
```

Garder : `GoalscorerPriceResult`, `calculate_edge()`, `remove_margin()`, `_interpret_probability()`
Supprimer : l'ancienne logique (conversion_rate, opponent_xga_factor, form_factor)

### Step 3 — `app/pricing/assist.py` (réécriture)

Nouvelle signature :
```python
def calculate_assist_price(
    team_match_xg: float,
    player_xa_share: float,
    creation_score: float,
    expected_minutes: float = 90.0,
) -> AssistPriceResult
```

Profils Smart Weights (depuis docs/03-modeling.md) :
- Créateur Axial (MF/AM) : xA 40%, KP 30%, SCA 20%, PPA 10%
- Ailier/Latéral (W/FB) : xA 35%, KP 20%, SCA 10%, PPA 10%, Crosses 25%
- Attaquant (FW) : xA 50%, KP 20%, SCA 30%

### Step 4 — `app/api/pricing.py` (ajouter endpoint)

`POST /api/v1/price/match` — orchestre `load_match_pricing()` et retourne la liste complète.

Request : `{ fixture_id, home_xg_override?, away_xg_override? }`
Response : `{ fixture, home_match_xg, away_match_xg, breakdowns, players[] }`

### Step 5 — `app/services/recommendation_service.py`

Remplacer :
- `_compute_team_strengths()` → supprimer
- `_get_opponent_factor()` → supprimer
- Utiliser `load_match_pricing(db, fixture)` à la place

### Step 6 — `app/backtest/simulator.py`

Adapter `simulate_historical()` pour utiliser le pipeline Top-Down.
Attention au leakage : utiliser uniquement les données d'avant chaque match.

### Step 7 — Frontend `calculator/page.tsx` (réécriture complète)

- Sélecteur de match (dropdown fixtures)
- Cartes Team xG (home/away) avec override input
- Tableau joueurs avec colonnes : Joueur, Équipe, Pos, Share, λ, Proba, Cote Ev0
- Toggle Buteur / Passeur
- Colonne cote bookmaker optionnelle → affiche edge

### Step 8 — `frontend/src/lib/api.ts`

Ajouter `priceMatch(fixture_id, home_xg_override?, away_xg_override?) -> MatchPriceResponse`

### Step 9 — Tests

```bash
uv run pytest tests/ -x -q  # tous les tests passent
uv run ruff check .          # clean
```

Tests à écrire :
- `test_team_xg.py` : estimation Dixon-Coles, home advantage, player shares, shrinkage
- Adapter `test_pricing_goalscorer.py` : nouvelle signature, pen taker, typical values
- Adapter `test_pricing_assist.py` : Smart Weights, creation_score clamp

### Vérification finale

1. Appeler `POST /api/v1/price/match {"fixture_id": <upcoming>}` → vérifier fourchettes :
   - FW top : 30-50%, cotes 2.0-3.3
   - MF : 8-15%, cotes 6.5-12.0
   - DF : 3-6%, cotes 17-33
2. Tester avec override : `home_xg_override: 3.0` → probas augmentent proportionnellement
3. Frontend : sélectionner un match → voir tableau complet
4. Relancer backtest → calibration améliorée

---

## Références documentaires (lire avant de coder)

- `docs/01-architecture.md` : Structure des modules (Pricing Engine = match xG → player allocation)
- `docs/03-modeling.md` : Mode Top-Down, Poisson mixte Open Play + Penalties, Smart Weights assists
- `docs/06-strategy.md` : Phases Early/Lineup, filtres edge, stake sizing
- `docs/18-data-contracts.md` : Schéma PricingOutput avec `team_xg_override`, `lambda_open_play`, `lambda_penalty`
- `docs/data-dictionary.md` : Définitions npxG, xA, Key Passes, SCA
