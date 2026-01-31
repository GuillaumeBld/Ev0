# API specification (v1.1)

Base URL: `/api/v1`

## Endpoints

### 1. Fixtures & Status
*   **GET `/fixtures?date=...`** : Liste des matchs.
*   **GET `/fixtures/{id}/lineups`** : Récupère les compos (prédites ou confirmées).

### 2. Pricing & Manual Overrides (Mode Expert)
*   **POST `/pricing/calculate`**
    *   *Body* : `{ "fixture_id": "...", "team_xg_override": { "home": 2.1, "away": 1.2 } }`
    *   *Desc* : Calcule les probas avec les réglages manuels.
*   **POST `/fixtures/{id}/lineups/confirm`**
    *   *Body* : `{ "home_starters": ["id1", "id2"...], "away_starters": [...] }`
    *   *Desc* : Force le passage en **Phase LINEUP** et recalcule tout instantanément.

### 3. Recommendations
*   **GET `/recommendations?phase=EARLY`**
*   **GET `/recommendations?phase=LINEUP`**

### 4. Backtest & Admin
*   **POST `/backtest/run`**
*   **GET `/health/data`** : État des scrapers et fraîcheur des cotes.