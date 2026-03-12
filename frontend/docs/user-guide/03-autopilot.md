# Autopilot — Agent RL

## Principe

L'Autopilot est un agent de Q-learning linéaire (sans deep learning) qui apprend quels paris prendre et à quelle fraction de Kelly, en optimisant le P&L à long terme.

## Architecture

**Modèle** : Q-learning linéaire bandit (γ=0, pas de lookahead)

```
Q(s, a) = W[a] · features
```

- `W` : matrice de poids 4 × 13 (4 actions × 13 features)
- `features` : vecteur d'état à 13 dimensions normalisées

**Mise à jour** :
```
W[a] += alpha × (reward - Q(s, a)) × features
```

**Récompense** : `pnl / bankroll` (scale-invariant)

## Features (état)

| # | Feature | Description |
|---|---------|-------------|
| 0 | edge | Edge calculé par Ev0 |
| 1 | confidence | Score de confiance interne |
| 2 | implied_prob | 1 / market_odds |
| 3 | fair_prob | Probabilité fair Ev0 |
| 4 | lambda | Intensité Poisson |
| 5 | mins_ratio | Minutes attendues / 90 |
| 6 | is_goalscorer | 1 si marché buteur |
| 7 | is_premier_league | 1 si Premier League |
| 8 | is_forward | 1 si le joueur est attaquant |
| 9 | edge_rank | Rang de l'edge parmi les recs du jour (0=pire, 1=meilleur) |
| 10 | n_recs_today | Nombre de recs du jour / 10 (plus il y en a, plus l'agent est sélectif) |
| 11 | odds_movement | Variation de cote depuis le 1er snapshot (positif = cote a baissé = steam) |
| 12 | bias | Toujours 1.0 |

## Actions

| Index | Action | Kelly multiplier |
|-------|--------|-----------------|
| 0 | skip | 0.0× (ne pas parier) |
| 1 | quarter_kelly | 0.25× |
| 2 | half_kelly | 0.5× |
| 3 | kelly | 1.0× |

## Entraînement

1. **Pré-entraînement** : sur les données de backtest (saison 2024-2025), tri chronologique strict pour éviter le data leakage. ε-greedy avec décroissance 1.0 → 0.1.
2. **Fine-tuning** : sur les décisions paper trade settlées en base. Se déclenche automatiquement après 10+ nouvelles décisions settlées.

Poids persistés dans `backend/app/autopilot/weights/agent.npz`.

## Mode paper vs live

- **Paper** : l'agent décide, on enregistre la décision, mais aucun pari réel n'est placé.
- **Live** : les décisions sont transmises pour exécution réelle. **Non implémenté encore.**

## Jobs automatiques

| Job | Fréquence | Description |
|-----|-----------|-------------|
| autopilot_run | Toutes les 2h | Analyse les recs du jour, crée des AutopilotDecision |
| autopilot_settle | Quotidien 09:00 UTC | Settle les paper trades sur les matchs terminés |

## Métriques de calibration

- **Brier Score** : erreur quadratique moyenne des probabilités prédites vs résultat réel. Score < 0.25 = mieux que pile/face.
- **Calibration plot** : les probabilités prédites sont-elles fiables ? Par bucket de probabilité, on compare la prédiction moyenne au taux de victoire réel.
- **Reward shaping** : l'agent reçoit une récompense positive (+0.02) quand il skip un pari qui aurait perdu, et une pénalité (-0.01) quand il skip un pari qui aurait gagné.
