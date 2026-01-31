# Developer setup

## Local requirements
- Docker and Docker Compose
- Python 3.11+
- Node.js 18+

## Suggested repo layout
- backend/
  - app/
  - tests/
- worker/
- frontend/
- infra/
  - docker-compose.yml
- docs/
- ui/

## Dev steps
1) Start DB via docker compose.
2) Run migrations.
3) Run worker in dev mode to ingest fixtures and snapshots.
4) Run backend API.
5) Run frontend dashboard.

## Quality gates
- Unit tests for:
  - name normalization
  - margin removal
  - pricing math
  - scenario mixtures
- Integration tests for:
  - ingestion -> canonicalization -> pricing -> recommendation flow
- Static checks:
  - ruff, mypy
