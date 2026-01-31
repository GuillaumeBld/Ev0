# Stack technique

## Backend
- **Python 3.11+**
- **FastAPI** pour les APIs
- **Pandas, NumPy** pour le traitement de données
- **SciPy** pour distributions Poisson et stats
- **scikit-learn** pour calibration (isotonique, Platt scaling)
- **SQLAlchemy** pour l'accès DB
- **Pydantic** pour validation des schémas
- **APScheduler** ou **Celery** pour jobs planifiés

## Scraping / Data
- **soccerdata** (probberechts) - FBref, Understat, FotMob
- **ScraperFC** (oseymour) - Alternative pip installable
- **httpx** ou **requests** pour APIs
- **beautifulsoup4** / **selectolax** pour parsing HTML

## Storage
- **PostgreSQL** comme datastore principal
- **Redis** optionnel pour cache et état des jobs
- **Object storage** optionnel pour snapshots bruts (Parquet)

## Frontend
- **Next.js** (React) avec TypeScript
- **Tailwind CSS** pour le styling
- **TanStack Query** (React Query) pour data fetching
- **Recharts** ou **Chart.js** pour les graphiques

## DevOps
- **Docker** et **Docker Compose**
- **GitHub Actions** pour CI (lint, tests, type checks)
- **Pre-commit hooks** (ruff, black, mypy)
- Observabilité :
  - Prometheus metrics endpoint
  - Grafana dashboards optionnel

## Sources de cotes
- **The Odds API** (https://the-odds-api.com) - Agrégateur
- **Scraping** Betclic, Parions Sport, Unibet (avec respect ToS)

## Repos de référence
| Repo | Usage |
|------|-------|
| `probberechts/soccerdata` | Scraping FBref, Understat |
| `oseymour/ScraperFC` | Alternative scraping |
| `ML-KULeuven/soccer_xg` | Modèles xG |
| `dashee87/blogScripts` | Baseline Poisson |
| `eddwebster/football_analytics` | Notebooks référence |

## Structure du projet
```
Ev0/
├── backend/
│   ├── api/              # FastAPI app
│   ├── ingestion/        # Jobs d'ingestion
│   ├── models/           # Modèles SQLAlchemy
│   ├── pricing/          # Moteurs de pricing
│   │   ├── goalscorer.py # Module Buteur
│   │   └── assist.py     # Module Passeur
│   ├── strategy/         # Logique de sélection
│   └── backtest/         # Framework de backtest
├── frontend/
│   └── ...               # Next.js app
├── infra/
│   ├── docker-compose.yml
│   └── ...
├── docs/
│   └── ...               # Documentation
└── tests/
    └── ...
```
