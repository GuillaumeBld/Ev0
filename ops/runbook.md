# Runbook

## Daily checks
- Data ingestion jobs succeeded
- Next 48h fixtures present
- Odds snapshots present for target markets
- Lineup signals are populated for near-term fixtures

## Incident: odds ingestion failure
1) Check source availability.
2) Check parser errors and schema version.
3) Disable affected source, fall back to alternative if available.
4) Re-run ingestion for affected window.
5) Mark data gaps in UI.

## Incident: player mapping errors spike
1) Check recent transfers and name variants.
2) Add mapping override.
3) Re-run canonicalization for impacted fixtures.

## Historical backfill (one-time setup)

The backfill populates the database with past-season fixtures, match events,
player stats, and synthetic odds for backtesting. It **must run on the VPS**
where PostgreSQL is accessible.

### Prerequisites
- PostgreSQL running (`docker compose up -d db`)
- Migrations applied: `alembic upgrade head`

### Run (inside the backend container or on the VPS)
```bash
cd backend

# Step by step (~15 min total):
python -m app.scripts.backfill --step fixtures       # ~5 sec
python -m app.scripts.backfill --step events          # ~13 min (or --limit 100)
python -m app.scripts.backfill --step stats           # ~10 sec
python -m app.scripts.backfill --step odds            # ~30 sec

# Or all at once:
python -m app.scripts.backfill
```

### Verify
```bash
python -m app.scripts.db_status            # table counts, coverage %
python -m app.scripts.forward_test_report  # Brier score, ROI, calibration
```

### Options
- `--league ligue_1|premier_league` (default: both)
- `--step fixtures|events|stats|odds|all` (default: all)
- `--limit N` (events step only — process N fixtures then stop)
- `--season 2024-2025` (default: 2024-2025)

### Notes
- The events step makes ~760 HTTP requests at 1 req/sec — takes ~13 min
- Use `--limit 100` to run in batches; re-running skips already-processed fixtures
- All steps are idempotent (safe to re-run)
- Synthetic odds use seed=42 for reproducibility
