.DEFAULT_GOAL := help
SHELL := /bin/bash

POC ?= 01
PY := uv run python
PYTEST := scripts/check_offline.sh

# The CI gate leaves out the scenario tests of a finished PoC (decided 2026-11-08; docs/guides/
# testing.md, "What CI runs"). Finished: every PoC whose README says `Status: done`, plus the ones
# named in CI_FINISHED_POCS, whose work is finished while their Status line is still open.
CI_FINISHED_POCS := 06a 06b 06c 07
POC_DIRS := $(patsubst %/,%,$(wildcard pocs/poc-*/))
DONE_POCS := $(sort $(patsubst %/README.md,%,$(shell grep -l '^Status: done' pocs/poc-*/README.md)) \
	$(foreach n,$(CI_FINISHED_POCS),$(wildcard pocs/poc-$(n)-*)))
OPEN_POCS := $(filter-out $(DONE_POCS),$(POC_DIRS))
GATE_TESTS := packages scripts/tests $(wildcard $(addsuffix /tests,$(OPEN_POCS)))
DONE_POC_TESTS := $(wildcard $(addsuffix /tests,$(DONE_POCS)))

.PHONY: help setup evals evals-regression redteam fmt fmt-check lint lint-extra type \
	test test-gate test-poc test-pocs-done gate-paths test-integration load-test \
	kind-poc04 kind-poc05 kind-poc06 kind-chart bakeoff poc06-mac record-cassettes \
	quick check planning-sync planning-check planning-check-strict progress schemas harness-lint \
	fake-model-server ts-check clean

help: ## Show this help
	@awk 'BEGIN {FS = ":.*##"} /^[a-zA-Z0-9_-]+:.*##/ {printf "  \033[36m%-18s\033[0m %s\n", $$1, $$2}' $(MAKEFILE_LIST)

setup: ## Install Python 3.12, the workspace, and the git pre-commit hook
	uv sync --all-packages
	install -m 755 scripts/git-hooks/pre-commit "$$(git rev-parse --git-common-dir)/hooks/pre-commit"
	@echo "ready: run 'make test'"

fmt: ## Format code
	uv run ruff format .
	uv run ruff check --fix .

lint-fix: ## Fix what the linters can fix: ruff format, ruff fixes, codespell typos, secrets baseline lines
	$(MAKE) fmt
	uv run codespell -w docs packages pocs deploy scripts .github
	scripts/check_secrets.sh --refresh

lint-staged: ## The fast lint-extra checks on staged files (the pre-commit hook runs this)
	scripts/lint_staged.sh

fmt-check: ## Check formatting without changing files
	uv run ruff format --check .

lint: ## Ruff rules and the import rules (import-linter)
	uv run ruff check .
	uv run lint-imports

# Not in `quick` or `check`: CI runs it as its own job. The pre-commit hook runs the fast
# part on staged files only (`make lint-staged`).
# The tools are wheels in the root dev group; uv.lock pins each by hash.
lint-extra: ## Extra linters: shellcheck, actionlint, hadolint, codespell, yamllint, detect-secrets
	uv run shellcheck -x -S warning $$(git ls-files '*.sh') scripts/git-hooks/pre-commit
	uv run actionlint .github/workflows/*.yml
	uv run hadolint --config .hadolint.yaml $$(git ls-files '*Dockerfile')
	uv run codespell docs packages pocs deploy scripts .github
	uv run yamllint --strict -c .yamllint.yaml .github deploy pocs/*/tests/fixtures
	scripts/check_secrets.sh

type: ## mypy, strict
	uv run mypy

test: ## Every test, offline: sockets disabled, API keys stripped from the environment
	$(PYTEST)

test-gate: ## The CI gate's tests, offline: packages, scripts/tests, and the open PoCs only
	$(PYTEST) $(GATE_TESTS)

test-poc: ## Scenario tests of one PoC: make test-poc POC=01 (POC=06 runs 06a, 06b, 06c)
	$(PYTEST) pocs/poc-$(POC)*/tests

test-pocs-done: ## Scenario tests of every finished PoC, offline; CI does not run them
	$(PYTEST) $(DONE_POC_TESTS)

gate-paths: ## Print which test folders the CI gate runs and which run only locally
	@echo "gate: $(strip $(GATE_TESTS))"
	@echo "local: $(strip $(DONE_POC_TESTS))"

test-integration: ## Network tests: real adapters in testcontainers, sockets on, keys stripped; needs Docker
	scripts/check_integration.sh $(ARGS)

load-test: ## PoC-4 load matrix (Locust from its hashed lock, Compose): make load-test ARGS="--engine echo-python"
	$(PY) -m load_tools.run_matrix $(ARGS)

kind-poc04: ## PoC-4 kind cluster and drills: make kind-poc04 ARGS="up native-sidecar"
	deploy/kind/run.sh $(ARGS)

kind-poc05: ## PoC-5 kind cluster (gVisor, NetworkPolicy, admission): make kind-poc05 ARGS="up"
	deploy/kind/poc05/run.sh $(ARGS)

kind-poc06: ## PoC-6 engines on the PoC-5 kind cluster: make kind-poc06 ARGS="..."
	deploy/kind/poc06/run.sh $(ARGS)

kind-chart: ## The library chart on kind (ADR-012 drain drill, H14): make kind-chart ARGS="up"
	deploy/kind/chart/run.sh $(ARGS)

bakeoff: ## PoC-6 benchmark kit: make bakeoff ARGS="..."
	uv run python -m bakeoff $(ARGS)

poc06-mac: ## PoC-6 runs on a Mac: make poc06-mac ARGS="..."
	scripts/poc06_mac.sh $(ARGS)

# PoC-8: the offline eval gate and the red-team set, the same steps as .github/workflows/poc08-evals.yml.
# Localhost processes and the fake model server only; DeepEval installs from its hashed lock per run.
EVAL_OUT ?= build/evals
EVAL_ARGS ?=
REDTEAM_ARGS ?=

evals: ## PoC-8 eval gate: DeepEval on the 50 gate cases, every engine and lane, then the baseline check
	$(PY) -m eval_kit.golden.generate --check
	$(PY) -m eval_kit.evalrun --out $(EVAL_OUT)/normal $(EVAL_ARGS)
	$(PY) -m eval_kit.evalgate --report $(EVAL_OUT)/normal $(EVAL_ARGS)

evals-regression: ## PoC-8 demo: business logic that drops facts must fail the eval gate (exit 1)
	$(PY) -m eval_kit.evalrun --logic drop-facts --engine echo-python --lane sidecar --out $(EVAL_OUT)/drop-facts
	@$(PY) -m eval_kit.evalgate --report $(EVAL_OUT)/drop-facts --engine echo-python --lane sidecar; rc=$$?; \
	if [ $$rc -eq 1 ]; then echo "ok: the gate failed the drop-facts logic"; \
	else echo "expected the gate to fail with exit 1, got $$rc"; exit 1; fi

redteam: ## PoC-8 red-team set on every engine, lane, and guardrail config, against the recorded baseline
	$(PY) -m eval_kit.redteam --out $(EVAL_OUT)/redteam.json $(REDTEAM_ARGS)

# The tests that own model cassettes. Add a file here when it records through `CassetteTransport`.
CASSETTE_TESTS ?= packages/chassis/tests/test_recorded_model.py \
	pocs/poc-03-one-interface-every-client/tests/test_interface_contract.py

record-cassettes: ## Re-record model cassettes offline, against the fake model server
	$(PYTEST) --record-mode=rewrite $(CASSETTE_TESTS)

quick: ## Iteration gate: format check, lint, and the tests of the packages you changed; no mypy (make check runs it)
	$(MAKE) fmt-check lint
	@changed=$$(git diff --name-only HEAD 2>/dev/null; git ls-files --others --exclude-standard) ; \
	dirs=$$(echo "$$changed" | grep -oE '^(packages|pocs)/[^/]+' | sort -u | while read -r d; do [ -d "$$d" ] && [ -n "$$(find "$$d" -name 'test_*.py' -not -path '*/node_modules/*' -print -quit)" ] && echo "$$d"; done) ; \
	if [ -n "$$dirs" ]; then echo "testing: $$dirs"; $(PYTEST) $$dirs; else $(PYTEST) -m "not slow"; fi

check: ## Boundary gate: what the CI `check` job runs; finished PoCs: make test-pocs-done
	$(MAKE) fmt-check lint type test-gate planning-check harness-lint

planning-sync: ## Regenerate the generated parts of the backlog
	cd docs/planning && python3 tools/sync.py

planning-check: ## Verify the backlog and the planning docs, with the evidence rules (--strict)
	cd docs/planning && python3 tools/check.py --strict

planning-check-strict: planning-check ## Alias of planning-check, which is strict since 2026-11-07

progress: ## The product plan per phase: issues done, boxes ticked, open complexity, what it waits on, critical path (PHASE=A lists its issues)
	cd docs/planning && python3 tools/progress.py $(PHASE)

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
