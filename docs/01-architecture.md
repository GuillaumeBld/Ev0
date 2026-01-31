# Architecture

## Modules
1) Ingestion
- Match schedule and metadata
- Team and player stats (xG, xA, minutes, role)
- Lineup signals (predicted and confirmed)
- Odds snapshots by market and selection

2) Storage
- Raw snapshots: store as ingested
- Canonical tables: normalized, deduplicated, keyed by stable IDs

3) Feature pipeline
- Transform raw stats to per-90, rolling windows, recency decay
- Opponent adjustments and home/away effects
- Minutes distributions and lineup scenarios
- Market mapping and selection normalization

4) Pricing engine
- Match-level team expected goals
- Allocation to player intensities (lambda)
- Convert to event probabilities and fair odds
- Calibration layer

5) Strategy engine
- Margin removal and market probability extraction
- Edge computation
- Filters and constraints
- Stake sizing
- Exposure and correlation controls

6) Backtest engine
- Time-based splits and walk-forward evaluation
- CLV computation using a consistent closing proxy
- Calibration and scoring metrics
- Strategy evaluation, confidence intervals

7) Monitoring and UI
- Dashboard of recommendations, model diagnostics, and logs
- Alerts for data gaps, drift, and CLV decay

## Data flow
- Ingest -> validate -> store raw snapshot -> canonicalize -> features -> price -> compare -> decide -> log -> evaluate.

## Service layout
- `api-service`: FastAPI for pricing and UI API
- `worker`: scheduled ingestion and feature updates
- `db`: PostgreSQL
- `frontend`: Next.js dashboard

## Key design rules
- Every record has a timestamp in UTC.
- Every model output is reproducible from stored inputs.
- Every recommendation includes an explanation payload.
