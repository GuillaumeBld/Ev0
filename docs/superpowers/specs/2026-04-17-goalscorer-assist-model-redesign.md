# Redesign — Modèles Buteur & Passeur (Top-Down Allocation)

**Date :** 2026-04-17
**Statut :** Approuvé — prêt pour implémentation

---

## Contexte & problèmes résolus

### Bugs diagnostiqués dans le modèle actuel

1. **Quality multiplier jamais appelé** — `goalscorer.py` contient `calculate_quality_multiplier_bzz()` mais `recommendation_service.py` n'importe que `calculate_edge`. Le λ réel est `xg_per_90 × mins/90`, sans aucun ajustement.

2. **CALIBRATION_SCALE = 0.84 hardcodé** — patch empirique sur un modèle mal calibré. Réduit systématiquement toutes les probas, aggravant les cotes trop hautes (>10 pour des attaquants moyens).

3. **Pas d'ancrage au match** — chaque joueur a une probabilité calculée indépendamment de l'intensité offensive attendue du match. PSG vs Lens = même λ base que Strasbourg vs Metz pour un même joueur.

4. **Modèle passeur sous-développé** — copie du modèle buteur avec `xa` à la place de `xg`. Aucun multiplicateur de création. Aucune distinction de profil (ailier vs meneur axial).

5. **Champs Bzzoiro riches mais inutilisés** — `form_xg_5`, `form_assists_5`, `key_pass_per_90`, `accurate_cross_per_90`, `cross_accuracy`, `shot_accuracy`, `xg_per_shot`, `finishing_delta`, `xa_delta`, `avg_rating` présents en DB, jamais chargés.

---

## Architecture globale

```
MarketXgService → λ_home / λ_away (ancre marché, inchangé)
                        │
          ┌─────────────┴─────────────┐
          ▼                           ▼
   MODÈLE BUTEUR              MODÈLE PASSEUR
   (goalscorer.py)            (assist.py)
          │                           │
   share top-down              share top-down
   + form blend                + form blend
   + finishing quality         + création multiplier
   + pen taker                   (hybride poste+profil)
          │                           │
       λ_buteur                    λ_passeur
          └─────────────┬─────────────┘
                        ▼
              edge = market/fair - 1
              VALUE / NO_VALUE / AVOID
```

**Principes :**
- Ancre commune : `λ_team` issu du marché (MarketXgService, inchangé)
- Deux modules indépendants avec leurs propres stats et multiplicateurs
- `CALIBRATION_SCALE` supprimé — cohérence marché remplace correction empirique
- Tout joueur avec ≥1 match en DB est inclus (pas de seuil minimum)

---

## Modèle Buteur

### Formule

```python
# 1. Blend saison + forme
form_rate  = form_xg_5 / (5 × avg_mins / 90)   # α=1.0 si form_xg_5 absent
blended_xg = 0.60 × xg_per_90 + 0.40 × form_rate

# 2. Share top-down
weight_i     = blended_xg × (expected_minutes / 90)
denominator  = max(Σ weight_j for j in équipe_en_DB, λ_team)
share_i      = weight_i / denominator              # ∈ [0, 1]

# 3. Finishing multiplier (normalisé par poste, autour de 1.0)
finishing_mult = (
    normalize(shot_accuracy, position) × 0.40
  + normalize(xg_per_shot, position)  × 0.40
  + normalize(avg_rating / 10, position) × 0.20
)
finishing_mult = clamp(finishing_mult, 0.70, 1.50)

# 4. Conversion rate
conversion = clamp(goals / xg, 0.75, 1.40)   # si ≥5 matchs, sinon 1.0

# 5. Lambda
λ = share_i × λ_team × finishing_mult × conversion
if is_pen_taker:
    λ += PEN_CONVERSION × PENS_PER_MATCH × (expected_minutes / 90)

# 6. Probabilité
P         = 1 - exp(-λ)
fair_odds = 1 / P
```

### Moyennes de normalisation par poste (buteur)

```python
GOALSCORER_POSITION_AVGS = {
    "FW": {"shot_accuracy": 0.42, "xg_per_shot": 0.12, "rating": 0.69},
    "MF": {"shot_accuracy": 0.35, "xg_per_shot": 0.09, "rating": 0.68},
    "DF": {"shot_accuracy": 0.30, "xg_per_shot": 0.07, "rating": 0.67},
}
```

Un joueur dans la moyenne de son poste → `finishing_mult ≈ 1.0`.

---

## Modèle Passeur

### Constante partagée

```python
ASSIST_GOAL_RATE = 0.65   # ~65% des buts ont une passe décisive officielle
```

### Formule

```python
# 1. Blend saison + forme
form_xa_rate = form_assists_5 / (5 × avg_mins / 90)   # α=1.0 si absent
blended_xa   = 0.60 × xa_per_90 + 0.40 × form_xa_rate

# 2. Share top-down (ancré sur budget assists du match)
budget_assists = λ_team × ASSIST_GOAL_RATE
weight_xa_i    = blended_xa × (expected_minutes / 90)
denominator    = max(Σ weight_xa_j for j in équipe_en_DB, budget_assists)
share_xa_i     = weight_xa_i / denominator

# 3. Détection de profil créateur
cross_dominance = accurate_cross_per_90 / (key_pass_per_90 + accurate_cross_per_90)
profile = "wide"    if cross_dominance > 0.55
        | "central" if cross_dominance < 0.25
        | "hybrid"  otherwise
        | "unknown" if total < 0.05

# 4. Création multiplier (normalisé par poste)
xa_norm  = xa_per_90             / avgs_pos["xa_per_90"]
kp_norm  = key_pass_per_90       / avgs_pos["key_pass_per_90"]
xc_norm  = accurate_cross_per_90 / avgs_pos["accurate_cross_per_90"]
xca_norm = cross_accuracy        / 0.35   # 35% = moyenne ligue

CREATION_WEIGHTS = {
    "wide":    {"xa": 0.25, "kp": 0.20, "xc": 0.40, "xca": 0.15},
    "central": {"xa": 0.40, "kp": 0.50, "xc": 0.08, "xca": 0.02},
    "hybrid":  {"xa": 0.35, "kp": 0.35, "xc": 0.20, "xca": 0.10},
    "unknown": {"xa": 0.40, "kp": 0.35, "xc": 0.15, "xca": 0.10},
}

w = CREATION_WEIGHTS[profile]
creation_mult = w["xa"]*xa_norm + w["kp"]*kp_norm + w["xc"]*xc_norm + w["xca"]*xca_norm
creation_mult = clamp(creation_mult, 0.70, 1.50)

# 5. Conversion xA → assists réels
xa_conversion = clamp(assists / xa, 0.75, 1.40)   # si ≥5 matchs, sinon 1.0

# 6. Lambda
λ_assist = share_xa_i × budget_assists × creation_mult × xa_conversion

# 7. Probabilité
P         = 1 - exp(-λ_assist)
fair_odds = 1 / P
```

### Moyennes de normalisation par poste (passeur)

```python
ASSIST_POSITION_AVGS = {
    "FW": {"xa_per_90": 0.08, "key_pass_per_90": 0.30, "accurate_cross_per_90": 0.15},
    "MF": {"xa_per_90": 0.06, "key_pass_per_90": 0.55, "accurate_cross_per_90": 0.20},
    "DF": {"xa_per_90": 0.03, "key_pass_per_90": 0.20, "accurate_cross_per_90": 0.40},
}
```

---

## Confidence scoring

```python
def compute_confidence(stats, market_type) -> float:
    matches  = stats.get("matches_played") or 0
    form_key = "form_xg_5" if market_type == "goalscorer" else "form_assists_5"
    rate_key = "xg_per_90" if market_type == "goalscorer" else "xa_per_90"
    has_form = stats.get(form_key) is not None
    has_real = stats.get(rate_key) is not None

    if matches >= 10 and has_form and has_real: return 0.85
    elif matches >= 5  and has_real:            return 0.70
    elif matches >= 3:                          return 0.55
    elif matches >= 1:                          return 0.40
    else:                                       return 0.25
```

---

## Champs Bzzoiro à charger (ajouts à la query)

| Champ | Modèle | Usage |
|---|---|---|
| `form_xg_5` | Buteur | blend forme |
| `form_assists_5` | Passeur | blend forme |
| `shot_accuracy` | Buteur | finishing_mult |
| `xg_per_shot` | Buteur | finishing_mult |
| `avg_rating` | Buteur | finishing_mult |
| `finishing_delta` | Buteur | proxy conversion (goals/xg) |
| `xa_delta` | Passeur | xa_conversion |
| `key_pass_per_90` | Passeur | création_mult + profil |
| `accurate_cross_per_90` | Passeur | création_mult + profil |
| `cross_accuracy` | Passeur | création_mult |

---

## Cas limites

| Situation | Comportement |
|---|---|
| `form_xg_5` absent | α=1.0, stats saison seules |
| Joueur avec 1 match | inclus, share faible, confidence=0.40 |
| Conversion < 5 matchs | conversion=1.0 neutre |
| Équipe peu couverte | `denominator = λ_team` → shares conservateurs |
| `denominator = 0` | fallback position defaults, log warning |
| `profile = "unknown"` | poids "unknown" par défaut |
| Pen taker | λ_buteur += bonus, inchangé |

---

## Ce qui ne change pas

- `MarketXgService` — intact
- Edge = `(market_odds / fair_odds) - 1` — intact
- `normalize_selection_name` / name matching — intact
- Strategy selector — intact
- Settlement pipeline — intact

---

## Fichiers impactés

| Fichier | Action |
|---|---|
| `backend/app/pricing/goalscorer.py` | Réécriture du calcul λ (top-down + finishing_mult) |
| `backend/app/pricing/assist.py` | Réécriture complète (modèle indépendant) |
| `backend/app/services/recommendation_service.py` | Enrichir query Bzzoiro + câbler les deux nouveaux modèles |
