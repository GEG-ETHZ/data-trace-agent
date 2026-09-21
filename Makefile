.PHONY: install dev lint format typecheck test test-unit test-integration eval \
        deploy-dev deploy-prod rollback health-check logs traces setup-gcp \
        upload-secret pre-commit clean help

-include .env
export

help:
	@echo ""
	@echo "Data Trace Agent — available targets"
	@echo ""
	@echo "  install      Install all dependencies with uv"
	@echo "  dev          Run agent locally via adk web UI (http://localhost:8000)"
	@echo "  lint         Run ruff lint check"
	@echo "  format       Run ruff formatter"
	@echo "  typecheck    Run pyright type checker"
	@echo "  test             Run all tests with coverage"
	@echo "  test-unit        Run unit tests only"
	@echo "  test-integration Run integration tests only"
	@echo "  eval             Run promptfoo red-team evaluation"
	@echo "  deploy-dev       Deploy to Agent Engine (dev)"
	@echo "  deploy-prod      Deploy to Agent Engine (prod)"
	@echo "  rollback         Redeploy a previous git ref: make rollback REF=<tag> [ENV=prod|dev]"
	@echo "  health-check     Smoke-test the deployed resource without redeploying"
	@echo "  logs             Stream Cloud Logging output for this agent"
	@echo "  traces           Open Cloud Trace in browser"
	@echo "  setup-gcp        One-time GCP project bootstrap"
	@echo "  upload-secret    Upload a secret value to Secret Manager: make upload-secret NAME=<name> FILE=<path>"
	@echo "  pre-commit       Run all pre-commit hooks on all files"
	@echo "  clean            Remove build artefacts and caches"
	@echo ""

install:
	uv sync

dev:
	uv run adk web

lint:
	uv run ruff check .

format:
	uv run ruff format .

typecheck:
	uv run pyright

test:
	uv run pytest

test-unit:
	uv run pytest tests/unit -v

test-integration:
	uv run pytest tests/integration -v --tb=short

eval:
	uv run python tests/evals/run_eval.py

deploy-dev:
	uv run python deployment/deploy.py --env dev

deploy-prod:
	uv run python deployment/deploy.py --env prod

# Agent Engine deploys are source-based (the agent is pickled), so there is no
# image digest to roll back to. Rolling back means checking out a previous ref
# and redeploying it against the SAME resource, which deploy.py updates in place.
rollback:
	@if [ -z "$(REF)" ]; then \
		echo "Usage: make rollback REF=<tag> [ENV=prod|dev]"; \
		exit 1; \
	fi
	@echo "Rolling back to $(REF) (env: $(or $(ENV),prod))..."
	git fetch --tags --quiet
	@branch=$$(git rev-parse --abbrev-ref HEAD); \
	git checkout $(REF) && \
	uv run python deployment/deploy.py --env $(or $(ENV),prod); \
	status=$$?; \
	git checkout "$$branch"; \
	exit $$status

health-check:
	uv run python deployment/scripts/health_check.py

logs:
	bash deployment/scripts/read_logs.sh

traces:
	bash deployment/scripts/read_traces.sh

setup-gcp:
	bash deployment/scripts/setup_gcp.sh

# Secret Manager names must be alphanumeric + underscores — Agent Engine rejects
# hyphens despite its error message claiming otherwise.
upload-secret:
	@if [ -z "$(NAME)" ] || [ -z "$(FILE)" ]; then \
		echo "Usage: make upload-secret NAME=<secret_name> FILE=<path-to-file-with-value>"; \
		exit 1; \
	fi
	bash deployment/scripts/upload_secret.sh "$(NAME)" "$(FILE)"

pre-commit:
	uv run pre-commit run --all-files

clean:
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -name "*.pyc" -delete 2>/dev/null || true
	rm -rf .pytest_cache .ruff_cache .coverage htmlcov .mypy_cache
