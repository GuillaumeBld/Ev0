# Bzzoiro API Integration — Design Spec
**Date:** 2026-04-09
**Status:** Approved

---

## 1. Objective

Replace the current multi-source scraping stack (Understat + Sofascore + OddsPortal) with the
Bzzoiro Sports Data API as the **primary data source** for the entire Ev0 project.

Goals:
- Clean, unified player data library covering all 30+ match-level stats
- Elimination of scraping fragility and VPS-side Cloudflare blocks
- Stable cross-entity IDs (players, teams, events) that eliminate name-matching issues
- Rich derived metrics stored in DB to power a more precise pricing engine
- Visual xG mode indicator (API 🟦 vs MODEL 🟧) across all relevant UI surfaces
- Understat and Sofascore demoted to silent fallback only

---

## 2. API Overview

**Base URL:** `https://sports.bzzoiro.com`
**Auth:** `Authorization: Token <token>` header
**Cost:** Free, no rate limits for registered users

**Relevant endpoints:**

| Endpoint | Purpose |
|----------|---------|
| `GET /api/players/` | Player profiles (identity, physical, team, transfers) |
| `GET /api/player-stats/` | Per-match stats per player (30 fields) |
| `GET /api/events/` | Matches with odds, lineups, shotmap, momentum, xG |
| `GET /api/predictions/` | ML predictions per match (prob_over_25, prob_btts, etc.) |
| `GET /api/teams/` | Team profiles |
| `GET /api/leagues/` | League catalogue |
| `GET /api/live/` | Live scores with incidents and live team stats |

---

## 3. Database Schema

All existing `player_stats`, `players`, `teams` tables are **dropped and replaced**.
Bzzoiro-native tables use `bzz_` prefix for clarity.

### 3.1 `bzz_leagues`
```
id              SERIAL PK
api_id          INTEGER UNIQUE NOT NULL   -- Bzzoiro league ID
name            VARCHAR(100)
country         VARCHAR(100)
season_id       INTEGER
synced_at       TIMESTAMPTZ
```

### 3.2 `bzz_teams`
```
id              SERIAL PK
api_id          INTEGER UNIQUE NOT NULL
name            VARCHAR(200)
short_name      VARCHAR(50)
country         VARCHAR(100)
league_api_id   INTEGER FK → bzz_leagues.api_id
synced_at       TIMESTAMPTZ
```

### 3.3 `bzz_players`
```
id              SERIAL PK
api_id          INTEGER UNIQUE NOT NULL
name            VARCHAR(200) INDEX
short_name      VARCHAR(100)
nationality     VARCHAR(100)
date_of_birth   DATE
height          INTEGER         -- cm, nullable
jersey_number   INTEGER
position        VARCHAR(5)      -- G / D / M / F
market_value    BIGINT          -- EUR, nullable
current_team_api_id  INTEGER FK → bzz_teams.api_id
national_team_api_id INTEGER FK → bzz_teams.api_id (nullable)
synced_at       TIMESTAMPTZ
```

### 3.4 `bzz_events`
```
id              SERIAL PK
api_id          INTEGER UNIQUE NOT NULL
league_api_id   INTEGER FK → bzz_leagues.api_id
home_team_api_id INTEGER FK → bzz_teams.api_id
away_team_api_id INTEGER FK → bzz_teams.api_id
event_date      TIMESTAMPTZ INDEX
status          VARCHAR(30)     -- notstarted/inprogress/finished/postponed/cancelled
period          VARCHAR(5)      -- 1T/HT/2T/FT
current_minute  INTEGER
round_number    INTEGER

-- Scores
home_score      INTEGER
away_score      INTEGER
home_score_ht   INTEGER
away_score_ht   INTEGER

-- xG (actual, from Bzzoiro)
home_xg         FLOAT
away_xg         FLOAT

-- Spatial data (JSONB — used for visualisation)
shotmap         JSONB           -- [{min, type, sit, body, home, xg, xgot, pos{x,y}, gm{y,z}, pid}]
incidents       JSONB           -- [{type, minute, player_name, is_home, sequence[]}]
momentum        JSONB           -- [{minute, value}]
average_positions JSONB         -- {home: [{player, pid, pos{x,y}, number}], away: [...]}
lineups         JSONB           -- {home: [player_api_ids], away: [...]}

-- Odds (from event response)
odds_1x2        JSONB           -- {home, draw, away}
odds_over_under JSONB           -- {over_15, under_15, over_25, under_25, over_35, under_35}
odds_btts       JSONB           -- {yes, no}

synced_at       TIMESTAMPTZ
```

### 3.5 `bzz_player_match_stats`
One row per player per match. Source of truth for all player performance data.

```
id              SERIAL PK
player_api_id   INTEGER FK → bzz_players.api_id  INDEX
event_api_id    INTEGER FK → bzz_events.api_id    INDEX
team_api_id     INTEGER FK → bzz_teams.api_id
is_home         BOOLEAN

-- Presence
minutes_played  INTEGER
rating          FLOAT           -- 1–10, nullable
touches         INTEGER

-- Attacking
goals           INTEGER
goal_assist     INTEGER
expected_goals  FLOAT           -- xG
expected_assists FLOAT          -- xA
total_shots     INTEGER
shots_on_target INTEGER

-- Passing
total_pass      INTEGER
accurate_pass   INTEGER
key_pass        INTEGER
total_long_balls    INTEGER
accurate_long_balls INTEGER

-- Crossing
total_cross     INTEGER
accurate_cross  INTEGER

-- Duels
duel_won        INTEGER
duel_lost       INTEGER
aerial_won      INTEGER
aerial_lost     INTEGER

-- Defense
total_tackle    INTEGER
won_tackle      INTEGER
total_clearance INTEGER
interception    INTEGER
ball_recovery   INTEGER

-- Discipline / Possession
yellow_card     INTEGER
red_card        INTEGER
fouls           INTEGER
was_fouled      INTEGER
dispossessed    INTEGER
possession_lost INTEGER

-- Goalkeeper
saves           INTEGER
goals_conceded  INTEGER

-- ── Derived metrics (computed on insert/update) ──────────────────
shot_accuracy           FLOAT   -- shots_on_target / total_shots
xg_per_shot             FLOAT   -- expected_goals / total_shots
finishing_delta         FLOAT   -- goals - expected_goals
xa_delta                FLOAT   -- goal_assist - expected_assists
pass_completion         FLOAT   -- accurate_pass / total_pass
long_ball_accuracy      FLOAT   -- accurate_long_balls / total_long_balls
cross_accuracy          FLOAT   -- accurate_cross / total_cross
duel_win_rate           FLOAT   -- duel_won / (duel_won + duel_lost)
aerial_win_rate         FLOAT   -- aerial_won / (aerial_won + aerial_lost)
tackle_success_rate     FLOAT   -- won_tackle / total_tackle

UNIQUE(player_api_id, event_api_id)
```

### 3.6 `bzz_player_season_stats`
Aggregated per player per season per league. Recomputed after each sync.

```
id              SERIAL PK
player_api_id   INTEGER FK → bzz_players.api_id
league_api_id   INTEGER FK → bzz_leagues.api_id
season          VARCHAR(10)     -- e.g. "2025-2026"
as_of_utc       TIMESTAMPTZ

-- Totals (season cumulative)
matches_played  INTEGER
minutes_played  INTEGER
starts          INTEGER
goals           INTEGER
goal_assist     INTEGER
expected_goals  FLOAT
expected_assists FLOAT
total_shots     INTEGER
shots_on_target INTEGER
total_pass      INTEGER
accurate_pass   INTEGER
key_pass        INTEGER
total_cross     INTEGER
accurate_cross  INTEGER
total_long_balls INTEGER
accurate_long_balls INTEGER
duel_won        INTEGER
duel_lost       INTEGER
aerial_won      INTEGER
aerial_lost     INTEGER
total_tackle    INTEGER
won_tackle      INTEGER
interception    INTEGER
ball_recovery   INTEGER
yellow_card     INTEGER
red_card        INTEGER
saves           INTEGER

-- Per-90 rates (computed)
xg_per_90               FLOAT
xa_per_90               FLOAT
shots_per_90            FLOAT
shots_on_target_per_90  FLOAT
key_pass_per_90         FLOAT
accurate_cross_per_90   FLOAT
recoveries_per_90       FLOAT
tackles_per_90          FLOAT
interceptions_per_90    FLOAT

-- Efficiency metrics (computed)
shot_accuracy           FLOAT   -- SoT / shots
xg_per_shot             FLOAT   -- xG / shots
finishing_delta         FLOAT   -- goals - xG (season)
xa_delta                FLOAT   -- assists - xA (season)
pass_completion         FLOAT
long_ball_accuracy      FLOAT
cross_accuracy          FLOAT
duel_win_rate           FLOAT
aerial_win_rate         FLOAT
tackle_success_rate     FLOAT
avg_rating              FLOAT   -- mean rating over starts

-- Playing time profile
avg_minutes_per_match   FLOAT
starts_pct              FLOAT   -- starts / matches

-- Form (rolling 5 matches, recomputed each sync)
form_xg_5               FLOAT   -- sum xG last 5 matches
form_rating_5           FLOAT   -- avg rating last 5
form_goals_5            INTEGER
form_assists_5          INTEGER
rating_trend            FLOAT   -- form_rating_5 - avg_rating

UNIQUE(player_api_id, league_api_id, season)
```

### 3.7 `bzz_predictions`
One row per event (ML predictions from Bzzoiro).

```
id              SERIAL PK
event_api_id    INTEGER UNIQUE FK → bzz_events.api_id
created_at      TIMESTAMPTZ

-- 1X2
prob_home_win   FLOAT
prob_draw       FLOAT
prob_away_win   FLOAT
predicted_result VARCHAR(1)      -- H / D / A

-- Expected goals
expected_home_goals FLOAT
expected_away_goals FLOAT

-- Markets
prob_over_15    FLOAT
prob_over_25    FLOAT
prob_over_35    FLOAT
prob_btts_yes   FLOAT

-- Model metadata
confidence      FLOAT
model_version   VARCHAR(50)
most_likely_score VARCHAR(10)    -- e.g. "2-1"
favorite        VARCHAR(1)       -- H / A / null
favorite_prob   FLOAT

-- Recommendation flags
favorite_recommend      BOOLEAN
over_15_recommend       BOOLEAN
over_25_recommend       BOOLEAN
over_35_recommend       BOOLEAN
btts_recommend          BOOLEAN
winner_recommend        BOOLEAN
```

---

## 4. Ingestion Layer

### 4.1 Files (new, replacing scraping stack)

```
backend/app/ingestion/bzzoiro/
    __init__.py
    client.py           -- BzzoiroClient: authenticated HTTP session, pagination helper
    sync_leagues.py     -- Sync bzz_leagues
    sync_teams.py       -- Sync bzz_teams
    sync_players.py     -- Sync bzz_players (profile + transfers)
    sync_events.py      -- Sync bzz_events (scores, odds, lineups, shotmap, momentum)
    sync_player_stats.py -- Sync bzz_player_match_stats per event, compute derived metrics
    sync_predictions.py -- Sync bzz_predictions
    aggregate.py        -- Compute bzz_player_season_stats from match stats
```

### 4.2 Deprecated (archived, not deleted)

```
backend/app/ingestion/understat_scraper.py    → fallback only (not called by worker)
backend/app/ingestion/sofascore_scraper.py    → fallback only
backend/app/ingestion/player_stats.py         → archived
backend/app/ingestion/sync_all_players.py     → replaced by bzzoiro/sync_players.py
```

### 4.3 Worker jobs

| Job | Frequency | Description |
|-----|-----------|-------------|
| `job_sync_bzzoiro_events` | Every 6h | Sync upcoming + recent events |
| `job_sync_bzzoiro_player_stats` | Every 6h | Sync match stats for finished events |
| `job_sync_bzzoiro_predictions` | Daily 07:00 | Sync predictions for next 48h |
| `job_aggregate_season_stats` | Daily 04:00 | Recompute bzz_player_season_stats |
| `job_sync_bzzoiro_players` | Daily 03:00 | Sync player profiles + team assignments |

---

## 5. xG Pricing Modes

### 5.1 Toggle

Global setting stored in `app_config` table:
```
xg_source: "bzzoiro" | "model"   (default: "bzzoiro")
```

Exposed via:
- `GET /api/config/xg-source` → current mode
- `PATCH /api/config/xg-source` → toggle

### 5.2 Resolution logic (per match)

```python
def resolve_xg_source(event_api_id: int, global_mode: str) -> tuple[float, float, str]:
    """Returns (home_xg, away_xg, source_label)"""
    if global_mode == "bzzoiro":
        event = get_bzz_event(event_api_id)
        if event.home_xg is not None and event.away_xg is not None:
            return event.home_xg, event.away_xg, "bzzoiro"
        # Silent fallback
    return compute_model_xg(event_api_id), "model"
```

### 5.3 Visual indicators

Every xG value displayed in the UI carries a badge:

| Badge | Color | Meaning |
|-------|-------|---------|
| `API` | Blue `#3B82F6` | xG from Bzzoiro |
| `MODEL` | Orange `#F97316` | xG from internal Dixon-Coles engine |

Surfaces where the badge appears:
- Dashboard match cards
- Recommendations (each player card)
- Historique (each resolved bet)
- Calculateur (xG inputs)
- Page Joueurs (player detail)

---

## 6. Pricing Engine Updates

### 6.1 Quality multiplier (goalscorer) — updated inputs

**Before:** `SOT_per_90 + TAP_per_90 + xGChain_per_90` (Understat + Sofascore)

**After:** `shot_accuracy + xg_per_shot + rating_normalized`

All three drawn directly from `bzz_player_season_stats`, no source merging required.

### 6.2 Creation multiplier (assist) — updated inputs

**Before:** `BCC_per_90 + xGChain_per_90 + crosses_per_90 + TB_per_90`

**After:** `key_pass_per_90 + accurate_cross_per_90 + xa_per_90`

### 6.3 Form factor

**Before:** Manually computed via `calculate_form_factor()` with decay lambda.

**After:** Read directly from `bzz_player_season_stats.form_xg_5` and `form_rating_5` — pre-computed at sync time.

### 6.4 Finishing correction (new)

`finishing_delta` (goals − xG over the season) stored per player. Applied as a small correction
to `lambda_open_play` for players who systematically over/under-perform their xG.

---

## 7. Page Joueurs — Frontend Refonte

### 7.1 List view columns

`Name | Team | Position | xG/90 | xA/90 | Rating | SoT/90 | Form (last 5) | Minutes`

Sortable by any column. Filter by league, team, position, min_minutes.

### 7.2 Player detail (click/drill-down)

**Section 1 — Season summary**
All fields from `bzz_player_season_stats`: totals + per-90 + efficiency rates + profile.

**Section 2 — Multi-metric chart** (match by match)
Y-axes (toggleable): `xG`, `rating`, `goals`, `assists`, `shots_on_target`, `key_pass`
X-axis: match date
Each point tooltip: opponent, result, minutes played.

**Section 3 — Computed profile**
Auto-categorized player archetype based on stats:
- Finisher: high xg_per_shot + shot_accuracy
- Creator: high key_pass_per_90 + xa_per_90
- Physical: high duel_win_rate + recoveries_per_90
- Complete: balanced across categories

---

## 8. Other Parts — Improvements via Bzzoiro

### 8.1 Odds
- Remove OddsPortal scraper and poll state entirely
- Odds (1X2, Over/Under 1.5/2.5/3.5, BTTS) read from `bzz_events.odds_*` JSONB columns
- Refreshed every 6h with event sync

### 8.2 Recommendations
- New column: **Bzzoiro ML** — `prob_over_25`, `prob_btts_yes` from `bzz_predictions`
- Divergence indicator: if Ev0 edge > threshold but Bzzoiro ML disagrees → yellow flag
- `confidence` score from Bzzoiro shown alongside edge %

### 8.3 Mode Compo
- Lineups from `bzz_events.lineups` JSONB — home/away starters + subs
- Average positions from `bzz_events.average_positions` — tactical view per player
- No separate scraping needed

### 8.4 Dashboard — Live
- Momentum chart for in-progress matches (minute-by-minute pressure visualization)
- Incidents feed from `bzz_live.incidents`
- Live team stats: possession, shots, corners

### 8.5 Shotmap (match detail)
- Per-shot visualization with xG and xGoT on pitch graphic
- Filterable by player, situation (open-play / set-piece / corner)

---

## 9. Migration Plan

1. Add Bzzoiro API key to `.env` + Dokploy DB
2. Run Alembic migration: create 7 new `bzz_*` tables
3. Initial sync: leagues → teams → players → events → player_stats → predictions
4. Verify data quality on 2 leagues (Ligue 1 + PL)
5. Drop old tables: `player_stats`, repoint `players`/`teams` FK references
6. Update pricing engine to read from `bzz_player_season_stats`
7. Update worker job schedule
8. Frontend: update `/players` page, add xG mode badge everywhere

---

## 10. Out of Scope

- Tennis and CS2 endpoints (beta, irrelevant to Ev0)
- Shotmap visualization (deferred — Phase 2)
- Live momentum chart (deferred — Phase 2)
- Bzzoiro ML recommendations as autonomous bets (cross-validation only, human approval required)
