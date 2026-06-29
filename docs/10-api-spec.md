# API specification (v2)

Base URL : `/api/v1`

---

## 1. Fixtures

**`GET /fixtures`**
Paramètres : `status` (`scheduled`|`finished`|`all`), `limit`, `upcoming_only` (bool), `league`.
Retourne la liste des fixtures avec équipes, date, statut, ligue.

**`GET /fixtures/{id}/lineups`**
Compos prédites ou confirmées pour un match donné.

---

## 2. Pricing match (Calculateur)

### `POST /price/match`

Calcule le pricing top-down pour tous les joueurs d'un match.

**Request body :**
```json
{
  "fixture_id": 12345,
  "home_xg_override": 1.8,          // optionnel — force le xG domicile
  "away_xg_override": 1.1,          // optionnel — force le xG extérieur
  "home_pen_taker_override": 42,     // optionnel — player_id du tireur de penalty DOM
  "away_pen_taker_override": 87,     // optionnel — player_id du tireur de penalty EXT
  "home_starters": ["slug1", ...],   // optionnel — redistribue xG sur ces titulaires
  "away_starters": ["slug1", ...]    // optionnel
}
```

**Response :**
```json
{
  "fixture_id": 12345,
  "home_team": "PSG",
  "away_team": "Lyon",
  "home_match_xg": 1.82,
  "away_match_xg": 1.14,
  "xg_source": "bzzoiro",            // "bzzoiro" | "market_implied" | "market_implied_flagged" | "override"
  "last_scraped_at": "2026-06-29T14:32:00Z",  // ISO — âge des cotes Bzzoiro
  "p00": 0.072,                      // P(score 0-0) — Poisson indépendant
  "home_players": [ <PlayerAllocationOut>, ... ],
  "away_players": [ <PlayerAllocationOut>, ... ],
  "home_lineup_players": null,       // non-null si home_starters fournis
  "away_lineup_players": null
}
```

**`PlayerAllocationOut` :**
```json
{
  "player_id": 42,
  "player_name": "Mbappé K.",
  "team": "PSG",
  "position": "FW",                  // "FW" | "MF" | "DF" | "GK" | null
  "expected_minutes": 87.0,
  "is_pen_taker": true,
  "npxg_share": 0.312,
  "xa_share": 0.089,
  "lambda_open_play": 0.48,
  "lambda_penalty": 0.11,
  "lambda_total": 0.59,
  "prob_goal": 0.446,
  "fair_odds_goal": 2.24,
  "lambda_assist": 0.21,
  "prob_assist": 0.189,
  "fair_odds_assist": 5.29,
  // Supersub
  "p_sub": 0.12,                     // P(jouer comme remplaçant entrant)
  "avg_sub_time": 65.0,              // minute d'entrée estimée si remplaçant
  "p_goal_supersub": 0.431,          // P(marquer, mécanique supersub)
  "fair_odds_goal_supersub": 2.32,
  "p_assist_supersub": 0.185,
  "fair_odds_assist_supersub": 5.41,
  "sub_premium_goal": 0.015,         // prime supersub sur le but
  "sub_premium_assist": 0.008
}
```

---

## 3. Tireur de penalty

**`GET /pen-takers/{fixture_id}`**
Retourne le tireur de penalty enregistré pour chaque équipe du match.
```json
{ "home_pen_taker_id": 42, "away_pen_taker_id": 87 }
```

**`POST /pen-takers`**
Sauvegarde le tireur de penalty pour un match.
```json
{
  "fixture_id": 12345,
  "home_pen_taker_id": 42,
  "away_pen_taker_id": 87
}
```

---

## 4. Recommandations

**`GET /recommendations`**
Paramètres : `phase` (`EARLY`|`LINEUP`|`all`), `limit`, `league`.

Champs clés de la réponse :
- `market_type` : `"goal"` | `"assist"` | `"supersub"`
- `supersub_market_type` : `"standard"` | `"supersub"` (précise le sous-type)
- `edge` : edge en % (cote fair vs cote book)
- `p_starter` : probabilité d'être titulaire
- `expected_minutes` : minutes attendues

---

## 5. CDM 2026

**`GET /wc2026/matches`**
Liste des matchs WC2026. Champs : `bzz_id`, `home_team`, `away_team`, `round_number`, `event_date`, `status`, `home_score`, `away_score`, `home_xg`, `away_xg`.

**`GET /wc2026/matches/{id}`**
Détail d'un match : stats joueurs, compo, xG par joueur.

**`GET /wc2026/advancement`**
Probabilités d'avancement par nation (résultat du Monte Carlo ELO).
```json
[
  {
    "nation": "France",
    "elo": 1641.0,
    "p_r32": 0.94, "p_r16": 0.71, "p_qf": 0.48,
    "p_sf": 0.31, "p_finalist": 0.19, "p_winner": 0.11,
    "e_games": 5.43,
    "n_sim": 50000,
    "computed_at": "2026-06-29T10:00:00Z"
  }
]
```

**`GET /wc2026/pricing`**
Cotes fair outright CDM par joueur.
Champs : `nation`, `player_name`, `position`, `lambda_goal`, `lambda_assist`, `p_top_scorer`, `fair_odds_top_scorer`, `p_top_assister`, `fair_odds_top_assister`.

**`POST /wc2026/sync-stats`**
Déclenche manuellement la synchro des stats joueurs WC2026 (matchs terminés sans stats).
Retourne : `{ "synced": 3, "skipped": 12, "errors": [] }`.

---

## 6. Health & Admin

**`GET /health`**
Statut général du système.

**`GET /health/scrapers`**
Fraîcheur des cotes par bookmaker et par ligue.
