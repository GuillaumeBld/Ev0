# Defender Calibration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Corriger la surestimation des probabilités buts/passes des défenseurs via deux fixes indépendants : clamp floors position-spécifiques et discount set-piece sur leur part de xG.

**Architecture:** Trois fichiers pricing touchés. Task 1 corrige `goalscorer.py` (finishing multiplier). Task 2 corrige `assist.py` (creation multiplier + xa_conversion avec nouveau paramètre `position`). Task 3 corrige `team_xg.py` (discount DF dans `compute_player_shares` + mise à jour de l'appel `calculate_xa_conversion`). TDD dans tous les cas.

**Tech Stack:** Python 3.13, pytest, SQLAlchemy async

---

## File Structure

- Modify: `backend/app/pricing/goalscorer.py` — `FINISHING_MULT_CLAMP` global tuple → dict position-spécifique
- Modify: `backend/app/pricing/assist.py` — `CREATION_MULT_CLAMP` + `XA_CONVERSION_CLAMP` → dicts ; `calculate_xa_conversion` reçoit `position`
- Modify: `backend/app/pricing/team_xg.py` — constante `DF_SETPIECE_DISCOUNT` + discount dans `compute_player_shares` + appel `calculate_xa_conversion` mis à jour
- Create: `backend/tests/pricing/test_defender_calibration.py` — tests unitaires des deux corrections

---

### Task 1 : Goalscorer — finishing multiplier position-spécifique

**Files:**
- Modify: `backend/app/pricing/goalscorer.py:45-67`
- Test: `backend/tests/pricing/test_defender_calibration.py`

**Contexte :** `FINISHING_MULT_CLAMP` est actuellement un tuple global `(0.70, 1.50)`. Un DF avec 0 tirs reçoit 0.70 (plancher trop élevé). On le remplace par un dict indexé par position.

- [ ] **Step 1 : Écrire les tests qui échouent**

Créer `backend/tests/pricing/test_defender_calibration.py` :

```python
import pytest
from app.pricing.goalscorer import calculate_finishing_multiplier


class TestFinishingMultiplierDefender:
    def test_df_no_shots_clamped_low(self):
        """DF avec 0 tirs : multiplier plafonné à 0.30 (pas 0.70)."""
        stats = {
            "shot_accuracy": 0.0,
            "xg_per_shot": 0.0,
            "avg_rating": 6.5,
            "matches_played": 20,
            "npxg_total": 0.5,
            "goals": 0,
        }
        mult = calculate_finishing_multiplier(stats, "DF")
        assert mult <= 0.32

    def test_fw_floor_unchanged(self):
        """FW avec stats normales : plancher toujours 0.70."""
        stats = {
            "shot_accuracy": 0.50,
            "xg_per_shot": 0.17,
            "avg_rating": 7.0,
            "matches_played": 20,
            "npxg_total": 5.0,
            "goals": 5,
        }
        mult = calculate_finishing_multiplier(stats, "FW")
        assert 0.70 <= mult <= 1.50

    def test_unknown_position_fallback(self):
        """Position inconnue → fallback MF (plancher 0.55)."""
        stats = {
            "shot_accuracy": 0.0,
            "xg_per_shot": 0.0,
            "avg_rating": 6.5,
            "matches_played": 5,
            "npxg_total": 0.0,
            "goals": 0,
        }
        mult = calculate_finishing_multiplier(stats, None)
        assert mult >= 0.55
```

- [ ] **Step 2 : Vérifier que les tests échouent**

```bash
cd backend && python3 -m pytest tests/pricing/test_defender_calibration.py::TestFinishingMultiplierDefender -v
```

Expected : `FAILED` — `test_df_no_shots_clamped_low` retourne 0.70 (assertion `<= 0.32` échoue).

- [ ] **Step 3 : Implémenter dans `goalscorer.py`**

Remplacer les lignes 45-46 :

```python
# Avant
FINISHING_MULT_CLAMP: tuple[float, float] = (0.70, 1.50)
CONVERSION_CLAMP: tuple[float, float] = (0.75, 1.40)
```

Par :

```python
FINISHING_MULT_CLAMP: dict[str, tuple[float, float]] = {
    "FW": (0.70, 1.50),
    "MF": (0.55, 1.50),
    "DF": (0.30, 1.30),
}
_FINISHING_MULT_CLAMP_DEFAULT: tuple[float, float] = (0.55, 1.50)
CONVERSION_CLAMP: tuple[float, float] = (0.75, 1.40)
```

Remplacer la dernière ligne de `calculate_finishing_multiplier` (ligne 67) :

```python
# Avant
    return max(FINISHING_MULT_CLAMP[0], min(raw, FINISHING_MULT_CLAMP[1]))
```

Par :

```python
    clamp = FINISHING_MULT_CLAMP.get(position or "", _FINISHING_MULT_CLAMP_DEFAULT)
    return max(clamp[0], min(raw, clamp[1]))
```

- [ ] **Step 4 : Vérifier que les tests passent**

```bash
cd backend && python3 -m pytest tests/pricing/test_defender_calibration.py::TestFinishingMultiplierDefender -v
```

Expected : `3 passed`

- [ ] **Step 5 : Vérifier aucune régression sur les tests existants**

```bash
cd backend && python3 -m pytest tests/pricing/test_supersub.py -v
```

Expected : `7 passed`

- [ ] **Step 6 : Commit**

```bash
git add backend/app/pricing/goalscorer.py backend/tests/pricing/test_defender_calibration.py
git commit -m "fix: position-specific finishing_mult clamp floors — DF floor 0.70→0.30"
```

---

### Task 2 : Assist — clamps position-spécifiques + position dans xa_conversion

**Files:**
- Modify: `backend/app/pricing/assist.py:48-122`
- Modify: `backend/app/pricing/team_xg.py:354`
- Test: `backend/tests/pricing/test_defender_calibration.py`

**Contexte :** `CREATION_MULT_CLAMP` et `XA_CONVERSION_CLAMP` sont des tuples globaux dans `assist.py`. De plus, `calculate_xa_conversion` ne prend pas `position` en paramètre — il faut l'ajouter (signature backward-compatible avec default `None`). L'appel dans `team_xg.py:354` doit être mis à jour.

- [ ] **Step 1 : Ajouter les tests qui échouent dans `test_defender_calibration.py`**

Ajouter après la classe `TestFinishingMultiplierDefender` :

```python
from app.pricing.assist import calculate_creation_multiplier_v2, calculate_xa_conversion


class TestAssistMultiplierDefender:
    def test_creation_mult_df_clamped_low(self):
        """DF avec 0 stats création : creation_mult = 0.40 (plancher DF)."""
        stats = {
            "xa_per_90": 0.0,
            "key_pass_per_90": 0.0,
            "accurate_cross_per_90": 0.0,
            "cross_accuracy": 0.0,
        }
        mult = calculate_creation_multiplier_v2(stats, "DF")
        assert mult == 0.40

    def test_creation_mult_fw_unchanged(self):
        """FW : plancher toujours 0.70."""
        stats = {
            "xa_per_90": 0.0,
            "key_pass_per_90": 0.0,
            "accurate_cross_per_90": 0.0,
            "cross_accuracy": 0.0,
        }
        mult = calculate_creation_multiplier_v2(stats, "FW")
        assert mult == 0.70

    def test_xa_conversion_df_floor(self):
        """DF avec 0 assists sur 10 matchs : conversion = 0.50 (plancher DF, pas 0.75)."""
        stats = {"matches_played": 10, "xa_total": 2.0, "assists": 0}
        conv = calculate_xa_conversion(stats, "DF")
        assert conv == 0.50

    def test_xa_conversion_no_position_backward_compat(self):
        """Appel sans position → plancher par défaut 0.75."""
        stats = {"matches_played": 10, "xa_total": 2.0, "assists": 0}
        conv = calculate_xa_conversion(stats)
        assert conv == 0.75
```

- [ ] **Step 2 : Vérifier que les tests échouent**

```bash
cd backend && python3 -m pytest tests/pricing/test_defender_calibration.py::TestAssistMultiplierDefender -v
```

Expected : `FAILED` — `test_creation_mult_df_clamped_low` retourne 0.70, `test_xa_conversion_df_floor` retourne 0.75 ou TypeError.

- [ ] **Step 3 : Implémenter dans `assist.py`**

Remplacer les lignes 48-49 :

```python
# Avant
CREATION_MULT_CLAMP: tuple[float, float] = (0.70, 1.50)
XA_CONVERSION_CLAMP: tuple[float, float] = (0.75, 1.40)
```

Par :

```python
CREATION_MULT_CLAMP: dict[str, tuple[float, float]] = {
    "FW": (0.70, 1.50),
    "MF": (0.70, 1.50),
    "DF": (0.40, 1.30),
}
_CREATION_MULT_CLAMP_DEFAULT: tuple[float, float] = (0.70, 1.50)

XA_CONVERSION_CLAMP: dict[str, tuple[float, float]] = {
    "FW": (0.75, 1.40),
    "MF": (0.75, 1.40),
    "DF": (0.50, 1.40),
}
_XA_CONVERSION_CLAMP_DEFAULT: tuple[float, float] = (0.75, 1.40)
```

Remplacer la dernière ligne de `calculate_creation_multiplier_v2` (ligne 110) :

```python
# Avant
    return max(CREATION_MULT_CLAMP[0], min(raw, CREATION_MULT_CLAMP[1]))
```

Par :

```python
    clamp = CREATION_MULT_CLAMP.get(position or "", _CREATION_MULT_CLAMP_DEFAULT)
    return max(clamp[0], min(raw, clamp[1]))
```

Remplacer la signature et la dernière ligne de `calculate_xa_conversion` (lignes 113 et 122) :

```python
# Avant
def calculate_xa_conversion(stats: dict[str, Any]) -> float:
    """Assists / xA conversion rate. Returns 1.0 if insufficient data."""
    matches = stats.get("matches_played") or 0
    if matches < XA_CONVERSION_MIN_MATCHES:
        return 1.0
    xa = stats.get("xa_total") or 0.0
    assists = stats.get("assists") or 0
    if xa <= 0:
        return 1.0
    return max(XA_CONVERSION_CLAMP[0], min(assists / xa, XA_CONVERSION_CLAMP[1]))
```

Par :

```python
def calculate_xa_conversion(stats: dict[str, Any], position: str | None = None) -> float:
    """Assists / xA conversion rate. Returns 1.0 if insufficient data."""
    matches = stats.get("matches_played") or 0
    if matches < XA_CONVERSION_MIN_MATCHES:
        return 1.0
    xa = stats.get("xa_total") or 0.0
    assists = stats.get("assists") or 0
    if xa <= 0:
        return 1.0
    clamp = XA_CONVERSION_CLAMP.get(position or "", _XA_CONVERSION_CLAMP_DEFAULT)
    return max(clamp[0], min(assists / xa, clamp[1]))
```

- [ ] **Step 4 : Mettre à jour l'appel dans `team_xg.py:354`**

Remplacer la ligne 354 de `backend/app/pricing/team_xg.py` :

```python
# Avant
    xa_conversion = calculate_xa_conversion(assist_stats)
```

Par :

```python
    xa_conversion = calculate_xa_conversion(assist_stats, share.position)
```

- [ ] **Step 5 : Vérifier que les tests passent**

```bash
cd backend && python3 -m pytest tests/pricing/test_defender_calibration.py -v
```

Expected : `7 passed`

- [ ] **Step 6 : Vérifier aucune régression**

```bash
cd backend && python3 -m pytest tests/pricing/test_supersub.py tests/services/test_sub_stats.py -v
```

Expected : `11 passed`

- [ ] **Step 7 : Commit**

```bash
git add backend/app/pricing/assist.py backend/app/pricing/team_xg.py backend/tests/pricing/test_defender_calibration.py
git commit -m "fix: position-specific creation/xa_conversion clamps — DF floors lowered"
```

---

### Task 3 : Set-piece discount DF dans compute_player_shares

**Files:**
- Modify: `backend/app/pricing/team_xg.py:43-253`
- Test: `backend/tests/pricing/test_defender_calibration.py`

**Contexte :** Dans `compute_player_shares` (Pass 1), le `goal_weight` d'un DF agrège son xG open-play et set-piece. On applique un discount de 0.55 sur le `goal_weight` des DF pour corriger la surestimation de leur part de xG équipe. Pas de discount sur `assist_weight`.

- [ ] **Step 1 : Ajouter les tests qui échouent**

Ajouter à la fin de `backend/tests/pricing/test_defender_calibration.py` :

```python
from app.pricing.team_xg import compute_player_shares, DF_SETPIECE_DISCOUNT


class TestSetpieceDiscount:
    def _make_player(self, name: str, position: str, npxg_per_90: float) -> dict:
        return {
            "player_id": hash(name),
            "player_name": name,
            "position": position,
            "matches_played": 20,
            "minutes_played": 1800,
            "avg_minutes_per_match": 90.0,
            "npxg_per_90": npxg_per_90,
            "xa_per_90": 0.05,
            "form_xg_5": None,
            "form_assists_5": None,
            # champs requis par compute_player_shares
            "shot_accuracy": 0.4,
            "xg_per_shot": 0.12,
            "avg_rating": 6.8,
            "cross_accuracy": 0.3,
            "xa_total": 1.0,
            "assists_total": 1,
            "npxg_total": 2.0,
            "goals_total": 2,
            "key_pass_per_90": 0.8,
            "accurate_cross_per_90": 0.3,
            "has_bzz_stats": True,
            "xg_per_90": npxg_per_90,
            "xgchain_per_90": 0.0,
            "shots_on_target_per_90": 0.5,
            "touches_attack_pen_area_per_90": 0.0,
            "bcc_per_90": 0.0,
            "accurate_crosses_per_90": 0.3,
            "through_balls_per_90": 0.0,
            "finishing_delta": 0.0,
        }

    def test_df_share_discounted(self):
        """npxg_share d'un DF est ~55% de ce qu'il serait sans discount."""
        fw = self._make_player("FW1", "FW", 0.30)
        df = self._make_player("DF1", "DF", 0.30)  # même stats que FW

        shares_fw = compute_player_shares([fw, df], "TeamA", lambda_team=1.5)
        share_fw = next(s for s in shares_fw if s.player_name == "FW1").npxg_share
        share_df = next(s for s in shares_fw if s.player_name == "DF1").npxg_share

        # DF share doit être ~55% du FW share (même stats, seul le discount diffère)
        ratio = share_df / share_fw
        assert abs(ratio - DF_SETPIECE_DISCOUNT) < 0.01

    def test_fw_share_unaffected(self):
        """npxg_share d'un FW est inchangé quand on ajoute un DF à l'équipe."""
        fw = self._make_player("FW1", "FW", 0.30)

        # FW seul
        shares_solo = compute_player_shares([fw], "TeamA", lambda_team=1.5)
        share_solo = shares_solo[0].npxg_share

        # FW + DF
        df = self._make_player("DF1", "DF", 0.10)
        shares_with_df = compute_player_shares([fw, df], "TeamA", lambda_team=1.5)
        share_with_df = next(s for s in shares_with_df if s.player_name == "FW1").npxg_share

        assert abs(share_solo - share_with_df) < 0.0001
```

- [ ] **Step 2 : Vérifier que les tests échouent**

```bash
cd backend && python3 -m pytest tests/pricing/test_defender_calibration.py::TestSetpieceDiscount -v
```

Expected : `FAILED` — `ImportError: cannot import name 'DF_SETPIECE_DISCOUNT'` ou ratio ≠ 0.55.

- [ ] **Step 3 : Implémenter dans `team_xg.py`**

Ajouter la constante après la ligne `PENS_PER_MATCH` (ligne ~42) :

```python
DF_SETPIECE_DISCOUNT: float = 0.55
```

Dans `compute_player_shares`, Pass 1, après le calcul de `goal_weight = blended_xg * mins_ratio` (ligne ~234), ajouter :

```python
        goal_weight = blended_xg * mins_ratio
        if pos == "DF":
            goal_weight *= DF_SETPIECE_DISCOUNT
```

La ligne existante `goal_weight = blended_xg * mins_ratio` reste, on ajoute juste le bloc `if` juste après.

- [ ] **Step 4 : Vérifier que les tests passent**

```bash
cd backend && python3 -m pytest tests/pricing/test_defender_calibration.py -v
```

Expected : `9 passed`

- [ ] **Step 5 : Vérifier aucune régression**

```bash
cd backend && python3 -m pytest tests/pricing/ -v
```

Expected : tous les tests pricing passent (test_supersub.py + test_defender_calibration.py).

- [ ] **Step 6 : Commit**

```bash
git add backend/app/pricing/team_xg.py backend/tests/pricing/test_defender_calibration.py
git commit -m "fix: set-piece discount 0.55 on DF goal_weight in compute_player_shares"
```
