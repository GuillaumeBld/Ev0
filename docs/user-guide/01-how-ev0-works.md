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

## xG au niveau du match : solveur Poisson market-implied

Avant d'allouer le xG aux joueurs, Ev0 estime les espérances de buts des deux équipes (`λ_home`, `λ_away`) via un solveur Poisson à 3 contraintes (L-BFGS-B). Dixon-Coles a été supprimé — le modèle ne fonctionne désormais que si des odds de marché récents sont disponibles.

### Pipeline de collecte d'odds

Le worker scrape les odds toutes les 15 secondes via une chaîne de fallback :

1. **OddsPortal** (Playwright) — source primaire ; CSS selectors à vérifier sur site en production
2. **Betclic** (HTTP, `__NEXT_DATA__` SSR JSON) — 1er fallback si OddsPortal échoue
3. **Unibet** (HTTP, LVS/kambicdn API) — 2e fallback

3 marchés sont collectés par scrape : **H2H** (1×2), **Over/Under 2.5**, **BTTS**. Chaque snapshot est stocké dans `match_odds_snapshots` avec les colonnes `source`, `source_url`, `parse_version`, `fallback_used`.

Le planning de scrape est géré par un token-bucket adaptatif (`MarketScrapeScheduler`) :
- Intervalles : >24h→120min, 6-24h→60min, 2-6h→20min, 30min-2h→7min, 5-30min→3min, ≤5min→stop
- RPM dynamique : 1.0 (idle), 2.0 (quelques matches), 3.0 (file chargée), jusqu'à 5.0 (pression pre-KO)
- Backoff automatique (÷2, gel 20min) sur erreur persistante ; récupération progressive (+0.25 toutes les 10min)

### Solveur Poisson

Après chaque scrape réussi, le service `MarketXgService` dé-viguise les cotes, puis minimise :

```
residual = Σ (P_poisson(i) - p_market(i))²  pour i ∈ {home_win, draw, over_2.5, btts}
```

avec les bornes λ ∈ [0.05, 4.5] et warm start via `brentq` sur Over 2.5.

Si BTTS n'est pas disponible, le solveur se replie sur 2 contraintes (H2H + O/U).

### Sources et staleness

- Un snapshot est considéré **périmé** après **3 h** (absolu, indépendamment du coup d'envoi)
- Si aucun snapshot récent n'est disponible, `compute()` retourne `None` → la recommandation est ignorée
- `xg_source` dans la réponse API indique la source d'odds : `"oddsportal"`, `"betclic"` ou `"unibet"`
- `flagged=True` si le résiduel du solveur dépasse 0.06 (marchés contradictoires)
- Le badge frontend est vert (OddsPortal) ou orange (fallback Betclic/Unibet)

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
