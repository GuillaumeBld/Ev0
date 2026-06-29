# Stack technique

## Backend

| Lib | Usage |
|-----|-------|
| Python 3.13 | Langage principal |
| FastAPI | API REST |
| SQLAlchemy (async) | ORM + requêtes DB |
| Alembic | Migrations DB |
| APScheduler | Jobs planifiés (worker) |
| Pydantic v2 | Validation schémas API |
| NumPy | Monte Carlo (bracket WC2026) |
| httpx | Requêtes HTTP async (Bzzoiro, scraping) |
| Playwright | Scraping Betclic (gRPC) |

## Scraping

| Source | Méthode |
|--------|---------|
| Bzzoiro | API REST (clé BZZOIRO_API_KEY) |
| OddsPortal | Scraping HTML + httpx |
| Betclic | Scraping gRPC via Playwright |
| Unibet | Scraping LVS (API interne) |
| Sofascore | Fetch local + import script (WC2026 events uniquement) |

## Base de données

| Service | Usage |
|---------|-------|
| PostgreSQL 15 | Datastore principal |
| Redis 7 | Cache + état des jobs APScheduler |

## Frontend

| Lib | Usage |
|-----|-------|
| Next.js 14 (App Router) | Framework React |
| TypeScript | Typage |
| Tailwind CSS | Styling |
| clsx | Classes conditionnelles |
| lucide-react | Icônes |
| next-auth | Authentification (credentials) |
| axios | Requêtes API backend |

## Infrastructure

| Outil | Usage |
|-------|-------|
| Docker + Docker Compose | Conteneurisation |
| Dokploy | Gestion déploiement VPS |
| Traefik | Reverse proxy + HTTPS |
| VPS OVH | Hébergement (213.130.144.204) |
| GitHub | Source de vérité du code |

## Structure du projet

```
Ev0/
├── backend/
│   ├── app/
│   │   ├── api/          # Endpoints FastAPI
│   │   ├── ingestion/    # Clients Bzzoiro, OddsPortal, Betclic, Unibet
│   │   ├── models/       # Modèles SQLAlchemy
│   │   ├── pricing/      # Moteurs de pricing (goalscorer, assist, supersub, wc2026_*)
│   │   ├── services/     # MarketXgService, recommendation engine
│   │   └── worker.py     # APScheduler jobs
│   ├── alembic/          # Migrations DB
│   ├── scripts/          # Scripts utilitaires ponctuels
│   └── tests/
├── frontend/
│   └── src/
│       ├── app/dashboard/   # Pages Next.js
│       ├── components/      # Composants React
│       └── lib/             # API client, helpers
├── docs/                    # Documentation
└── docker-compose.yml
```
