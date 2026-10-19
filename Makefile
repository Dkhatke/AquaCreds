.DEFAULT_GOAL := help
COMPOSE := docker compose

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-14s\033[0m %s\n", $$1, $$2}'

sync: ## Install/refresh the local .venv (all packages + dev + ml groups)
	uv sync --all-packages --group ml

dev: ## Build and start the dev stack, wait until healthy
	$(COMPOSE) up --build -d --wait db valkey s3 api
	$(COMPOSE) up -d worker
	@echo "API: http://localhost:8000/docs   readiness: http://localhost:8000/readyz"

down: ## Stop the dev stack (keeps data volumes)
	$(COMPOSE) down

nuke: ## Stop the dev stack AND delete all data volumes
	$(COMPOSE) down -v

logs: ## Tail logs of api + worker
	$(COMPOSE) logs -f api worker

smoke: ## End-to-end check: readiness + API → queue → worker round trip
	curl -fsS http://localhost:8000/readyz && echo
	curl -fsS -X POST http://localhost:8000/api/v1/system/ping-worker && echo

migrate: ## Apply DB migrations inside the stack
	$(COMPOSE) run --rm migrate aqua-api migrate

revision: ## Create a migration: make revision m="add projects table"
	$(COMPOSE) run --rm migrate aqua-api revision -m "$(m)" --autogenerate

lint: ## Ruff lint + format check + mypy
	uv run ruff check .
	uv run ruff format --check .
	uv run mypy

fmt: ## Auto-fix lint and format
	uv run ruff check --fix .
	uv run ruff format .

test: ## Run the Python test suite
	uv run pytest

.PHONY: help sync dev down nuke logs smoke migrate revision lint fmt test
