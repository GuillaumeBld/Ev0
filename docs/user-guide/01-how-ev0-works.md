# Comment Ev0 calcule

## Le principe : valeur attendue (Expected Value)

Ev0 cherche des paris à **valeur positive (+EV)** : des paris où notre estimation de la probabilité réelle est plus haute que celle implicite dans la cote du bookmaker.

### Étape 1 — Probabilité "fair"

Pour chaque joueur et chaque match, Ev0 calcule une probabilité "fair" qu'il marque (buteur) ou fasse une passe décisive (passeur) :

- **Module buteur** : basé sur xG/90min, minutes attendues, poste, forme récente (5 matchs)
- **Module passeur** : basé sur xA/90min, occasions créées, centres, passes clés

### Étape 2 — Cote "fair"

```
fair_odds = 1 / fair_probability
```

### Étape 3 — Edge

```
edge = (fair_probability - market_probability) / market_probability
```

Un edge > 0.05 (5%) est considéré comme une opportunité de valeur.

### Étape 4 — Fraction de Kelly

La mise optimale selon le critère de Kelly :

```
kelly_fraction = edge / (market_odds - 1)
stake = bankroll × kelly_fraction × kelly_multiplier
```

Le `kelly_multiplier` est entre 0.25 et 1.0 pour limiter la variance.

## Sources de probabilité

| Signal | Module | Source |
|--------|--------|--------|
| xG/90min | Buteur | FBref / Understat |
| xA/90min | Passeur | FBref / Understat |
| Minutes attendues | Les deux | Modèle interne (historique) |
| Forme récente | Les deux | 5 derniers matchs, pondérés |
| Intensité lambda | Buteur | Poisson process (λ = xG × mins/90) |
