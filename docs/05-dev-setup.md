# Dev setup

## Prérequis

- Docker + Docker Compose
- Python 3.13 (backend local)
- Node.js 20+ (frontend local)
- Git

---

## Variables d'environnement

Copier `.env.example` en `.env` à la racine. Variables obligatoires :

```
DATABASE_URL=postgresql+asyncpg://ev0:<password>@localhost:5432/ev0
REDIS_URL=redis://:<password>@localhost:6379/0
BZZOIRO_API_KEY=...
ODDS_API_KEY=...
NEXTAUTH_SECRET=...
NEXTAUTH_URL=http://localhost:3000
ADMIN_USERNAME=...
ADMIN_PASSWORD=...
POSTGRES_USER=ev0
POSTGRES_PASSWORD=...
POSTGRES_DB=ev0
```

---

## Démarrage local

```bash
# Backend (DB + Redis)
docker compose up -d db redis

# Migrations
cd backend && alembic upgrade head

# Backend dev
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload

# Worker
python -m app.worker

# Frontend
cd frontend && npm install && npm run dev
```

---

## Déploiement VPS

Géré via **Dokploy** sur le VPS 213.130.144.204.
Compose project : `ev0-compose-z5hvqt`
Répertoire : `/etc/dokploy/compose/ev0-compose-z5hvqt/code/`

### Commandes de redéploiement

```bash
# Rebuild backend + worker (sans toucher à la DB)
docker compose -p ev0-compose-z5hvqt --env-file .env up -d --build --no-deps --remove-orphans backend worker

# Rebuild frontend
docker compose -p ev0-compose-z5hvqt --env-file .env up -d --build --no-deps --remove-orphans frontend

# Restart simple (sans rebuild)
docker restart ev0-compose-z5hvqt-backend-1 ev0-compose-z5hvqt-worker-1
```

> **RÈGLE CRITIQUE** : Ne jamais utiliser `--force-recreate` en spécifiant un service nommé — cela recrée aussi les `depends_on` (y compris la DB → perte de données).
> Toujours utiliser `--remove-orphans` pour éviter l'accumulation de containers orphelins.

### Mise à jour des variables d'env

Dokploy écrase le `.env` à chaque déploiement depuis sa DB interne. Toujours mettre à jour les deux :
1. Le fichier `.env` sur le VPS
2. La colonne `compose.env` dans la DB Dokploy (`composeId = 'bpQY8Yr986JiwJRR_b0sk'`)

---

## Migrations Alembic

```bash
# Créer une nouvelle migration
alembic revision --autogenerate -m "description"

# Appliquer
alembic upgrade head

# Vérifier l'état
alembic current
alembic history
```

La chaîne de révisions est linéaire. En cas de multiple heads, fusionner avant de déployer :
```bash
alembic merge heads -m "merge heads"
```

---

## Tests

```bash
cd backend
pytest tests/ -v
```

Les tests ne nécessitent pas de vraie base de données — les fixtures pytest mockent les sessions SQLAlchemy là où nécessaire.
