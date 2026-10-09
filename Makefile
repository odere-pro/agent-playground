.DEFAULT_GOAL := help
SHELL := /bin/bash

POC ?= 01
PY := uv run python
PYTEST := scripts/check_offline.sh

.PHONY: help setup fmt fmt-check lint lint-extra type test test-poc test-integration load-test kind-poc04 kind-poc05 record-cassettes quick check planning-sync planning-check schemas harness-lint fake-model-server ts-check clean

help: ## Show this help
	@awk 'BEGIN {FS = ":.*##"} /^[a-zA-Z0-9_-]+:.*##/ {printf "  \033[36m%-18s\033[0m %s\n", $$1, $$2}' $(MAKEFILE_LIST)

setup: ## Install Python 3.12, the workspace, and the git pre-commit hook
	uv sync --all-packages
	install -m 755 scripts/git-hooks/pre-commit "$$(git rev-parse --git-common-dir)/hooks/pre-commit"
	@echo "ready: run 'make test'"

fmt: ## Format code
	uv run ruff format .
	uv run ruff check --fix .

fmt-check: ## Check formatting without changing files
	uv run ruff format --check .

lint: ## Ruff rules and the import rules (import-linter)
	uv run ruff check .
	uv run lint-imports

# Not in `quick` or `check`: CI runs it as its own job, so the pre-commit hook stays fast.
# The tools are wheels in the root dev group; uv.lock pins each by hash.
lint-extra: ## Extra linters: shellcheck, actionlint, hadolint, codespell, yamllint, detect-secrets
	uv run shellcheck -x -S warning $$(git ls-files '*.sh') scripts/git-hooks/pre-commit
	uv run actionlint .github/workflows/*.yml
	uv run hadolint --config .hadolint.yaml $$(git ls-files '*Dockerfile')
	uv run codespell docs packages pocs deploy scripts .github
	uv run yamllint --strict -c .yamllint.yaml .github deploy pocs/*/tests/fixtures
	git ls-files -z | xargs -0 uv run detect-secrets-hook --baseline .secrets.baseline

type: ## mypy, strict
	uv run mypy

test: ## Every test, offline: sockets disabled, API keys stripped from the environment
	$(PYTEST)

test-poc: ## Scenario tests of one PoC: make test-poc POC=01
	$(PYTEST) pocs/poc-$(POC)-*/tests

test-integration: ## Network tests: real adapters in testcontainers, sockets on, keys stripped; needs Docker
	scripts/check_integration.sh $(ARGS)

POC04 := pocs/poc-04-stateless-scalable

load-test: ## PoC-4 load matrix (Locust via uv run --with, Compose): make load-test ARGS="--engine echo-python"
	$(PY) $(POC04)/load/run_matrix.py $(ARGS)

kind-poc04: ## PoC-4 kind cluster and drills: make kind-poc04 ARGS="up native-sidecar"
	deploy/kind/run.sh $(ARGS)

kind-poc05: ## PoC-5 kind cluster (gVisor, NetworkPolicy, admission): make kind-poc05 ARGS="up"
	deploy/kind/poc05/run.sh $(ARGS)

# The tests that own model cassettes. Add a file here when it records through `CassetteTransport`.
CASSETTE_TESTS ?= packages/chassis/tests/test_recorded_model.py \
	pocs/poc-03-one-interface-every-client/tests/test_interface_contract.py

record-cassettes: ## Re-record model cassettes offline, against the fake model server
	$(PYTEST) --record-mode=rewrite $(CASSETTE_TESTS)

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

ts-check: ## The TypeScript workload gate, as CI runs it: npm ci, typecheck, test, build
	cd packages/workloads/echo-typescript && npm ci && npm run typecheck && npm test && npm run build

fake-model-server: ## Run the fake model server on port 8081 with the example script
	uv run fake-model-server --script packages/fake-model-server/scripts/example.yaml --port 8081

clean: ## Remove caches
	rm -rf .pytest_cache .mypy_cache .ruff_cache .import_linter_cache .hypothesis
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
