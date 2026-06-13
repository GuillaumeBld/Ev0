# Defender Calibration — Design Spec

**Goal:** Corriger la surestimation des probabilités buts/passes des défenseurs en adressant deux mécanismes indépendants : planchers de multiplicateurs trop élevés et part de xG gonflée par les coups de pied arrêtés.

**Architecture:** Deux corrections chirurgicales dans la couche pricing, sans impact sur FW/MF, sans changement de schéma DB ni de frontend.

**Tech Stack:** Python, `goalscorer.py`, `assist.py`, `team_xg.py`

---

## Cause 1 — Clamp floors trop élevés pour les défenseurs

### Problème

`FINISHING_MULT_CLAMP`, `CREATION_MULT_CLAMP` et `XA_CONVERSION_CLAMP` sont actuellement des tuples globaux avec plancher à 0.70-0.75. Un CB avec 0 tirs et 0 passes clés reçoit quand même 70% du multiplicateur moyen d'un FW — ce plancher universel empêche le modèle d'exprimer la faible qualité finishing/création d'un défenseur type.

### Correction

Remplacer les tuples globaux par des dicts indexés par position :

**`goalscorer.py`**
```python
FINISHING_MULT_CLAMP: dict[str, tuple[float, float]] = {
    "FW": (0.70, 1.50),
    "MF": (0.55, 1.50),
    "DF": (0.30, 1.30),
}
```

**`assist.py`**
```python
CREATION_MULT_CLAMP: dict[str, tuple[float, float]] = {
    "FW": (0.70, 1.50),
    "MF": (0.70, 1.50),
    "DF": (0.40, 1.30),
}
XA_CONVERSION_CLAMP: dict[str, tuple[float, float]] = {
    "FW": (0.75, 1.40),
    "MF": (0.75, 1.40),
    "DF": (0.50, 1.40),
}
```

Les fonctions `calculate_finishing_multiplier`, `calculate_creation_multiplier_v2` et `calculate_xa_conversion` reçoivent déjà `position` en paramètre — elles lookupent la bonne paire avec fallback sur "MF" si position inconnue.

**Impact simulé :** FW/MF avec bonnes stats → 0 impact. MF en-dessous de la moyenne → légère baisse (correction bienvenue). DF → réduction significative. Total λ équipe : -4.6%.

---

## Cause 2 — Part de xG DF gonflée par les coups de pied arrêtés

### Problème

Les données Bzzoiro agrègent le xG open-play et set-piece (corners, coups francs) en une seule colonne. La part historique d'un CB dans le xG de son équipe reflète donc des buts de tête sur corner qui ne scalent pas linéairement avec le `team_match_xg` issu des marchés 1X2/OU/BTTS. Le modèle leur attribue ainsi une fraction du budget équipe qu'ils ne méritent pas en jeu ouvert.

### Correction

Dans `compute_player_shares` (Pass 1), appliquer un discount sur le `goal_weight` des DF avant normalisation :

**`team_xg.py`**
```python
DF_SETPIECE_DISCOUNT: float = 0.55
```

```python
# Pass 1 — compute blended weights per player
# ... après calcul de goal_weight :
if pos == "DF":
    goal_weight *= DF_SETPIECE_DISCOUNT
```

Pas de discount sur `assist_weight` — les passes décisives des DF proviennent majoritairement du jeu ouvert (centres, passes longues).

**Impact simulé :** FW/MF shares → 0 impact dans tous les scénarios (typical et top team), car `goal_denom = max(total_goal, lambda_team) = lambda_team` dans la quasi-totalité des cas. DF shares réduits d'environ 45%.

---

## Fichiers touchés

| Fichier | Modification |
|---|---|
| `backend/app/pricing/goalscorer.py` | `FINISHING_MULT_CLAMP` → dict position-spécifique + lookup dans `calculate_finishing_multiplier` |
| `backend/app/pricing/assist.py` | `CREATION_MULT_CLAMP`, `XA_CONVERSION_CLAMP` → dicts + lookups dans `calculate_creation_multiplier_v2` et `calculate_xa_conversion`; ajout de `position: str \| None` à la signature de `calculate_xa_conversion` |
| `backend/app/pricing/team_xg.py` | Constante `DF_SETPIECE_DISCOUNT = 0.55` + application dans `compute_player_shares` Pass 1 ; appel `calculate_xa_conversion(assist_stats, share.position)` (ligne 354) |
| `backend/tests/pricing/test_defender_calibration.py` | Nouveau fichier — tests unitaires des deux corrections |

Aucune migration DB. Aucun changement frontend. Aucune nouvelle dépendance.

---

## Tests

**`backend/tests/pricing/test_defender_calibration.py`**

```python
def test_finishing_mult_df_no_shots():
    """Un DF avec 0 tirs reçoit un multiplier ≤ 0.32 (plancher DF = 0.30)."""
    stats = {"shot_accuracy": 0.0, "xg_per_shot": 0.0, "avg_rating": 6.5,
             "matches_played": 20, "npxg_total": 0.5, "goals": 0}
    mult = calculate_finishing_multiplier(stats, "DF")
    assert mult <= 0.32

def test_finishing_mult_fw_unchanged():
    """Un FW avec stats normales n'est pas affecté par le changement."""
    stats = {"shot_accuracy": 0.50, "xg_per_shot": 0.17, "avg_rating": 7.0,
             "matches_played": 20, "npxg_total": 5.0, "goals": 5}
    mult = calculate_finishing_multiplier(stats, "FW")
    assert 0.70 <= mult <= 1.50

def test_df_setpiece_discount_applied():
    """goal_weight d'un DF est multiplié par DF_SETPIECE_DISCOUNT dans compute_player_shares."""
    # Vérifie que npxg_share d'un DF est ~55% de ce qu'il serait sans discount
    # en comparant un run avec DF et un run avec DF simulé en MF (même stats)

def test_fw_share_unaffected_by_df_discount():
    """npxg_share d'un FW est inchangé quand on applique le discount DF."""

def test_xa_conversion_df_clamp():
    """calculate_xa_conversion avec position DF retourne min 0.50 (pas 0.75)."""
    stats = {"matches_played": 10, "xa_total": 2.0, "assists": 0}
    conv = calculate_xa_conversion(stats, "DF")
    assert conv == 0.50

def test_creation_mult_df_clamp():
    """calculate_creation_multiplier_v2 avec position DF est clamped à (0.40, 1.30)."""
    stats = {"xa_per_90": 0.0, "key_pass_per_90": 0.0,
             "accurate_cross_per_90": 0.0, "cross_accuracy": 0.0}
    mult = calculate_creation_multiplier_v2(stats, "DF")
    assert mult == 0.40
```
