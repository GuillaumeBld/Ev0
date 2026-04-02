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

## xG au niveau du match : odds de marché vs Dixon-Coles

Avant d'allouer le xG aux joueurs, Ev0 estime les espérances de buts des deux équipes (`home_match_xg`, `away_match_xg`). Depuis la version market-implied-xG, cette estimation suit un ordre de priorité :

1. **Market-implied** (source : `market_implied`) — Ev0 charge le dernier snapshot d'odds bookmaker (Betfair > Pinnacle) pour la rencontre et résout λ_h + λ_a en inversant conjointement les marchés Over 2.5, BTTS et H2H via un système d'équations Poisson. C'est la méthode la plus précise car elle incorpore l'information agrégée du marché.
2. **Market-implied flagged** (source : `market_implied_flagged`) — Même pipeline, mais la cross-validation détecte une erreur > 8 % sur l'un des marchés. La valeur est utilisée mais signalée.
3. **Dixon-Coles** (source : `dixon_coles`) — Fallback activé quand aucun snapshot n'est disponible, que le snapshot est périmé (> 24 h avant le coup d'envoi), ou que les solveurs échouent.
4. **Override** (source : `override`) — L'appelant de l'API passe `home_xg_override` et/ou `away_xg_override`; ces valeurs sont utilisées directement.

Le champ `xg_source` est exposé dans la réponse de l'endpoint `POST /price/match` pour permettre à l'interface de signaler la provenance des cotes fair calculées.

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
