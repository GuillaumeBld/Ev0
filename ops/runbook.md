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
