.PHONY: dev dev-db dev-frontend dev-backend test lint format migrate help

# Colors
GREEN := \033[0;32m
NC := \033[0m

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "$(GREEN)%-15s$(NC) %s\n", $$1, $$2}'

# ===================
# DEVELOPMENT
# ===================

dev: dev-db dev-backend dev-frontend ## Start all services for development

dev-db: ## Start database and redis
	cd infra && docker compose -f docker-compose.dev.yml up -d db redis

dev-backend: ## Start backend with hot reload
	cd backend && pip install -e ".[dev]" && uvicorn app.main:app --reload --port 8000

dev-frontend: ## Start frontend with hot reload
	cd frontend && npm install && npm run dev

# ===================
# DOCKER
# ===================

docker-build: ## Build all Docker images
	cd infra && docker compose build

docker-up: ## Start all services with Docker
	cd infra && docker compose -f docker-compose.dev.yml up

docker-down: ## Stop all services
	cd infra && docker compose -f docker-compose.dev.yml down

docker-logs: ## Show logs
	cd infra && docker compose -f docker-compose.dev.yml logs -f

# ===================
# TESTING
# ===================

test: ## Run all tests
	cd backend && pytest tests/ -v

test-cov: ## Run tests with coverage
	cd backend && pytest tests/ -v --cov=app --cov-report=html

# ===================
# CODE QUALITY
# ===================

lint: ## Run linters
	cd backend && ruff check .
	cd frontend && npm run lint

format: ## Format code
	cd backend && ruff format .

typecheck: ## Run type checking
	cd backend && mypy app
	cd frontend && npm run type-check

# ===================
# DATABASE
# ===================

migrate: ## Run database migrations
	cd backend && alembic upgrade head

migrate-new: ## Create new migration
	cd backend && alembic revision --autogenerate -m "$(msg)"

# ===================
# PRODUCTION
# ===================

prod-up: ## Start production stack
	cd infra && docker compose up -d

prod-down: ## Stop production stack
	cd infra && docker compose down

prod-logs: ## Show production logs
	cd infra && docker compose logs -f
