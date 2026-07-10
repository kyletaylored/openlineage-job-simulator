.PHONY: help setup run run-plain stop clean

help:
	@grep -E '^[a-zA-Z_-]+:.*##' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*## "}; {printf "  %-12s %s\n", $$1, $$2}'

setup: ## Create .venv, install deps, copy .env.example -> .env
	test -d .venv || uv venv
	uv pip install -r requirements.txt
	test -f .env || cp .env.example .env

run: ## Run the app with ddtrace + trace-log correlation
	uv run --env-file .env -- ddtrace-run python app.py

run-plain: ## Run the app without ddtrace-run
	uv run --env-file .env -- python app.py

stop: ## Kill a leftover background instance
	./scripts/stop.sh

clean: ## Remove .venv
	rm -rf .venv

.DEFAULT_GOAL := help
