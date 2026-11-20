.DEFAULT_GOAL := help
COMPOSE ?= docker compose

.PHONY: help up down logs ps restart clean test test-backend test-frontend lint dev-backend dev-frontend fake-backend

help: ## Show available targets
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-15s\033[0m %s\n", $$1, $$2}'

.env:
	@cp .env.example .env
	@echo "Created .env from .env.example. Add real keys to LLM_API_KEYS, then run make up again to apply them."

up: .env ## Build and start backend (:8000) and frontend (:3000)
	$(COMPOSE) up -d --build
	@echo ""
	@echo "  frontend  http://localhost:3000"
	@echo "  backend   http://localhost:8000/health"

down: ## Stop the stack (keeps the data volume)
	$(COMPOSE) down

restart: ## Restart containers (history persists in the volume)
	$(COMPOSE) restart

logs: ## Follow logs from both services
	$(COMPOSE) logs -f

ps: ## Show container status
	$(COMPOSE) ps

clean: ## Stop the stack and delete the SQLite/cache volume
	$(COMPOSE) down -v

test: test-backend test-frontend ## Run all tests

test-backend: ## Backend tests (pytest)
	cd backend && python -m pytest -q

test-frontend: ## Frontend tests (vitest)
	cd frontend && npm test

lint: ## Lint backend (ruff) and typecheck frontend
	cd backend && ruff check . && ruff format --check .
	cd frontend && npx tsc -b

dev-backend: ## Run the backend locally without Docker (needs .env)
	cd backend && DATABASE_URL=sqlite:///./data/app.db CACHE_DIR=./data/llm_cache uvicorn app.asgi:app --env-file ../.env --reload --port 8000

fake-backend: ## Run the backend with a fake LLM provider (no keys needed)
	cd backend && DATABASE_URL=sqlite:///./data/fake.db python -m scripts.fake_provider_server

dev-frontend: ## Run the frontend dev server on :3000
	cd frontend && npm run dev
