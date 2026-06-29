# Modélisation — Pricing joueur (état actuel)

## Architecture globale

```
Team Match xG (MarketXgService)
    ↓
compute_player_shares()          — shares npxG / xA par joueur (form-blended)
    ↓
allocate_player()                — lambda buteur + lambda passeur par joueur
    ↓
calculate_supersub_prob()        — P(marquer/passer avec mécanique supersub)
    ↓
PlayerAllocationOut              — réponse API pricing
```

---

## Stage 1 — Team Match xG (MarketXgService)

Le xG d'équipe est la base de tout le pricing. Trois sources possibles, par priorité :

| Source | `xg_source` | Condition |
|--------|-------------|-----------|
| Bzzoiro live | `"bzzoiro"` | Cotes WDW scrappées sur Bzzoiro disponibles et récentes |
| Market-implied (OddsPortal/books) | `"market_implied"` | Odds snapshots disponibles |
| Override manuel | `"override"` | Opérateur a saisi un xG dans le calculateur |

Si les cotes Bzzoiro existent mais sont trop anciennes, la source bascule sur `"market_implied_flagged"`.

---

## Stage 2 — Shares joueurs (`compute_player_shares`)

Pour chaque joueur, le share est calculé à partir d'un **xG/xA form-blendé** :

```
blended_xg = (1 - form_w) × xg_per_90 + form_w × form_rate
goal_weight = blended_xg × (expected_minutes / 90)
npxg_share  = goal_weight / max(Σ goal_weights, λ_team)
```

`form_w` varie par position (`FORM_WEIGHTS_BY_POSITION`) :
- FW : 0.25, MF : 0.20, DF : 0.12, GK : 0.05, défaut : 0.18

`form_rate` = buts des 5 derniers matchs / (5 × minutes/90).
Si historique insuffisant (<5 matchs récents), `form_w = 0`.

Le même schéma s'applique pour le share passeur (`xa_share` / `blended_xa`).

**Discount set-pièce défenseur (`DF_SETPIECE_DISCOUNT = 0.55`)**
Pour les DF, le `goal_weight` est multiplié par 0.55 pour éviter la surestimation des buts sur coup de pied arrêté.

---

## Stage 3 — Lambda buteur (`allocate_player`)

```
finishing_mult = calculate_finishing_multiplier(stats, position)
lambda_open_play = npxg_share × team_xg × finishing_mult × conversion_rate
lambda_penalty   = pen_rate × lambda_pen_team   (0 si non-tireur)
lambda_total     = clamp(lambda_open_play + lambda_penalty, 0.001, 3.0)
```

### Finishing multiplier (calibration 11 buckets)

Calibré sur Bzzoiro 2024-25 (≥450 min, Big5 + UCL). Normalise les stats de finition par rapport à la moyenne de position.

Clamps par position (plancher, plafond) :

| Position | Plancher | Plafond |
|----------|----------|---------|
| FW | 0.70 | 1.50 |
| AM | 0.55 | 1.35 |
| MF | 0.55 | 1.50 |
| CB | 0.30 | 1.10 |
| DF | 0.30 | 1.30 |
| défaut | 0.50 | 1.40 |

Les planchers bas pour DF/CB évitent d'écraser à zéro des défenseurs avec peu de stats de tir.

### Probabilité de but (Poisson)
```
prob_goal = 1 - e^(-lambda_total)
fair_odds_goal = 1 / prob_goal
```

---

## Stage 4 — Lambda passeur (`calculate_assist_lambda`)

```
ASSIST_GOAL_RATE = 0.72   # ~72% des buts ont une passe décisive
budget_assists = team_xg × ASSIST_GOAL_RATE
lambda_assist = xa_share × budget_assists × creation_mult × xa_conversion
```

`creation_mult` : multiplicateur basé sur les Key Passes / SCA par rapport à la moyenne de position.

`xa_conversion` : ratio passes décisives réelles / xA (si ≥5 matchs), clamped par position.

---

## Stage 5 — Modèle Supersub

Le marché "supersub" cible les joueurs susceptibles d'entrer en cours de match.

### Formule P(supersub buteur)

```
P = (1 - p_sub) × (1 - e^(-λ_A))
  + p_sub       × (1 - e^(-(λ_A × t_sub/90 + λ_B × (90 - t_sub) / 90)))
```

- `λ_A` = lambda_total du joueur en tant que titulaire
- `λ_B` = xG/90 moyen d'un remplaçant entrant à sa position (constante calibrée)
- `p_sub` = probabilité d'être remplaçant (historique joueur, ou défaut par position)
- `t_sub` = minute d'entrée estimée (historique ou T_SUB_DEFAULT = 65')

### Constantes `λ_B` (SUB_GOAL_LAMBDA / SUB_ASSIST_LAMBDA)

Calibrées sur L1/PL 2023-2026 :

| Position | λ_B but | λ_B passe |
|----------|---------|-----------|
| FW | 0.18 | 0.07 |
| MF | 0.08 | 0.10 |
| DF | 0.02 | 0.02 |
| GK | 0.00 | 0.00 |

### Défauts `p_sub` (P_SUB_DEFAULT, si <5 matchs historique)

| Position | p_sub défaut |
|----------|-------------|
| FW | 0.45 |
| MF | 0.40 |
| DF | 0.25 |
| GK | 0.02 |

---

## Stage 6 — Override compo (redistribution sur titulaires)

Si l'opérateur fournit une liste de titulaires (`home_starters` / `away_starters`) :
- Les shares sont recalculés uniquement sur ces joueurs
- Les minutes des non-titulaires passent à 0
- Le résultat est retourné dans `home_lineup_players` / `away_lineup_players` (parallèle à la réponse complète)

---

## Pricing CDM 2026 (`wc2026_player_pricing`)

Module séparé pour les marchés outright CDM (meilleur buteur, meilleur passeur).

```
e_games = wc2026_team_advancement.e_games   (simulation Monte Carlo ELO)
λ_joueur_CDM = Σ_matchs [ P(match joué) × λ_équipe × share_joueur × (mins/90) ]
```

`P(match joué)` est issu des probabilités de `wc2026_team_advancement` (p_r32, p_r16, p_qf, p_sf, p_finalist).

Monte Carlo 10 000 simulations → P(joueur X est meilleur buteur du tournoi) → cote fair.

Recalculé automatiquement après chaque run bracket (`job_sync_wc_bracket`).

---

## Paramètres retirés

- `matchup_factor` — retiré (complexity non-justifiée sur les données disponibles)
- `corners` — retiré (modèle corners abandonné)
