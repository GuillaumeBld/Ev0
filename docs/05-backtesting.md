# Backtesting Scientifique

## Objectif
Valider l'avantage mathématique (Edge) du modèle avec une rigueur statistique suffisante pour rejeter l'hypothèse nulle (Hasard).

---

## 1. Principes d'Intégrité (The Iron Rules)

### Règle n°1 : No Look-Ahead Bias
Toute donnée utilisée pour une prédiction à `T` doit avoir un timestamp `ingested_at < T`.
*   *Vérification* : Le framework de backtest doit interroger la DB avec une clause `WHERE as_of_utc <= bet_timestamp`.

### Règle n°2 : Closing Line Value (CLV) Proxy
Pour le backtest historique, on ne peut pas toujours parier à l'ouverture.
*   **Proxy Conservateur** : On utilise la cote de fermeture (Closing Line) pour évaluer la performance brute. Si on bat la Closing Line, on bat le marché.
*   **Proxy Réaliste** : On utilise la cote moyenne enregistrée à `H-12` (si disponible).

---

## 2. Protocole de Validation Statistique

Un ROI positif n'est pas suffisant. Il doit être **significatif**.

### Test de Significativité (T-Test)
Pour chaque stratégie, on calcule la *t-statistic* du Yield.

Formule :
$$ t = \frac{ROI \times \sqrt{N}}{\sigma} $$

Où :
*   $ROI$ = Retour sur investissement moyen
*   $N$ = Nombre de paris
*   $\sigma$ = Écart-type des rendements (Standard Deviation)

### Seuils d'Acceptation
| Métrique | Seuil Minimum | Commentaire |
|----------|---------------|-------------|
| **N (Sample Size)** | > 500 paris | En dessous, le bruit domine. |
| **P-Value** | < 0.05 | 95% de confiance que ce n'est pas de la chance. |
| **Brier Score** | < 0.23 | Mesure la calibration des probabilités. |

---

## 3. Walk-Forward Validation (Fenêtre Glissante)

Ne jamais entraîner sur 2024 et tester sur 2024.

**Méthodologie :**
1.  **Train** : Semaines 1 à 10.
2.  **Test** : Semaine 11.
3.  **Expand** : Train devient Semaines 1 à 11.
4.  **Test** : Semaine 12.
5.  *Répéter jusqu'à fin de saison.*

Cette méthode simule la réalité de l'accumulation de connaissances.

---

## 4. Analyse des Échecs (Failure Analysis)

Tout backtest doit produire un rapport de "Pires Erreurs" :
1.  Top 10 des pertes (plus grosse variance négative).
2.  Analyse des paris perdus avec Edge > 10% (Le modèle a-t-il raté une info blessure ?).
3.  **Drawdown Max** : Quelle a été la pire chute de bankroll cumulée ?

## 5. Rapport de Sortie Standardisé

Chaque run de backtest doit générer ce JSON :

```json
{
  "run_id": "bt_20260130_v1",
  "config": { "min_edge": 0.05, "model": "poisson_npxg" },
  "stats": {
    "total_bets": 850,
    "roi": 0.062,
    "profit_units": 52.7,
    "win_rate": 0.34,
    "avg_odds": 3.45
  },
  "significance": {
    "t_score": 2.1,
    "p_value": 0.035,
    "is_significant": true
  },
  "risk": {
    "max_drawdown_units": -18.5,
    "max_losing_streak": 9
  }
}
```