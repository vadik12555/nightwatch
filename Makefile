# NightWatch developer commands. `make help` lists them.
.DEFAULT_GOAL := help
COMPOSE := docker compose
MANAGE := $(COMPOSE) exec web python manage.py
PYTHON ?= python

.PHONY: help env up down restart logs ps migrate seed seed-reset runbooks weekly-report superuser shell \
        test lint fmt check dev agent-dry-run clean

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-14s\033[0m %s\n", $$1, $$2}'

env: ## Create .env from .env.example (if missing)
	@test -f .env || (cp .env.example .env && echo "Created .env — review the secrets in it")

# --- Docker ---------------------------------------------------------------
up: env ## Build and start the whole stack (http://localhost)
	$(COMPOSE) up -d --build --wait
	@echo "NightWatch is up: http://localhost (see .env for the admin login)"

down: ## Stop the stack (data volumes are kept)
	$(COMPOSE) down

restart: ## Restart application containers
	$(COMPOSE) restart web celery

logs: ## Follow logs of all services
	$(COMPOSE) logs -f --tail=100

ps: ## Show container status
	$(COMPOSE) ps

migrate: ## Apply database migrations
	$(MANAGE) migrate

seed: ## Create demo servers with a week of data
	$(MANAGE) seed_demo

seed-reset: ## Recreate demo servers
	$(MANAGE) seed_demo --reset

runbooks: ## Load the example runbooks and index them for the AI chat
	$(MANAGE) load_runbooks

weekly-report: ## Build the weekly AI health report now and send it to Telegram
	$(COMPOSE) exec celery celery -A config call ai.tasks.weekly_health_report

superuser: ## Create an admin user interactively
	$(MANAGE) createsuperuser

shell: ## Django shell inside the web container
	$(MANAGE) shell

# --- Local development (virtualenv) ------------------------------------------
dev: ## Run the dev server locally (SQLite unless DATABASE_URL is set)
	cd backend && DJANGO_DEBUG=1 $(PYTHON) manage.py migrate && DJANGO_DEBUG=1 $(PYTHON) manage.py runserver

test: ## Run the test suite (backend + agent)
	$(PYTHON) -m pytest

lint: ## Lint and check formatting
	$(PYTHON) -m ruff check .
	$(PYTHON) -m ruff format --check .

fmt: ## Auto-format and fix lint issues
	$(PYTHON) -m ruff format .
	$(PYTHON) -m ruff check --fix .

check: lint test ## Everything CI runs

agent-dry-run: ## Print one agent report for this machine
	$(PYTHON) agent/nightwatch_agent.py --config agent/config.example.yaml --dry-run

clean: ## Stop the stack and DELETE its volumes (database!)
	$(COMPOSE) down -v
