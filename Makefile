# TracePilot task runner. Run `make help` for the menu.
SHELL := /bin/bash
.DEFAULT_GOAL := help

COMPOSE := docker compose
OLLAMA_GEN ?= llama3.1:8b
OLLAMA_REASON ?= qwen2.5-coder:7b
OLLAMA_EMBED ?= nomic-embed-text

.PHONY: help
help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-20s\033[0m %s\n", $$1, $$2}'

# --- Local Python dev ---
.PHONY: install
install: ## Editable-install all Python packages into the current venv
	pip install --upgrade pip
	bash scripts/install_packages.sh

.PHONY: api
api: ## Run the FastAPI backend locally with reload
	uvicorn tracepilot_api.main:app --reload --host 0.0.0.0 --port 8000

.PHONY: web
web: ## Run the Next.js frontend locally
	cd apps/web && npm install && npm run dev

# --- Docker stack ---
.PHONY: up
up: ## Build and start the full local stack
	$(COMPOSE) up -d --build
	@echo "API:      http://localhost:8000/docs"
	@echo "Web:      http://localhost:3000"
	@echo "Langfuse: http://localhost:3001 (admin@tracepilot.local / tracepilot123)"
	@echo "Qdrant:   http://localhost:6333/dashboard"

.PHONY: down
down: ## Stop the stack
	$(COMPOSE) down

.PHONY: clean
clean: ## Stop the stack and remove all volumes (DESTRUCTIVE)
	$(COMPOSE) down -v

.PHONY: logs
logs: ## Tail logs for the api + web services
	$(COMPOSE) logs -f api web

.PHONY: pull-models
pull-models: ## Pull the default Ollama models into the running container
	$(COMPOSE) exec ollama ollama pull $(OLLAMA_GEN)
	$(COMPOSE) exec ollama ollama pull $(OLLAMA_REASON)
	$(COMPOSE) exec ollama ollama pull $(OLLAMA_EMBED)

# --- Data ---
.PHONY: seed
seed: ## Ingest the bundled demo repository
	python scripts/seed_demo.py

# --- Quality ---
.PHONY: test
test: ## Run backend tests
	pytest -q

.PHONY: lint
lint: ## Lint Python with ruff
	ruff check packages apps/api tests

.PHONY: fmt
fmt: ## Format Python with ruff
	ruff format packages apps/api tests scripts
