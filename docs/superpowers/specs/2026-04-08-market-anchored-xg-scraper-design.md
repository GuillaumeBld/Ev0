# Design — Market-Anchored Team xG Scraper & Scheduler

Date: 2026-04-08
Status: Approved

## Context & Motivation

The current team xG model (Dixon-Coles) produces systematically poor (λ_h, λ_a) estimates → all downstream player pricing is miscalibrated → model in net loss. The fix: infer (λ_h, λ_a) directly from betting market odds, which already embed the collective market view on match outcomes.

The Odds API (paid) is also removed and replaced with free scraping (OddsPortal primary, Betclic + Unibet fallback).

**Full replacements:**
- Dixon-Coles → MarketXgService (market-implied Poisson fit)
- The Odds API match odds feed → OddsPortal scraper + fallback chain

**Unchanged:** player props odds (Betclic gRPC, Unibet LVS for goalscorer/assist markets).

---

## Architecture — 6 new components, 4 modified, 2 deleted

### New
1. `app/ingestion/oddsportal_scraper.py` — Playwright scraper, OddsPortal (primary)
2. `app/ingestion/betclic_match_scraper.py` — Playwright scraper, Betclic H2H+OU+BTTS (fallback, distinct from existing player props scraper)
3. `app/ingestion/unibet_match_scraper.py` — Playwright scraper, Unibet H2H+OU+BTTS (fallback, distinct from existing player props scraper)
4. `app/ingestion/market_scrape_chain.py` — Orchestrates fallback chain
5. `app/ingestion/odds_sanity.py` — Shared sanity check functions
6. `app/services/market_scrape_scheduler.py` — Adaptive token-bucket scheduler

### Modified
1. `app/services/market_xg.py` — Add BTTS constraint, update source preference, update `MarketXgResult`
2. `app/services/recommendation_service.py` — Remove Dixon-Coles, use MarketXgService exclusively
3. `app/worker.py` — Add scheduler job (tick 15s), remove match odds Odds API job
4. Frontend recommendations/matches pages — Add xG source badge

### Deleted (logic removed)
- `job_snapshot_match_odds` job in `worker.py` (The Odds API match odds)
- Dixon-Coles path in `recommendation_service.py` (`compute_team_stats()` call)

---

## Section 1 — Database (Alembic migration)

### 1.1 Extend `match_odds_snapshots` (Option A — minimal migration)

4 nullable columns added. Existing rows unaffected (NULL for legacy data).

```
source          VARCHAR(20)   nullable  -- "oddsportal" | "betclic" | "unibet"
source_url      VARCHAR(500)  nullable  -- URL effectively scraped
parse_version   VARCHAR(20)   nullable  -- e.g. "op-v1", "bc-v1" (DOM change detection)
fallback_used   BOOLEAN       default false
```

**Important:** existing `bookmaker` field continues to be used. For OddsPortal rows: `bookmaker="oddsportal"`. For Betclic fallback: `bookmaker="betclic"`. For Unibet fallback: `bookmaker="unibet"`.

**Market type naming (CRITICAL):** always use `market_type="totals"` for Over/Under 2.5 (NOT "ou_2_5") to stay compatible with existing `MarketXgService` queries.

One scrape cycle = **7 rows** (not 3):
- h2h: 3 rows (outcome: "home", "draw", "away")
- totals: 2 rows (outcome: "over_2.5", "under_2.5")
- btts: 2 rows (outcome: "yes", "no")

### 1.2 New table `oddsportal_poll_state`

One record per fixture. Persistent scheduler state.

```sql
CREATE TABLE oddsportal_poll_state (
    id                   SERIAL PRIMARY KEY,
    fixture_id           INTEGER NOT NULL UNIQUE REFERENCES fixtures(id),
    oddsportal_url       VARCHAR(500) NOT NULL,
    betclic_url          VARCHAR(500),
    unibet_url           VARCHAR(500),
    next_due_at_utc      TIMESTAMPTZ NOT NULL,
    last_scraped_at_utc  TIMESTAMPTZ,
    last_success_at_utc  TIMESTAMPTZ,
    error_streak         INTEGER NOT NULL DEFAULT 0,
    stopped              BOOLEAN NOT NULL DEFAULT false,
    stopped_reason       VARCHAR(50),  -- "T_MINUS_5" | "POSTPONED" | "FINISHED"
    created_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at           TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

**Seeding:** records are created manually via an admin script/endpoint before each gameweek. At minimum `oddsportal_url` is required; `betclic_url` and `unibet_url` are optional fallbacks. V1: manual input. V2 (out of scope): auto-discovery scraper on OddsPortal league pages.

### 1.3 New table `team_xg_estimates`

Append-only time series. No unique constraint on `(fixture_id, as_of_utc)` — collisions are harmless, latest is fetched by `ORDER BY as_of_utc DESC LIMIT 1`.

```sql
CREATE TABLE team_xg_estimates (
    id                   SERIAL PRIMARY KEY,
    fixture_id           INTEGER NOT NULL REFERENCES fixtures(id),
    as_of_utc            TIMESTAMPTZ NOT NULL,
    lambda_home          FLOAT NOT NULL,
    lambda_away          FLOAT NOT NULL,
    fit_residual         FLOAT NOT NULL,
    flagged              BOOLEAN NOT NULL DEFAULT false,  -- residual > 6%
    data_source          VARCHAR(20) NOT NULL,  -- "oddsportal" | "betclic" | "unibet"
    fallback_used        BOOLEAN NOT NULL DEFAULT false,
    input_snapshot_ids   JSONB,  -- [id1, id2, ..., id7] rows used
    created_at           TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX ON team_xg_estimates(fixture_id, as_of_utc DESC);
```

### 1.4 Add `xg_source` to `recommendations`

```sql
ALTER TABLE recommendations ADD COLUMN xg_source VARCHAR(20);
-- "oddsportal" | "betclic" | "unibet" | null (legacy rows)
```

---

## Section 2 — Scrapers

### 2.1 Common interface

Each scraper exposes one async function:

```python
async def scrape_match_markets(url: str, page: Page) -> ScrapeResult
```

```python
@dataclass
class ScrapeResult:
    source: str               # "oddsportal" | "betclic" | "unibet"
    source_url: str
    parse_version: str        # e.g. "op-v1"
    h2h: dict | None          # {"home": 2.05, "draw": 3.30, "away": 3.80}
    totals: dict | None       # {"over_2.5": 1.95, "under_2.5": 1.95}  ← key is "totals"
    btts: dict | None         # {"yes": 1.80, "no": 1.95}
    ingested_at_utc: datetime
    error: str | None
```

### 2.2 Fallback chain (`app/ingestion/market_scrape_chain.py`)

```
1. OddsPortal  (oddsportal_url)  → success → write 7 rows, source="oddsportal", fallback_used=false
2. Betclic     (betclic_url)     → success → write 7 rows, source="betclic",    fallback_used=true
3. Unibet      (unibet_url)      → success → write 7 rows, source="unibet",     fallback_used=true
4. All fail    → error_streak++, nothing written, log scrape_fail with sources_tried list
```

**1 token consumed per chain attempt**, regardless of how many sources are tried.

**"Success"** = all 3 markets present AND pass sanity checks. Stop at first success.

**"Failure"** includes: no URL configured, HTTP error, timeout, selector empty, sanity check fail.

### 2.3 Sanity checks (`app/ingestion/odds_sanity.py`)

```python
def validate_market(market_type: str, odds: dict) -> bool:
    """
    - All expected selections present (h2h: 3, totals: 2, btts: 2)
    - All odds > 1.01
    - No NaN / None
    - Sum of implied probs in [1.0, 1.50]  (bookmaker margin sanity)
    """

def compute_clean_probs(odds: dict) -> dict:
    """
    p_implied[i] = 1 / odds[i]
    p_clean[i]   = p_implied[i] / sum(p_implied)  (simple normalisation, V1)
    assert abs(sum(p_clean) - 1.0) < 1e-6
    """
```

---

## Section 3 — Adaptive Scheduler (`app/services/market_scrape_scheduler.py`)

### 3.1 Token bucket (in-memory state)

```python
max_rpm_hard: float = 5.0
target_rpm:   float = 1.0   # dynamic, range [0.5, 5.0]
tokens:       float = 1.0   # refilled each tick, capped at max_rpm_hard
```

Refill per tick: `tokens = min(tokens + target_rpm/60 * elapsed_s, max_rpm_hard)`

### 3.2 Tick loop (APScheduler IntervalTrigger, ~15s ± 10% jitter)

```
1. Refill tokens
2. Adjust target_rpm (§3.4)
3. Load eligible from DB:
       not stopped
       AND now >= next_due_at_utc
       AND now < start_time_utc - 5min
4. Sort by score DESC: score = 1/(t_minutes + 15) - min(0.5, 0.1 * error_streak)
5. While tokens >= 1.0 and eligible not empty:
       tokens -= 1.0
       fire scrape_chain(match) as async task (non-blocking)
       schedule: next_due_at = now + interval(t) * jitter(0.85..1.15)
       update poll_state in DB
```

### 3.3 Polling intervals by time-to-KO

| t before KO | Interval |
|---|---|
| > 24h | 120 min |
| 6h – 24h | 60 min |
| 2h – 6h | 20 min |
| 30m – 2h | 7 min |
| 5m – 30m | 3 min |
| ≤ 5m | **STOP** → `stopped_reason="T_MINUS_5"` |

### 3.4 Dynamic `target_rpm`

Driven primarily by actual queue depth (`due` = matches currently overdue):

| Condition | `target_rpm` |
|---|---|
| `due == 0` | 1.0 (eco mode) |
| `1 ≤ due ≤ 3` | 2.0 |
| `due > 3` | 3.0 |
| `due > 0` AND `pressure > 10`* | up to `max_rpm_hard` (temporary boost) |

*`pressure` = count of matches with t ∈ (5, 120] minutes.

Always capped by `max_rpm_hard`. Backoff overrides this (§3.5).

### 3.5 Backoff (anti-ban)

Triggered by: HTTP 429, captcha detected, 3 consecutive errors on any source, empty content anomaly.

```
target_rpm = max(0.5, target_rpm * 0.5)
freeze_until = now + 20min  (all matches paused, all sources)
```

Recovery: +0.25 rpm every 10min of clean scrapes until back to normal range.

**Note:** backoff is global (all sources frozen together) in V1. Per-source backoff is out of scope.

---

## Section 4 — MarketXgService enhancements (`app/services/market_xg.py`)

### 4.1 Updated `MarketXgResult`

```python
@dataclass
class MarketXgResult:
    xg_home: float
    xg_away: float
    xg_source: Literal["market_implied", "market_implied_flagged"]  # computation status
    data_source: str        # "oddsportal" | "betclic" | "unibet"   # data origin
    fallback_used: bool
    fit_residual: float
    flagged: bool           # residual > 6%
    as_of_utc: datetime
    input_snapshot_ids: list[int]
```

`"dixon_coles"` is removed from `xg_source` — Dixon-Coles is gone.

### 4.2 Source preference

`_preferred_bookmaker()` updated priority: `"oddsportal"` > `"betfair"` > `"pinnacle"` > `"betclic"` > `"unibet"` > any.

When multiple sources exist within the same 30-min window for a fixture, prefer `"oddsportal"`.

### 4.3 Staleness fix (CRITICAL)

Old logic: reject if `snapshot_utc < kickoff_utc - 24h` → breaks all matches >24h away.

New logic: reject if `now - snapshot_utc > MAX_SNAPSHOT_AGE` where `MAX_SNAPSHOT_AGE = 3h`.
This is independent of kickoff distance.

### 4.4 BTTS as third constraint

Current: sequential brentq solve (λ_t from O/U, then λ_h from H2H).

New: 2D minimisation incorporating BTTS as third constraint:

```python
def _fit_lambdas(
    p_home_win: float,
    p_draw: float,
    p_over_2_5: float,
    p_btts_yes: float,
) -> tuple[float, float]:
    """
    Minimise sum of squared residuals across 3 markets:
      - P(home win) via Poisson(λ_h, λ_a) Skellam truncation
      - P(total > 2.5) via 1 - Poisson_CDF(λ_h+λ_a, 2)
      - P(BTTS yes) via (1 - e^-λ_h)(1 - e^-λ_a)

    Initial point: existing brentq solution (warm start).
    Bounds: λ_h, λ_a ∈ [0.05, 4.5].
    Method: scipy.optimize.minimize with L-BFGS-B.
    Flag if fit_residual > 0.06.
    """
```

---

## Section 5 — Recommendation Service (`app/services/recommendation_service.py`)

### What changes

```python
# REMOVED
team_stats = await compute_team_stats(db)
team_xg = estimate_team_match_xg(fixture, team_stats)

# REPLACED WITH
market_xg = await MarketXgService(db).compute(fixture.id, session)
if market_xg is None:
    logger.warning("fixture %s skipped — no market data", fixture.id)
    continue

team_xg_home = market_xg.xg_home
team_xg_away = market_xg.xg_away
xg_source = market_xg.data_source  # stored on Recommendation.xg_source
```

**No fallback.** If no market data exists for a fixture, that fixture generates no recommendations.

Monitoring alert if >50% of scheduled fixtures in a gameweek have no market data (indicates OddsPortal outage or missing poll_state records).

### After each batch

Write a `team_xg_estimates` row per fixture processed (upsert not needed — append).

---

## Section 6 — Worker (`app/worker.py`)

### Removed
```python
# DELETE this job entirely:
scheduler.add_job(job_snapshot_match_odds, ...)
```

### Added
```python
async def job_oddsportal_scheduler_tick():
    """Token-bucket scheduler tick — fires scrape chains for due fixtures."""
    await MarketScrapeScheduler(db).tick()

scheduler.add_job(
    job_oddsportal_scheduler_tick,
    IntervalTrigger(seconds=15),
    id="job_oddsportal_scheduler_tick",
    jitter=2,
)
```

---

## Section 7 — Frontend

### xG source badge

On recommendations page (`dashboard/recommendations`) and matches page (`dashboard/matches`):

| `xg_source` | Display | Color |
|---|---|---|
| `oddsportal` | `xG · OddsPortal` | green |
| `betclic` | `xG · Betclic (fallback)` | orange |
| `unibet` | `xG · Unibet (fallback)` | orange |
| `null` | `xG · indisponible` | grey |

**Required API change:** `/api/v1/recommendations` response schema must include `xg_source` field.

---

## Section 8 — Observability

### Logs (structured)

Each tick logs:
- `target_rpm`, `tokens_available`, `due_count`, `pressure_count`

Each scrape attempt logs:
- `fixture_id`, `t_to_ko_minutes`, `interval_chosen_minutes`, `next_due_at`
- `source_attempted`, `sources_tried`, `fallback_used`
- `scrape_result`: `success` | `fail`
- On failure: `error_type` (timeout | parsing | sanity | http_error)

### Alerts
- Backoff triggered → log `WARN` with `target_rpm` after degradation
- >50% fixtures without market data in a gameweek → log `ERROR`
- `fit_residual > 0.06` → log `WARN` per fixture with `flagged=true` on `team_xg_estimates`

---

## Out of Scope (V1)

- Multi-worker Playwright parallelisation
- Proxy rotation
- Per-source backoff (global only in V1)
- Automatic OddsPortal URL discovery (manual seed in V1)
- Shin/Power margin removal (simple normalisation only)
- Calibration ML on top of market-implied λ

---

## Key Constants

| Constant | Value | Location |
|---|---|---|
| `MAX_SNAPSHOT_AGE` | 3 hours | `market_xg.py` |
| `max_rpm_hard` | 5.0 | scheduler |
| `tick_interval_s` | 15s ± 10% | worker |
| `fit_residual_flag_threshold` | 0.06 | `market_xg.py` |
| `backoff_freeze_minutes` | 20 | scheduler |
| `T_MINUS_STOP_MINUTES` | 5 | scheduler |
| `lambda_bounds` | [0.05, 4.5] | `market_xg.py` |
