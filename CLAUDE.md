# Ev0 — Claude Code Project Notes

## Architecture

- **Backend**: FastAPI + SQLAlchemy (async) + PostgreSQL + Redis, in `backend/`
- **Frontend**: Next.js, in `frontend/`
- **Deployment**: Docker Compose on a VPS (Traefik reverse proxy)

## Key Commands (backend)

```bash
cd backend
uv run pytest tests/ -x -q          # run tests
uv run ruff check .                  # lint
alembic upgrade head                 # apply DB migrations
uv run python -m app.worker          # start background worker (fixture sync, odds, recommendations)
```

## Database

PostgreSQL runs **on the VPS only** (inside Docker Compose as the `db` service). There is no local Postgres on the dev machine. Any script that writes to the database must be run on the VPS or through an SSH tunnel.

Connection string (from `.env`): `postgresql://ev0:<password>@db:5432/ev0`

## Historical Backfill (MUST RUN ON VPS)

The backfill pipeline populates the database with past-season data for backtesting. It **requires database access** and must be run on the VPS where Postgres is running.

### Steps (run inside the backend container or on the VPS)

```bash
# 1. Ensure migrations are applied
alembic upgrade head

# 2. Backfill fixtures (~5 sec, 2-4 API calls to FotMob)
python -m app.scripts.backfill --step fixtures

# 3. Backfill match events (~13 min, ~760 API calls at 1 req/sec)
# Use --limit to run in batches if needed
python -m app.scripts.backfill --step events
python -m app.scripts.backfill --step events --limit 100  # first 100 only

# 4. Backfill player stats (~10 sec, 2 API calls to Understat)
python -m app.scripts.backfill --step stats

# 5. Generate synthetic odds (~30 sec, 0 API calls — pure computation)
python -m app.scripts.backfill --step odds

# Or run all steps at once:
python -m app.scripts.backfill
```

### Options

- `--league ligue_1|premier_league` — default: both
- `--step fixtures|events|stats|odds|all` — default: all
- `--limit N` — max fixtures for events step (for batching)
- `--season 2024-2025` — default: 2024-2025

### Monitoring

```bash
# Check database coverage after backfill
python -m app.scripts.db_status

# Check backtest results (requires backfill to be complete)
python -m app.scripts.forward_test_report
```

### What the backfill produces

| Step | What it creates | Source |
|------|----------------|--------|
| fixtures | ~760 Fixture records (2 leagues × ~380 matches) | FotMob API |
| events | MatchEvent records (goals, assists) for each finished fixture | FotMob API |
| stats | PlayerStats + Player records from Understat | Understat API |
| odds | Synthetic OddsSnapshot records (bookmaker="synthetic") | Poisson model + margin/noise |

The synthetic odds allow the backtest pipeline (`POST /backtest/simulate`) to validate model calibration and edge detection. Real odds come from the worker's hourly odds collection job (forward testing).

## Backtest API

```bash
# Run backtest on backfilled data
curl -X POST http://localhost:8000/backtest/simulate \
  -H "Content-Type: application/json" \
  -d '{"league": "ligue_1", "min_date": "2024-08-01", "max_date": "2025-06-30"}'
```
