.PHONY: help setup run run-plain stop clean azureml-setup azureml-rebuild-env azureml-submit

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

azureml-setup: ## Install Azure ML driver deps, copy azureml/.env.example -> azureml/.env
	uv pip install -r azureml/requirements-azureml.txt
	test -f azureml/.env || cp azureml/.env.example azureml/.env

azureml-rebuild-env: ## Rebuild+re-register the Azure ML environment (Docker image), bump azureml/.env
	uv run --env-file azureml/.env -- python -m azureml.rebuild_env

azureml-submit: ## Submit a simulated request as an Azure ML pipeline job (pass flags via ARGS="...")
	uv run --env-file azureml/.env -- python -m azureml.cli $(ARGS)

.DEFAULT_GOAL := help
