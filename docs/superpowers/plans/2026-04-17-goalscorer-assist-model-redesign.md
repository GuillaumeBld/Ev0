# Goalscorer & Assist Model Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remplacer le modèle bottom-up cassé (`λ = xg_per_90 × mins/90`) par un modèle top-down ancré sur le xG marché, avec deux modules indépendants buteur/passeur exploitant les données Bzzoiro riches.

**Architecture:** MarketXgService fournit `λ_home`/`λ_away` comme budget de buts. Chaque joueur reçoit une `share` proportionnelle à sa contribution historique (blendée avec la forme récente). Deux multiplicateurs distincts : `finishing_mult` (buteur) et `creation_mult` hybride position+profil (passeur). `CALIBRATION_SCALE` supprimé.

**Tech Stack:** Python 3.12, SQLAlchemy async, Bzzoiro (`bzz_player_season_stats`), pytest + pytest-asyncio

---

## File Map

| Fichier | Action | Responsabilité |
|---|---|---|
| `backend/app/pricing/goalscorer.py` | Modifier | Ajouter constantes + 3 nouvelles fonctions (finishing_mult, conversion, λ) |
| `backend/app/pricing/assist.py` | Modifier | Ajouter constantes + 4 nouvelles fonctions (profil, création_mult_v2, xa_conversion, λ) |
| `backend/app/services/recommendation_service.py` | Modifier | Enrichir query Bzzoiro, ajouter helpers top-down, câbler nouveaux modèles, supprimer CALIBRATION_SCALE |
| `backend/tests/pricing/test_goalscorer_bzz.py` | Modifier | Tests pour les nouvelles fonctions goalscorer |
| `backend/tests/pricing/test_assist_v2.py` | Créer | Tests pour les nouvelles fonctions assist |
| `backend/tests/services/test_recommendation_topdown.py` | Créer | Tests helpers top-down dans recommendation_service |

---

## Task 1 — Nouvelles fonctions Goalscorer (goalscorer.py)

**Files:**
- Modify: `backend/app/pricing/goalscorer.py`
- Test: `backend/tests/pricing/test_goalscorer_bzz.py`

- [ ] **Step 1 : Écrire les tests qui vont échouer**

Ajouter à la fin de `backend/tests/pricing/test_goalscorer_bzz.py` :

```python
# ── Nouveaux tests : finishing multiplier, conversion, λ top-down ──────────

from app.pricing.goalscorer import (
    calculate_finishing_multiplier,
    calculate_conversion_rate,
    calculate_goalscorer_lambda,
)


class TestFinishingMultiplier:
    def test_average_fw_returns_near_one(self):
        stats = {"shot_accuracy": 0.42, "xg_per_shot": 0.12, "avg_rating": 6.9}
        mult = calculate_finishing_multiplier(stats, "FW")
        assert 0.95 <= mult <= 1.05

    def test_elite_fw_clamped_to_max(self):
        stats = {"shot_accuracy": 0.80, "xg_per_shot": 0.30, "avg_rating": 9.0}
        mult = calculate_finishing_multiplier(stats, "FW")
        assert mult == 1.50

    def test_poor_fw_clamped_to_min(self):
        stats = {"shot_accuracy": 0.05, "xg_per_shot": 0.01, "avg_rating": 4.0}
        mult = calculate_finishing_multiplier(stats, "FW")
        assert mult == 0.70

    def test_none_stats_return_min(self):
        stats = {"shot_accuracy": None, "xg_per_shot": None, "avg_rating": None}
        mult = calculate_finishing_multiplier(stats, "MF")
        assert mult == 0.70

    def test_unknown_position_uses_fallback(self):
        stats = {"shot_accuracy": 0.37, "xg_per_shot": 0.10, "avg_rating": 6.8}
        mult = calculate_finishing_multiplier(stats, None)
        assert 0.95 <= mult <= 1.05


class TestConversionRate:
    def test_below_min_matches_returns_one(self):
        stats = {"matches_played": 3, "goals": 5, "npxg_total": 2.0}
        assert calculate_conversion_rate(stats) == 1.0

    def test_overperformer_clamped(self):
        stats = {"matches_played": 10, "goals": 15, "npxg_total": 5.0}
        assert calculate_conversion_rate(stats) == 1.40

    def test_underperformer_clamped(self):
        stats = {"matches_played": 10, "goals": 1, "npxg_total": 8.0}
        assert calculate_conversion_rate(stats) == 0.75

    def test_zero_xg_returns_one(self):
        stats = {"matches_played": 10, "goals": 0, "npxg_total": 0.0}
        assert calculate_conversion_rate(stats) == 1.0

    def test_normal_conversion(self):
        stats = {"matches_played": 10, "goals": 8, "npxg_total": 8.0}
        assert calculate_conversion_rate(stats) == pytest.approx(1.0, abs=0.01)


class TestGoalscorerLambda:
    def test_basic_lambda(self):
        lam = calculate_goalscorer_lambda(
            share=0.30, lambda_team=1.5, finishing_mult=1.0,
            conversion=1.0, mins_ratio=1.0, is_pen_taker=False,
        )
        assert lam == pytest.approx(0.45, abs=0.001)

    def test_pen_taker_bonus_added(self):
        lam_no_pen = calculate_goalscorer_lambda(0.20, 1.5, 1.0, 1.0, 1.0, False)
        lam_pen    = calculate_goalscorer_lambda(0.20, 1.5, 1.0, 1.0, 1.0, True)
        assert lam_pen > lam_no_pen
        # bonus ≈ PEN_CONVERSION(0.78) × PENS_PER_MATCH(0.10) × 1.0 = 0.078
        assert lam_pen - lam_no_pen == pytest.approx(0.078, abs=0.001)

    def test_lambda_clamped_to_max(self):
        lam = calculate_goalscorer_lambda(1.0, 10.0, 1.5, 1.4, 1.0, False)
        assert lam == 3.0  # CLAMP_LAMBDA_MAX

    def test_lambda_clamped_to_min(self):
        lam = calculate_goalscorer_lambda(0.0, 0.0, 0.70, 0.75, 0.0, False)
        assert lam == 0.01  # CLAMP_LAMBDA_MIN
```

- [ ] **Step 2 : Vérifier que les tests échouent**

```bash
cd backend && uv run pytest tests/pricing/test_goalscorer_bzz.py::TestFinishingMultiplier tests/pricing/test_goalscorer_bzz.py::TestConversionRate tests/pricing/test_goalscorer_bzz.py::TestGoalscorerLambda -v 2>&1 | tail -20
```

Expected : `ImportError` ou `FAILED` sur les 3 classes.

- [ ] **Step 3 : Implémenter les nouvelles fonctions dans goalscorer.py**

Ajouter après les constantes existantes (`CLAMP_MULTIPLIER_MIN`, etc.) et avant `calculate_quality_multiplier` :

```python
# ── Pen taker constants ───────────────────────────────────────────
PEN_CONVERSION = 0.78
PENS_PER_MATCH = 0.10

# ── Top-down finishing multiplier (Bzzoiro) ───────────────────────

GOALSCORER_POSITION_AVGS: dict[str, dict[str, float]] = {
    "FW": {"shot_accuracy": 0.42, "xg_per_shot": 0.12, "rating": 0.69},
    "MF": {"shot_accuracy": 0.35, "xg_per_shot": 0.09, "rating": 0.68},
    "DF": {"shot_accuracy": 0.30, "xg_per_shot": 0.07, "rating": 0.67},
}
_GOALSCORER_FALLBACK_AVGS: dict[str, float] = {
    "shot_accuracy": 0.37, "xg_per_shot": 0.10, "rating": 0.68,
}

FINISHING_MULT_WEIGHTS: dict[str, float] = {
    "shot_accuracy": 0.40,
    "xg_per_shot":   0.40,
    "rating":        0.20,
}
FINISHING_MULT_CLAMP: tuple[float, float] = (0.70, 1.50)
CONVERSION_CLAMP: tuple[float, float] = (0.75, 1.40)
CONVERSION_MIN_MATCHES: int = 5


def calculate_finishing_multiplier(stats: dict[str, Any], position: str | None) -> float:
    """Normalize finishing quality stats against position averages → multiplier ≈ 1.0."""
    avgs = GOALSCORER_POSITION_AVGS.get(position or "", _GOALSCORER_FALLBACK_AVGS)

    def norm(key: str) -> float:
        val = stats.get(key) or 0.0
        avg = avgs.get(key, 1.0)
        return (val / avg) if avg > 0 else 0.0

    rating_raw = (stats.get("avg_rating") or 0.0) / 10.0  # normalize to 0-1
    rating_norm = rating_raw / avgs["rating"] if avgs["rating"] > 0 else 0.0

    raw = (
        norm("shot_accuracy") * FINISHING_MULT_WEIGHTS["shot_accuracy"]
        + norm("xg_per_shot")   * FINISHING_MULT_WEIGHTS["xg_per_shot"]
        + rating_norm           * FINISHING_MULT_WEIGHTS["rating"]
    )
    return max(FINISHING_MULT_CLAMP[0], min(raw, FINISHING_MULT_CLAMP[1]))


def calculate_conversion_rate(stats: dict[str, Any]) -> float:
    """Goals / xG conversion rate. Returns 1.0 if insufficient data."""
    matches = stats.get("matches_played") or 0
    if matches < CONVERSION_MIN_MATCHES:
        return 1.0
    xg = stats.get("npxg_total") or 0.0
    goals = stats.get("goals") or 0
    if xg <= 0:
        return 1.0
    return max(CONVERSION_CLAMP[0], min(goals / xg, CONVERSION_CLAMP[1]))


def calculate_goalscorer_lambda(
    share: float,
    lambda_team: float,
    finishing_mult: float,
    conversion: float,
    mins_ratio: float,
    is_pen_taker: bool = False,
) -> float:
    """Compute final goalscorer λ (top-down allocation)."""
    lam = share * lambda_team * finishing_mult * conversion
    if is_pen_taker:
        lam += PEN_CONVERSION * PENS_PER_MATCH * mins_ratio
    return max(CLAMP_LAMBDA_MIN, min(lam, CLAMP_LAMBDA_MAX))
```

- [ ] **Step 4 : Vérifier que les tests passent**

```bash
cd backend && uv run pytest tests/pricing/test_goalscorer_bzz.py::TestFinishingMultiplier tests/pricing/test_goalscorer_bzz.py::TestConversionRate tests/pricing/test_goalscorer_bzz.py::TestGoalscorerLambda -v 2>&1 | tail -20
```

Expected : tous `PASSED`.

- [ ] **Step 5 : Commit**

```bash
cd backend && git add app/pricing/goalscorer.py tests/pricing/test_goalscorer_bzz.py
git commit -m "feat(pricing): add top-down goalscorer finishing_mult, conversion, λ functions"
```

---

## Task 2 — Nouvelles fonctions Assist (assist.py)

**Files:**
- Modify: `backend/app/pricing/assist.py`
- Create: `backend/tests/pricing/test_assist_v2.py`

- [ ] **Step 1 : Créer le fichier de tests**

Créer `backend/tests/pricing/test_assist_v2.py` :

```python
"""Tests for top-down assist pricing functions (v2, Bzzoiro-based)."""

import pytest

from app.pricing.assist import (
    ASSIST_GOAL_RATE,
    detect_creator_profile,
    calculate_creation_multiplier_v2,
    calculate_xa_conversion,
    calculate_assist_lambda,
)


class TestDetectCreatorProfile:
    def test_wide_profile_cross_dominant(self):
        stats = {"key_pass_per_90": 0.30, "accurate_cross_per_90": 1.20}
        assert detect_creator_profile(stats) == "wide"

    def test_central_profile_pass_dominant(self):
        stats = {"key_pass_per_90": 1.00, "accurate_cross_per_90": 0.10}
        assert detect_creator_profile(stats) == "central"

    def test_hybrid_profile(self):
        stats = {"key_pass_per_90": 0.60, "accurate_cross_per_90": 0.40}
        assert detect_creator_profile(stats) == "hybrid"

    def test_unknown_when_no_data(self):
        stats = {"key_pass_per_90": 0.0, "accurate_cross_per_90": 0.0}
        assert detect_creator_profile(stats) == "unknown"

    def test_unknown_when_none(self):
        stats = {"key_pass_per_90": None, "accurate_cross_per_90": None}
        assert detect_creator_profile(stats) == "unknown"

    def test_boundary_wide_threshold(self):
        # cross_dominance = 0.56 → wide
        stats = {"key_pass_per_90": 0.44, "accurate_cross_per_90": 0.56}
        assert detect_creator_profile(stats) == "wide"

    def test_boundary_central_threshold(self):
        # cross_dominance = 0.20 → central
        stats = {"key_pass_per_90": 0.80, "accurate_cross_per_90": 0.20}
        assert detect_creator_profile(stats) == "central"


class TestCreationMultiplierV2:
    def test_average_mf_returns_near_one(self):
        stats = {
            "xa_per_90": 0.06, "key_pass_per_90": 0.55,
            "accurate_cross_per_90": 0.20, "cross_accuracy": 0.35,
        }
        mult = calculate_creation_multiplier_v2(stats, "MF")
        assert 0.95 <= mult <= 1.05

    def test_elite_creator_clamped_to_max(self):
        stats = {
            "xa_per_90": 0.30, "key_pass_per_90": 3.0,
            "accurate_cross_per_90": 2.0, "cross_accuracy": 0.80,
        }
        mult = calculate_creation_multiplier_v2(stats, "MF")
        assert mult == 1.50

    def test_poor_creator_clamped_to_min(self):
        stats = {
            "xa_per_90": 0.0, "key_pass_per_90": 0.0,
            "accurate_cross_per_90": 0.0, "cross_accuracy": 0.0,
        }
        mult = calculate_creation_multiplier_v2(stats, "MF")
        assert mult == 0.70

    def test_wide_profile_weights_crosses_more(self):
        # Two players same xa+kp, but one has high crosses
        base = {"xa_per_90": 0.06, "key_pass_per_90": 0.55, "cross_accuracy": 0.35}
        low_cross = {**base, "accurate_cross_per_90": 0.10}
        high_cross = {**base, "accurate_cross_per_90": 1.50}
        # DF profile → wide detection → crosses weighted 0.40
        mult_low  = calculate_creation_multiplier_v2(low_cross, "DF")
        mult_high = calculate_creation_multiplier_v2(high_cross, "DF")
        assert mult_high > mult_low

    def test_none_position_uses_unknown_weights(self):
        stats = {
            "xa_per_90": 0.06, "key_pass_per_90": 0.45,
            "accurate_cross_per_90": 0.22, "cross_accuracy": 0.35,
        }
        mult = calculate_creation_multiplier_v2(stats, None)
        assert 0.70 <= mult <= 1.50


class TestXaConversion:
    def test_below_min_matches_returns_one(self):
        stats = {"matches_played": 3, "assists": 5, "xa_total": 2.0}
        assert calculate_xa_conversion(stats) == 1.0

    def test_overperformer_clamped(self):
        stats = {"matches_played": 10, "assists": 12, "xa_total": 4.0}
        assert calculate_xa_conversion(stats) == 1.40

    def test_underperformer_clamped(self):
        stats = {"matches_played": 10, "assists": 1, "xa_total": 8.0}
        assert calculate_xa_conversion(stats) == 0.75

    def test_zero_xa_returns_one(self):
        stats = {"matches_played": 10, "assists": 0, "xa_total": 0.0}
        assert calculate_xa_conversion(stats) == 1.0


class TestAssistLambda:
    def test_basic_lambda(self):
        budget = 1.5 * ASSIST_GOAL_RATE
        lam = calculate_assist_lambda(
            share_xa=0.25, budget_assists=budget,
            creation_mult=1.0, xa_conversion=1.0,
        )
        assert lam == pytest.approx(0.25 * budget, abs=0.001)

    def test_lambda_clamped_to_max(self):
        lam = calculate_assist_lambda(1.0, 10.0, 1.5, 1.4)
        assert lam == 2.0  # CLAMP_LAMBDA_MAX pour assist

    def test_lambda_clamped_to_min(self):
        lam = calculate_assist_lambda(0.0, 0.0, 0.70, 0.75)
        assert lam == 0.01  # CLAMP_LAMBDA_MIN

    def test_assist_goal_rate_constant(self):
        assert ASSIST_GOAL_RATE == pytest.approx(0.65, abs=0.001)
```

- [ ] **Step 2 : Vérifier que les tests échouent**

```bash
cd backend && uv run pytest tests/pricing/test_assist_v2.py -v 2>&1 | tail -20
```

Expected : `ImportError` ou `FAILED` sur toutes les classes.

- [ ] **Step 3 : Implémenter les nouvelles fonctions dans assist.py**

Ajouter après les imports existants et avant les constantes `CREATION_WEIGHTS_DEFAULT` :

```python
# ── Top-down assist constants (Bzzoiro v2) ────────────────────────

ASSIST_GOAL_RATE: float = 0.65  # ~65% des buts ont une passe décisive officielle

ASSIST_POSITION_AVGS: dict[str, dict[str, float]] = {
    "FW": {"xa_per_90": 0.08, "key_pass_per_90": 0.30, "accurate_cross_per_90": 0.15},
    "MF": {"xa_per_90": 0.06, "key_pass_per_90": 0.55, "accurate_cross_per_90": 0.20},
    "DF": {"xa_per_90": 0.03, "key_pass_per_90": 0.20, "accurate_cross_per_90": 0.40},
}
_ASSIST_FALLBACK_AVGS: dict[str, float] = {
    "xa_per_90": 0.06, "key_pass_per_90": 0.45, "accurate_cross_per_90": 0.22,
}

CROSS_ACC_LEAGUE_AVG: float = 0.35

CREATION_WEIGHTS_BY_PROFILE: dict[str, dict[str, float]] = {
    "wide":    {"xa": 0.25, "kp": 0.20, "xc": 0.40, "xca": 0.15},
    "central": {"xa": 0.40, "kp": 0.50, "xc": 0.08, "xca": 0.02},
    "hybrid":  {"xa": 0.35, "kp": 0.35, "xc": 0.20, "xca": 0.10},
    "unknown": {"xa": 0.40, "kp": 0.35, "xc": 0.15, "xca": 0.10},
}

CREATION_MULT_CLAMP: tuple[float, float] = (0.70, 1.50)
XA_CONVERSION_CLAMP: tuple[float, float] = (0.75, 1.40)
XA_CONVERSION_MIN_MATCHES: int = 5
_PROFILE_WIDE_THRESHOLD: float = 0.55
_PROFILE_CENTRAL_THRESHOLD: float = 0.25
_PROFILE_MIN_TOTAL: float = 0.05
```

Puis ajouter ces 4 fonctions avant `calculate_creation_multiplier` (l'ancienne, qui reste pour rétrocompatibilité) :

```python
def detect_creator_profile(stats: dict[str, Any]) -> str:
    """Detect player's creation style from stats: 'wide', 'central', 'hybrid', or 'unknown'."""
    kp = stats.get("key_pass_per_90") or 0.0
    xc = stats.get("accurate_cross_per_90") or 0.0
    total = kp + xc
    if total < _PROFILE_MIN_TOTAL:
        return "unknown"
    cross_dominance = xc / total
    if cross_dominance > _PROFILE_WIDE_THRESHOLD:
        return "wide"
    elif cross_dominance < _PROFILE_CENTRAL_THRESHOLD:
        return "central"
    return "hybrid"


def calculate_creation_multiplier_v2(stats: dict[str, Any], position: str | None) -> float:
    """
    Hybrid position+profile creation multiplier (Bzzoiro v2).

    Normalises xa_per_90, key_pass_per_90, accurate_cross_per_90, cross_accuracy
    against position averages. Weights depend on detected creation profile.
    Returns value clamped to [0.70, 1.50] (1.0 = league average for this position).
    """
    profile = detect_creator_profile(stats)
    avgs = ASSIST_POSITION_AVGS.get(position or "", _ASSIST_FALLBACK_AVGS)

    def norm(stat_key: str, avg_key: str) -> float:
        val = stats.get(stat_key) or 0.0
        avg = avgs.get(avg_key, 1.0)
        return (val / avg) if avg > 0 else 0.0

    xa_norm  = norm("xa_per_90", "xa_per_90")
    kp_norm  = norm("key_pass_per_90", "key_pass_per_90")
    xc_norm  = norm("accurate_cross_per_90", "accurate_cross_per_90")
    xca_norm = (stats.get("cross_accuracy") or 0.0) / CROSS_ACC_LEAGUE_AVG

    w = CREATION_WEIGHTS_BY_PROFILE[profile]
    raw = (
        w["xa"]  * xa_norm
        + w["kp"]  * kp_norm
        + w["xc"]  * xc_norm
        + w["xca"] * xca_norm
    )
    return max(CREATION_MULT_CLAMP[0], min(raw, CREATION_MULT_CLAMP[1]))


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


def calculate_assist_lambda(
    share_xa: float,
    budget_assists: float,
    creation_mult: float,
    xa_conversion: float,
) -> float:
    """Compute final assist λ (top-down allocation)."""
    lam = share_xa * budget_assists * creation_mult * xa_conversion
    return max(CLAMP_LAMBDA_MIN, min(lam, CLAMP_LAMBDA_MAX))
```

- [ ] **Step 4 : Vérifier que les tests passent**

```bash
cd backend && uv run pytest tests/pricing/test_assist_v2.py -v 2>&1 | tail -20
```

Expected : tous `PASSED`.

- [ ] **Step 5 : Commit**

```bash
cd backend && git add app/pricing/assist.py tests/pricing/test_assist_v2.py
git commit -m "feat(pricing): add top-down assist profile detection, creation_mult_v2, xa_conversion, λ"
```

---

## Task 3 — Enrichir la query Bzzoiro (recommendation_service.py)

**Files:**
- Modify: `backend/app/services/recommendation_service.py`

- [ ] **Step 1 : Ajouter les champs manquants au dict `entry` dans `get_recommendations_for_date()`**

Dans la section qui construit `entry` (après `xa_per_90 = ...`), remplacer le bloc `entry = {...}` existant par :

```python
entry = {
    "player_api_id":          stats.player_api_id,
    "xg_per_90":              xg_per_90,
    "xa_per_90":              xa_per_90,
    "npxg_total":             xg_total,
    "xa_total":               stats.expected_assists or 0.0,
    "expected_minutes":       expected_minutes,
    "avg_minutes_per_match":  stats.avg_minutes_per_match or expected_minutes,
    "conversion_rate":        conversion_rate,
    "team":                   team_name,
    "position":               position,
    "goals":                  goals_total,
    "assists":                stats.goal_assist or 0,
    "matches_played":         stats.matches_played or 0,
    # Bzzoiro enriched fields (top-down model)
    "form_xg_5":              stats.form_xg_5,
    "form_assists_5":         stats.form_assists_5,
    "shot_accuracy":          stats.shot_accuracy,
    "xg_per_shot":            stats.xg_per_shot,
    "avg_rating":             stats.avg_rating,
    "key_pass_per_90":        stats.key_pass_per_90,
    "accurate_cross_per_90":  stats.accurate_cross_per_90,
    "cross_accuracy":         stats.cross_accuracy,
}
```

- [ ] **Step 2 : Vérifier que le service tourne sans erreur**

```bash
cd backend && uv run pytest tests/test_recommendations_status.py tests/test_recommendations_view_all.py -v 2>&1 | tail -20
```

Expected : tous `PASSED` (pas de régression).

- [ ] **Step 3 : Commit**

```bash
cd backend && git add app/services/recommendation_service.py
git commit -m "feat(recs): enrich Bzzoiro player stats dict with form, shot, creation fields"
```

---

## Task 4 — Helpers top-down dans recommendation_service.py

**Files:**
- Modify: `backend/app/services/recommendation_service.py`
- Create: `backend/tests/services/test_recommendation_topdown.py`

- [ ] **Step 1 : Écrire les tests qui vont échouer**

Créer `backend/tests/services/test_recommendation_topdown.py` :

```python
"""Tests for top-down share computation helpers in recommendation_service."""

import pytest

from app.services.recommendation_service import _blend_rate, _compute_team_denominators


class TestBlendRate:
    def test_no_form_returns_season_rate(self):
        result = _blend_rate(season_rate=0.30, form_value=None, avg_mins=75.0)
        assert result == pytest.approx(0.30, abs=0.001)

    def test_form_blended_60_40(self):
        # form_value=1.0 xG over 5 matches at 75 min avg
        # form_rate = 1.0 / (5 × 75/90) = 1.0 / 4.167 = 0.24
        # blended = 0.60×0.30 + 0.40×0.24 = 0.18 + 0.096 = 0.276
        result = _blend_rate(season_rate=0.30, form_value=1.0, avg_mins=75.0)
        assert result == pytest.approx(0.276, abs=0.001)

    def test_zero_avg_mins_returns_season_rate(self):
        result = _blend_rate(season_rate=0.20, form_value=2.0, avg_mins=0.0)
        assert result == pytest.approx(0.20, abs=0.001)

    def test_form_zero_blended(self):
        # form_rate = 0.0 → blended = 0.60×0.30 = 0.18
        result = _blend_rate(season_rate=0.30, form_value=0.0, avg_mins=75.0)
        assert result == pytest.approx(0.18, abs=0.001)


class TestComputeTeamDenominators:
    def _make_player(self, team, xg, xa, mins=75.0, form_xg=None, form_xa=None):
        return {
            "team": team, "xg_per_90": xg, "xa_per_90": xa,
            "expected_minutes": mins, "avg_minutes_per_match": mins,
            "form_xg_5": form_xg, "form_assists_5": form_xa,
            "position": "MF",
        }

    def test_single_player_denominator_equals_lambda_when_weight_less(self):
        player_stats = {
            "PlayerA": self._make_player("Home FC", xg=0.20, xa=0.10),
        }
        denoms = _compute_team_denominators(
            player_stats, "Home FC", "Away FC",
            lambda_home=1.5, lambda_away=1.2,
        )
        # weight = 0.20 × (75/90) = 0.167 < lambda_home=1.5 → denom = 1.5
        assert denoms["Home FC"]["goal_denom"] == pytest.approx(1.5, abs=0.001)

    def test_denominator_uses_player_sum_when_larger_than_lambda(self):
        player_stats = {
            "P1": self._make_player("Home FC", xg=1.0, xa=0.5, mins=90.0),
            "P2": self._make_player("Home FC", xg=1.0, xa=0.5, mins=90.0),
        }
        denoms = _compute_team_denominators(
            player_stats, "Home FC", "Away FC",
            lambda_home=1.5, lambda_away=1.2,
        )
        # sum weights = 1.0×1.0 + 1.0×1.0 = 2.0 > lambda_home=1.5 → denom = 2.0
        assert denoms["Home FC"]["goal_denom"] == pytest.approx(2.0, abs=0.001)

    def test_away_team_uses_lambda_away(self):
        player_stats = {
            "P1": self._make_player("Away FC", xg=0.10, xa=0.05),
        }
        denoms = _compute_team_denominators(
            player_stats, "Home FC", "Away FC",
            lambda_home=1.5, lambda_away=0.8,
        )
        assert denoms["Away FC"]["goal_denom"] == pytest.approx(0.8, abs=0.001)

    def test_players_from_other_teams_ignored(self):
        player_stats = {
            "P1": self._make_player("Home FC", xg=0.20, xa=0.10),
            "P2": self._make_player("Third FC", xg=2.0, xa=1.0),  # not in fixture
        }
        denoms = _compute_team_denominators(
            player_stats, "Home FC", "Away FC",
            lambda_home=1.5, lambda_away=1.2,
        )
        assert denoms["Home FC"]["goal_denom"] == pytest.approx(1.5, abs=0.001)

    def test_assist_denom_uses_budget_assists(self):
        player_stats = {
            "P1": self._make_player("Home FC", xg=0.20, xa=0.05),
        }
        denoms = _compute_team_denominators(
            player_stats, "Home FC", "Away FC",
            lambda_home=1.5, lambda_away=1.2,
        )
        # budget_assists = 1.5 × 0.65 = 0.975; player xa weight = 0.05 × (75/90) = 0.042 < 0.975
        assert denoms["Home FC"]["assist_denom"] == pytest.approx(0.975, abs=0.001)
```

- [ ] **Step 2 : Vérifier que les tests échouent**

```bash
cd backend && uv run pytest tests/services/test_recommendation_topdown.py -v 2>&1 | tail -20
```

Expected : `ImportError` sur `_blend_rate` et `_compute_team_denominators`.

- [ ] **Step 3 : Implémenter les helpers dans recommendation_service.py**

Ajouter après les imports, avant `CALIBRATION_SCALE` (ou à la place si déjà supprimé) :

```python
from app.pricing.assist import ASSIST_GOAL_RATE


def _blend_rate(season_rate: float, form_value: float | None, avg_mins: float) -> float:
    """Blend season per-90 rate with last-5-match form (60% season, 40% form).

    form_value is cumulative over 5 matches (e.g. form_xg_5 or form_assists_5).
    Returns season_rate unchanged if form_value is None or avg_mins is 0.
    """
    if form_value is None or avg_mins <= 0:
        return season_rate
    form_rate = form_value / (5.0 * avg_mins / 90.0)
    return 0.60 * season_rate + 0.40 * form_rate


def _compute_team_denominators(
    player_stats: dict[str, dict],
    home_team: str,
    away_team: str,
    lambda_home: float,
    lambda_away: float,
) -> dict[str, dict[str, float]]:
    """Compute top-down share denominators for both teams in a fixture.

    For each team: denominator = max(sum of player weights in DB, λ_team).
    This ensures share_i = weight_i / denom ≤ 1 even when DB covers < 100% of squad.

    Returns: {team_name: {"goal_denom": float, "assist_denom": float}}
    """
    team_goal_weights: dict[str, list[float]] = {home_team: [], away_team: []}
    team_assist_weights: dict[str, list[float]] = {home_team: [], away_team: []}

    for stats in player_stats.values():
        team = stats.get("team")
        if team not in team_goal_weights:
            continue
        avg_mins = stats.get("avg_minutes_per_match") or stats.get("expected_minutes") or 75.0
        mins_ratio = (stats.get("expected_minutes") or 75.0) / 90.0

        blended_xg = _blend_rate(
            stats.get("xg_per_90") or 0.0,
            stats.get("form_xg_5"),
            avg_mins,
        )
        blended_xa = _blend_rate(
            stats.get("xa_per_90") or 0.0,
            stats.get("form_assists_5"),
            avg_mins,
        )
        team_goal_weights[team].append(blended_xg * mins_ratio)
        team_assist_weights[team].append(blended_xa * mins_ratio)

    def denom(weights: list[float], lambda_ref: float, rate: float = 1.0) -> float:
        return max(sum(weights), lambda_ref * rate)

    return {
        home_team: {
            "goal_denom":   denom(team_goal_weights[home_team],   lambda_home),
            "assist_denom": denom(team_assist_weights[home_team], lambda_home, ASSIST_GOAL_RATE),
        },
        away_team: {
            "goal_denom":   denom(team_goal_weights[away_team],   lambda_away),
            "assist_denom": denom(team_assist_weights[away_team], lambda_away, ASSIST_GOAL_RATE),
        },
    }
```

- [ ] **Step 4 : Vérifier que les tests passent**

```bash
cd backend && uv run pytest tests/services/test_recommendation_topdown.py -v 2>&1 | tail -20
```

Expected : tous `PASSED`.

- [ ] **Step 5 : Commit**

```bash
cd backend && git add app/services/recommendation_service.py tests/services/test_recommendation_topdown.py
git commit -m "feat(recs): add _blend_rate and _compute_team_denominators top-down helpers"
```

---

## Task 5 — Câbler les nouveaux modèles dans generate_recommendations()

**Files:**
- Modify: `backend/app/services/recommendation_service.py`

- [ ] **Step 1 : Mettre à jour les imports en tête de fichier**

Remplacer :
```python
from app.pricing.goalscorer import calculate_edge
from app.pricing.team_xg import PEN_CONVERSION, PENS_PER_MATCH
```

Par :
```python
from app.pricing.goalscorer import (
    calculate_edge,
    calculate_finishing_multiplier,
    calculate_conversion_rate,
    calculate_goalscorer_lambda,
)
from app.pricing.assist import (
    ASSIST_GOAL_RATE,
    calculate_creation_multiplier_v2,
    calculate_xa_conversion,
    calculate_assist_lambda,
)
```

Note : `PEN_CONVERSION` et `PENS_PER_MATCH` sont maintenant encapsulés dans `calculate_goalscorer_lambda` — ne pas les importer séparément. Supprimer aussi l'import `from app.pricing.team_xg import ...` s'il ne sert plus à rien d'autre.

- [ ] **Step 2 : Supprimer CALIBRATION_SCALE**

Supprimer la ligne :
```python
CALIBRATION_SCALE = 0.84  # Empirically derived ...
```

- [ ] **Step 3 : Remplacer le bloc λ dans generate_recommendations()**

Dans la boucle `for fixture in fixtures:`, juste avant `for odds_entry in fixture_odds:`, ajouter le calcul des denominateurs :

```python
        # Pre-compute top-down share denominators for this fixture
        team_denoms = _compute_team_denominators(
            player_stats, home_team, away_team,
            home_match_xg, away_match_xg,
        )
```

Puis, dans la boucle interne `for odds_entry in fixture_odds:`, remplacer le bloc :

```python
            if market_type == "goalscorer":
                lambda_base = max(0.001, _xg_per_90 * mins_ratio)
                lambda_penalty = PEN_CONVERSION * PENS_PER_MATCH * mins_ratio if is_pen_taker else 0.0
                lambda_val = lambda_base + lambda_penalty
            else:  # assist
                lambda_val = max(0.001, _xa_per_90 * mins_ratio)

            probability = 1 - math.exp(-lambda_val)
            probability = probability * CALIBRATION_SCALE
            fair_odds = 1 / probability if probability > 0 else 9999.0
            fair_odds = round(fair_odds, 2)
```

Par :

```python
            # `team` est déjà défini plus haut dans la boucle — ne pas le redéfinir
            team_lambda = home_match_xg if team == home_team else away_match_xg
            denom_info = team_denoms.get(team, {})
            avg_mins = stats.get("avg_minutes_per_match") or expected_minutes

            if market_type == "goalscorer":
                blended_xg = _blend_rate(_xg_per_90, stats.get("form_xg_5"), avg_mins)
                weight_i = blended_xg * mins_ratio
                goal_denom = denom_info.get("goal_denom") or team_lambda or 1.0
                share_i = weight_i / goal_denom if goal_denom > 0 else 0.0

                finishing_mult = calculate_finishing_multiplier(stats, position)
                conversion = calculate_conversion_rate(stats)
                lambda_val = calculate_goalscorer_lambda(
                    share_i, team_lambda, finishing_mult, conversion, mins_ratio, is_pen_taker,
                )
            else:  # assist
                blended_xa = _blend_rate(_xa_per_90, stats.get("form_assists_5"), avg_mins)
                weight_xa = blended_xa * mins_ratio
                budget_assists = team_lambda * ASSIST_GOAL_RATE
                assist_denom = denom_info.get("assist_denom") or budget_assists or 1.0
                share_xa = weight_xa / assist_denom if assist_denom > 0 else 0.0

                creation_mult = calculate_creation_multiplier_v2(stats, position)
                xa_conv = calculate_xa_conversion(stats)
                lambda_val = calculate_assist_lambda(share_xa, budget_assists, creation_mult, xa_conv)

            probability = 1 - math.exp(-lambda_val)
            fair_odds = round(1 / probability if probability > 0 else 9999.0, 2)
```

- [ ] **Step 4 : Mettre à jour le champ `team` dans la recommendation**

Le `team` est maintenant calculé plus haut. Retirer le doublon dans le dict `recommendation` si présent (le champ `"team": team` doit utiliser la variable déjà calculée).

- [ ] **Step 5 : Mettre à jour l'explanation dict**

Remplacer le bloc `explanation` existant par :

```python
            explanation = {
                "model": "top_down_v2",
                "xg_source": xg_source,
                "team_lambda": round(team_lambda, 3),
                "expected_minutes": expected_minutes,
                "lambda": round(lambda_val, 4),
                "is_pen_taker": is_pen_taker,
                "market_type": market_type,
            }
```

- [ ] **Step 6 : Vérifier qu'aucune régression**

```bash
cd backend && uv run pytest tests/ -x -q 2>&1 | tail -30
```

Expected : tous les tests existants passent. Corriger si besoin avant de continuer.

- [ ] **Step 7 : Commit**

```bash
cd backend && git add app/services/recommendation_service.py
git commit -m "feat(recs): wire top-down goalscorer+assist models, remove CALIBRATION_SCALE"
```

---

## Task 6 — Mettre à jour le confidence scoring

**Files:**
- Modify: `backend/app/services/recommendation_service.py`

- [ ] **Step 1 : Remplacer le bloc confidence**

Localiser le bloc :
```python
            has_real_xg = stats.get("xg_per_90") is not None and stats.get(
                "xg_per_90"
            ) != pos_defaults.get("xg_per_90")
            if matches >= 10 and has_real_xg:
                confidence = 0.80
            elif matches >= 5 and has_real_xg:
                confidence = 0.65
            elif matches >= 3:
                confidence = 0.55
            else:
                confidence = 0.40
```

Le remplacer par :

```python
            form_key = "form_xg_5" if market_type == "goalscorer" else "form_assists_5"
            rate_key = "xg_per_90" if market_type == "goalscorer" else "xa_per_90"
            has_form = stats.get(form_key) is not None
            has_real = stats.get(rate_key) is not None

            if matches >= 10 and has_form and has_real:
                confidence = 0.85
            elif matches >= 5 and has_real:
                confidence = 0.70
            elif matches >= 3:
                confidence = 0.55
            elif matches >= 1:
                confidence = 0.40
            else:
                confidence = 0.25
```

- [ ] **Step 2 : Supprimer la variable `pos_defaults` si elle n'est plus utilisée**

Vérifier que `pos_defaults` n'est plus référencée ailleurs dans la boucle. Si c'est le cas, retirer les lignes :
```python
            pos_defaults = (
                POSITION_DEFAULTS.get(position, DEFAULT_POSITION_FALLBACK)
                ...
            )
```

- [ ] **Step 3 : Lancer la suite de tests complète**

```bash
cd backend && uv run pytest tests/ -x -q 2>&1 | tail -30
```

Expected : tous `PASSED`.

- [ ] **Step 4 : Commit final**

```bash
cd backend && git add app/services/recommendation_service.py
git commit -m "feat(recs): update confidence scoring with form awareness (0.25–0.85 scale)"
```

---

## Task 7 — Push et vérification VPS

- [ ] **Step 1 : Push**

```bash
git push
```

- [ ] **Step 2 : Rebuild du container backend sur le VPS**

```bash
ssh root@213.130.144.204
cd /etc/dokploy/compose/ev0-compose-z5hvqt/code
docker compose -p ev0-compose-z5hvqt --env-file .env up -d --build --no-deps backend worker
```

- [ ] **Step 3 : Vérifier les logs au démarrage**

```bash
docker logs ev0-compose-z5hvqt-backend-1 --tail 50
```

Expected : pas d'ImportError ni d'erreur au boot.

- [ ] **Step 4 : Déclencher une génération manuelle de recommendations**

```bash
docker exec ev0-compose-z5hvqt-backend-1 python3 -c "
import asyncio
from app.db import get_db_session
from app.services.recommendation_service import get_recommendations_for_date
from datetime import datetime, timezone

async def test():
    async with get_db_session() as db:
        recs, meta = await get_recommendations_for_date(datetime.now(timezone.utc), db)
        print('meta:', meta)
        for r in recs[:3]:
            print(r['player_name'], r['market_type'], 'fair:', r['fair_odds'], 'edge:', round(r['edge'],3))

asyncio.run(test())
"
```

Expected : cotes fair dans des plages raisonnables (buteurs FW ~3–7, passeurs ~4–9). Aucune cote > 15 pour un titulaire habituel.

- [ ] **Step 5 : Vérifier les cotes sur le dashboard**

Ouvrir `http://ev0-compose-z5hvqt-a4f9a7-213-130-144-204.traefik.me` → onglet Recommendations. Vérifier visuellement que les cotes fair sont cohérentes avec les cotes bookmakers.
