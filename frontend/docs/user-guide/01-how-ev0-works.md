# Comment Ev0 calcule

## Le principe : valeur attendue (Expected Value)

Ev0 cherche des paris à **valeur positive (+EV)** : des situations où la probabilité qu'un joueur marque (ou fasse une passe décisive) est plus élevée que celle que le bookmaker lui attribue implicitement via sa cote.

```
edge = (market_odds / fair_odds) - 1
```

Un edge > 5 % est considéré comme une opportunité de valeur.

---

## Le modèle top-down (v2)

Le moteur de pricing est **entièrement top-down** : on part du xG estimé de l'équipe pour le match, puis on redistribue ce budget aux joueurs proportionnellement à leur contribution historique.

### Étape 1 — xG de l'équipe pour le match

Le xG de chaque équipe (`λ_home`, `λ_away`) est calculé à partir des **cotes bookmaker** (Over/Under 2.5, H2H 1×2, BTTS) via un solveur Poisson L-BFGS-B à 4 contraintes. Ce sont des λ market-implied — ils reflètent ce que le marché anticipe pour ce match précis.

Si les 4 marchés sont disponibles, le solveur utilise : victoire domicile, nul, over 2.5, BTTS.  
Si BTTS est absent, il se replie sur 2 contraintes : over 2.5 + H2H.

Un snapshot est considéré **périmé après 4 h**. Si aucun snapshot récent n'est disponible pour un match, le pricing n'est pas calculé.

### Étape 2 — Parts des joueurs (player shares)

Pour chaque joueur, on calcule un **poids form-blended** :

```
blended_xg = 0.60 × xg_per_90 (saison) + 0.40 × form_xg_5 (5 derniers matchs)
```

La part de chaque joueur dans le budget de l'équipe est proportionnelle à ce poids blendé. Si les données de forme sont absentes, on utilise uniquement le xG saison.

Le budget passeur décisif est calculé comme :

```
budget_assists = λ_team × 0.65
```

(environ 65 % des buts ont une passe décisive officielle)

### Étape 3 — Pricing joueur

**Module buteur :**

```
λ_goal = npxg_share × λ_team × finishing_multiplier × conversion_rate
```

- `finishing_multiplier` : qualité de finition du joueur normalisée par la moyenne de sa position (FW / MF / DF). Un attaquant moyen donne ≈ 1.0.
  - Métriques : `shot_accuracy × 0.40 + xg_per_shot × 0.40 + rating × 0.20`
- `conversion_rate` : ratio buts / xG cumulé sur la saison, bridé à [0.75, 1.40]

**Module passeur décisif :**

```
λ_assist = xa_share × budget_assists × creation_multiplier × xa_conversion
```

- `creation_multiplier` : profil de création normalisé par position et profil (côté / axial / hybride)
  - Métriques : `xa_per_90 × 0.40 + key_pass_per_90 × 0.35 + accurate_cross_per_90 × 0.25`
- `xa_conversion` : ratio passes déc. réelles / xA cumulé, bridé à [0.75, 1.30]

**Conversion en probabilité et cote fair :**

```
P(score ≥ 1) = 1 - e^(-λ)
fair_odds = 1 / P
```

---

## Comparaison avec la cote bookmaker

Pour chaque joueur présent dans le marché buteur ou passeur du bookmaker, Ev0 compare sa cote fair à la cote proposée :

```
edge = (market_odds / fair_odds) - 1
```

| Edge | Classification |
|------|---------------|
| ≥ 5 % | **VALUE** — opportunité identifiée |
| 0 % à 5 % | **NO_VALUE** — légèrement sous-coté, pas recommandé |
| < 0 % | **AVOID** — surcôté |

---

## Sources de données

| Donnée | Source | Table DB |
|--------|--------|----------|
| Stats joueurs (xG/90, xA/90, rating…) | Bzzoiro API | `bzz_player_season_stats` |
| Forme récente (5 derniers matchs) | Bzzoiro API | `bzz_player_match_stats` |
| Cotes match (H2H, O/U, BTTS) | OddsPortal → Betclic → Unibet | `match_odds_snapshots` |
| Cotes joueurs (buteur / passeur) | Betclic, Unibet | `player_odds_snapshots` |
| Fixtures | FotMob (backfill) + The Odds API | `fixtures` |
