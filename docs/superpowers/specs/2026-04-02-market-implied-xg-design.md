# Market-Implied xG Design

## Goal

Remplacer le modèle Dixon-Coles par une estimation de xG directement dérivée des cotes du marché betting, pour aligner le pricing des joueurs sur le consensus des "sharks".

---

## Motivation

Dixon-Coles diverge du marché sur des matchs atypiques → génère de fausses values → pertes réelles. En inférant les xG depuis les cotes, on pense comme le marché et on évite les désaccords systématiques avec les bookmakers sharp.

---

## Prérequis — Nouveau schéma et ingestion (REQUIS avant MarketXgService)

Les cotes actuellement stockées dans `odds_snapshots` sont **uniquement des player props** (goalscorer, assist). Les marchés nécessaires à ce feature (Over 2.5, BTTS, H2H) ne sont **pas encore fetchés ni stockés**.

Il faut donc :

### Nouvelle table `match_odds_snapshots`

```sql
CREATE TABLE match_odds_snapshots (
    id            SERIAL PRIMARY KEY,
    fixture_id    INTEGER NOT NULL REFERENCES fixtures(id),
    bookmaker     VARCHAR NOT NULL,
    market_type   VARCHAR NOT NULL,   -- 'h2h' | 'totals' | 'btts'
    outcome       VARCHAR NOT NULL,   -- 'home' | 'draw' | 'away' | 'over_2.5' | 'under_2.5' | 'yes' | 'no'
    odds          FLOAT NOT NULL,
    snapshot_utc  TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE(fixture_id, bookmaker, market_type, outcome, snapshot_utc)
);
```

### Nouvelle ingestion dans le worker

Endpoint The Odds API : `GET /v4/sports/soccer_*/events/{event_id}/odds?markets=h2h,totals,both_teams_to_score&bookmakers=betfair,pinnacle`

Logique :
- Fetch pour chaque fixture upcoming (kickoff dans les 7 prochains jours)
- Filtrer les bookmakers : `betfair` en priorité, `pinnacle` en fallback
- Upsert dans `match_odds_snapshots` (insert, ne pas écraser les snapshots passés)
- Cadence : toutes les heures (même job que odds player props, ajout d'un appel séparé)

---

## Marchés utilisés

| Marché | API key | Utilité | Marge |
|--------|---------|---------|-------|
| Match Totals Over 2.5 | `totals` | Contrainte λt = λh + λa | Légère asymétrie (Under = outsider) |
| BTTS Yes/No | `both_teams_to_score` | Séparer λh de λa | ~50/50 → marge symétrique |
| H2H (1X2) | `h2h` | Identifier la plus forte équipe | Asymétrique — outsider plus margé |

**Source préférée** : Betfair Exchange mid-price (pas d'overround bookmaker, spread ~2% symétrique). Fallback : Pinnacle (overround ~2-3%).

**Fraîcheur** : rejeter tout snapshot plus vieux que 24h avant kickoff. Si seules des cotes stale sont disponibles → fallback Dixon-Coles.

---

## Devigging

**Méthode** : multiplicatif pour tous les marchés.

```
P_true(side_i) = (1/O_i) / Σ(1/O_j)
```

**Justification par marché :**
- BTTS (~50/50) : marge symétrique → devig multiplicatif sans biais.
- Over 2.5 (~60-65% Over) : Over est le favori léger, Under légèrement plus margé → utiliser le **côté Over uniquement** pour dériver λt.
- H2H : outsider clairement plus margé, mais H2H sert uniquement à **détecter le signe** (λh > λa), pas les valeurs absolues → biais non critique.

---

## Pipeline de calcul

### Étape 1 — Dériver λt depuis Over 2.5

```
P(total ≥ 3) = 1 - e^(-λt) × (1 + λt + λt²/2)
```

Résoudre numériquement (`scipy.optimize.brentq`, λt ∈ [0.1, 10]) pour trouver λt tel que l'équation = P_true(Over 2.5).

### Étape 2 — Séparer λh et λa via BTTS

Avec λa = λt - λh :

```
P(BTTS Yes) = (1 - e^(-λh)) × (1 - e^(-(λt - λh)))
```

Résoudre en 1D pour λh ∈ [0.05, λt - 0.05] via `brentq`. Deux solutions possibles (λh_1, λt - λh_1) — symétrique autour de λt/2.

### Étape 3 — Résoudre l'ambiguïté avec H2H

Si P_true(home win) > P_true(away win) → λh = max(λh_1, λt - λh_1).
Sinon → λh = min(λh_1, λt - λh_1).
λa = λt - λh.

### Étape 4 — Cross-validation

Depuis (λh, λa), recalculer les probabilités de marché :

**P(BTTS)** : `(1 - e^(-λh)) × (1 - e^(-λa))`

**P(Over 2.5)** : `1 - e^(-λt) × (1 + λt + λt²/2)`

**P(draw)** (somme sur k=0..5, erreur de troncation ~0.1% pour λ < 3) :
```
P(draw) = Σ_{k=0}^{5} [ e^(-λh) × λh^k / k! ] × [ e^(-λa) × λa^k / k! ]
```

Comparer chaque P_pred avec P_true du marché. Si **écart absolu > 8%** sur l'un des marchés → flag `market_implied_flagged`.

> Le seuil 8% est une valeur initiale à calibrer sur données historiques. Il représente le bruit de dévigging attendu sur les marchés européens pour des cotes Betfair/Pinnacle. Le tracker `xg_source` permettra d'analyser rétrospectivement les cas flaggés et d'ajuster ce seuil.

---

## Type de retour de `compute()`

La méthode retourne un objet typé pour exprimer les trois états distincts :

```python
@dataclass
class MarketXgResult:
    xg_home: float
    xg_away: float
    xg_source: Literal["market_implied", "market_implied_flagged", "dixon_coles"]
    flagged_reason: str | None = None
```

`compute()` retourne toujours un `MarketXgResult`. Si les cotes sont insuffisantes ou le solver échoue → `xg_source = "dixon_coles"` avec les valeurs Dixon-Coles en fallback.

---

## Intégration dans le pipeline existant

### Fichiers à modifier

1. **Nouveau** : `backend/app/services/market_xg.py`
   Classe `MarketXgService` avec méthode `compute(fixture_id, session) -> MarketXgResult`.

2. **Modifier** : `backend/app/pricing/team_xg.py` — fonction `load_match_pricing()`
   Remplacer l'appel à `estimate_team_match_xg()` (Dixon-Coles) par `MarketXgService.compute()`.
   Utiliser le fallback si `result.xg_source == "dixon_coles"`.

3. **Modifier** : `backend/app/pricing/team_xg.py` — dataclass `MatchPricingResult`
   Ajouter `xg_source: str = "dixon_coles"`.

4. **Modifier** : `backend/app/api/pricing.py` — `MatchPriceResponse`
   Ajouter `xg_source: str`.

---

## Gestion des erreurs

| Cas | Comportement |
|-----|-------------|
| Cotes manquantes (< 2 marchés disponibles) | Fallback Dixon-Coles, `xg_source = "dixon_coles"` |
| Snapshot trop vieux (> 24h avant kickoff) | Fallback Dixon-Coles, `xg_source = "dixon_coles"` |
| Solver Over 2.5 ne converge pas | Fallback Dixon-Coles |
| BTTS solver : pas de racine dans [0.05, λt-0.05] (cas dégénéré λt bas + BTTS élevé) | Fallback Dixon-Coles |
| Cross-validation : écart > 8% | `xg_source = "market_implied_flagged"`, valeurs utilisées quand même, warning dans la rec |
| λh ou λa < 0.05 après solver | Clamp à 0.05 |

---

## Tests

- **Unit** : solver Over 2.5 (valeurs connues), solver BTTS, sélection H2H (cas home > away et inverse), cross-validation (cas valide + cas flaggé + cas no-root BTTS).
- **Integration** : `MarketXgService.compute()` avec cotes mockées dans `match_odds_snapshots` → vérifie (λh, λa, xg_source) attendus.
- **Ingestion** : worker fetche correctement les 3 marchés et insère dans `match_odds_snapshots`.

---

## Non-inclus dans ce scope

- Rétro-calcul des xG sur les fixtures historiques.
- UI exposant `xg_source` dans le frontend (le champ est dispo dans l'API mais pas affiché).
- Marché Asian Handicap (non disponible de façon fiable pour le soccer européen sur The Odds API).
- Shin devig pour H2H (non nécessaire car H2H sert uniquement pour le signe).
