.DEFAULT_GOAL := help
SHELL := /bin/bash

POC ?= 01
PY := uv run python
PYTEST := scripts/check_offline.sh

.PHONY: help setup fmt fmt-check lint type test test-poc quick check planning-sync planning-check schemas harness-lint fake-model-server clean

help: ## Show this help
	@awk 'BEGIN {FS = ":.*##"} /^[a-zA-Z_-]+:.*##/ {printf "  \033[36m%-18s\033[0m %s\n", $$1, $$2}' $(MAKEFILE_LIST)

setup: ## Install Python 3.12, the workspace, and the git pre-commit hook
	uv sync --all-packages
	install -m 755 scripts/git-hooks/pre-commit .git/hooks/pre-commit
	@echo "ready: run 'make test'"

fmt: ## Format code
	uv run ruff format .
	uv run ruff check --fix .

fmt-check: ## Check formatting without changing files
	uv run ruff format --check .

lint: ## Ruff rules and the import rules (import-linter)
	uv run ruff check .
	uv run lint-imports

type: ## mypy, strict
	uv run mypy

test: ## Every test, offline: sockets disabled, API keys stripped from the environment
	$(PYTEST)

test-poc: ## Scenario tests of one PoC: make test-poc POC=01
	$(PYTEST) pocs/poc-$(POC)-*/tests

quick: ## Iteration gate: format check, lint, and the tests of the packages you changed
	$(MAKE) fmt-check lint
	@changed=$$(git diff --name-only HEAD 2>/dev/null; git ls-files --others --exclude-standard) ; \
	dirs=$$(echo "$$changed" | grep -oE '^(packages|pocs)/[^/]+' | sort -u | while read -r d; do [ -d "$$d" ] && echo "$$d"; done) ; \
	if [ -n "$$dirs" ]; then echo "testing: $$dirs"; $(PYTEST) $$dirs; else $(PYTEST) -m "not slow"; fi

check: ## Boundary gate: everything CI runs
	$(MAKE) fmt-check lint type test planning-check harness-lint

planning-sync: ## Regenerate the generated parts of the backlog
	cd docs/planning && python3 tools/sync.py

planning-check: ## Verify the backlog and the planning docs
	cd docs/planning && python3 tools/check.py

schemas: ## Regenerate the JSON Schemas in packages/chassis/schemas from the models
	$(PY) -m chassis.schemas --write

harness-lint: ## Check the Claude layer, the PoC folders, and docs/plans are wired correctly
	$(PY) scripts/harness_lint.py

fake-model-server: ## Run the fake model server on port 8081 with the example script
	uv run fake-model-server --script packages/fake-model-server/scripts/example.yaml --port 8081

clean: ## Remove caches
	rm -rf .pytest_cache .mypy_cache .ruff_cache
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
