# Comment Ev0 calcule

## Le principe : valeur attendue (Expected Value)

Ev0 cherche des paris à **valeur positive (+EV)** : des paris où notre estimation de la probabilité réelle est plus haute que celle implicite dans la cote du bookmaker.

### Étape 1 — Probabilité "fair"

Pour chaque joueur et chaque match, Ev0 calcule une probabilité "fair" qu'il marque (buteur) ou fasse une passe décisive (passeur) via le **Modèle C** (Understat + Sofascore) :

- **Module buteur** : `λ = npxG/90 × (mins/90) × quality_multiplier × conversion_rate × opponent_factor × form`
  - Ancre : `npxG` (Understat — expected goals hors pénaltys)
  - Multiplicateur qualité : SOT×0.40 + TAP×0.35 + xGChain×0.25 (normalisé par moyenne de ligue)
- **Module passeur** : `λ = xA/90 × (mins/90) × creation_multiplier × opponent_factor × form`
  - Ancre : `xA` (Understat — expected assists)
  - Multiplicateur création : BCC×w + xGChain×w + Crosses×w + TB×w (poids adaptés au poste)

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
| npxG/90min (ancre buteur) | Buteur | Understat |
| xA/90min (ancre passeur) | Passeur | Understat |
| xGChain/90 | Buteur + Passeur | Understat |
| SOT/90, TAP/90 | Buteur (quality multiplier) | Sofascore |
| BCC/90, Crosses/90, TB/90 | Passeur (creation multiplier) | Sofascore |
| Minutes attendues | Les deux | Modèle interne (historique) |
| Forme récente | Les deux | Décroissance exponentielle |
| Intensité lambda | Les deux | Poisson process |
