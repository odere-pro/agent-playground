Status: done
Date: 2026-09-29

# Plan: PoC monorepo scaffold with PoC-1 day 0 and a lean Claude harness

## Context

The planning workspace in `tmp/slm-agent-platform-epic/` (epic, backlog, PoC track, ADR-001) is done. The next step is a repo to build the nine PoC iterations in. Decisions from the user:

- The current directory becomes the git repo (its name stays `agent-orchestration`; nothing new is nested under it). Planning moves to `docs/planning/`.
- Shared code in a uv workspace under `packages/`. One self-contained folder per PoC under `pocs/`, with its own docs, Claude context, tests, demo, and notes. Folders that are empty at scaffold time get a `.gitkeep`.
- Build the skeleton plus PoC-1 day 0: the four ports the walking skeleton uses (`ModelPort`, `EnginePort`, `ConfigPort`, `TelemetryPort`) with fakes and contract suites, the fake model server, the `fake`/`local`/`cloud` profiles, and the import-lint rule. `make test` passes offline.
- Lean Claude layer: one `settings.json` at the root, a root `CLAUDE.md` plus one per package and per PoC, eight agents, five skills, one output style, two hooks.

Conventions to match (from `~/git/developer-agent-flow-poc`): uv with exact pins in `[dependency-groups] dev`, ruff and mypy strict, a Makefile as the single entry point (`quick` for iteration, `check` for the boundary), CI that runs the same `make` targets, agent frontmatter `name/description/tools/model/effort/maxTurns/skills`, evidence over narrative. Host: uv 0.11, Docker 29, git, gh; no kind, helm, or ruff (uv provides ruff and Python 3.12). The planning tools resolve their root from their own file, so the move needs no code change.

## Layout

Six top-level folders. Each one answers one question.

```
.                                  repo root (this directory)
├── README.md                      quick start and map
├── CLAUDE.md                      root Claude context: rules, commands, delegation
├── Makefile                       every check and run target
├── pyproject.toml  uv.lock  .python-version
│
├── packages/                      shared code the PoCs build and keep
│   ├── chassis/                   the service chassis: core, ports, fakes, adapters, schemas
│   ├── contract-suites/           one pytest suite per port; fakes and real adapters pass the same one
│   ├── fake-model-server/         scripted OpenAI-compatible server for offline tests
│   └── workloads/                 what runs behind the chassis, one folder per workload
│       ├── echo-python/           .gitkeep, first workload in the walking skeleton
│       └── echo-typescript/       .gitkeep, PoC-2
│
├── pocs/                          one folder per iteration, self-contained
│   ├── README.md                  how an iteration runs, how to start and close one
│   ├── CURRENT                    the iteration in progress
│   ├── poc-01-walking-skeleton/
│   │   ├── README.md              question, scope checklist, exit criteria, status
│   │   ├── CLAUDE.md              what to read, what "done" means here, what not to touch
│   │   ├── tests/                 scenario tests, one per exit criterion
│   │   ├── demo/                  the demo script and its recording (.gitkeep)
│   │   └── notes/                 measurements, decisions, drafts for ADRs (.gitkeep)
│   ├── poc-02-two-engines-one-contract/
│   ├── poc-03-one-interface-every-client/
│   ├── poc-04-stateless-scalable/
│   ├── poc-05-sandboxed/
│   ├── poc-06a-bake-off-sidecar-lane/
│   ├── poc-06b-bake-off-remote-lane/
│   ├── poc-07-cross-cutting-decisions/
│   ├── poc-08-cross-cutting-build/
│   └── poc-09-agent-mvp-template/   (each with the same five entries; tests/, demo/, notes/ hold a .gitkeep)
│
├── deploy/                        how it runs: compose/, kind/, helm/ (.gitkeep each; filled by PoC-1, PoC-5, PoC-9)
│
├── docs/
│   ├── README.md                  index
│   ├── planning/                  moved as is: epic, issues/, poc/, adr/, tools/, diagrams/
│   ├── plans/                     every approved Claude plan and review, dated, kept under version control
│   ├── contracts/                 contract-v0.md: envelope, events, handle, ports
│   ├── guides/                    local-dev.md, testing.md, adding-a-port.md
│   └── templates/                 poc-readme.md, poc-claude.md, adr.md
│
├── scripts/                       harness_lint.py, check_offline.sh, git-hooks/pre-commit
├── .claude/                       settings.json, agents/, skills/, output-styles/, hooks/
└── .github/                       workflows/ci.yml, PULL_REQUEST_TEMPLATE.md
```

Naming rules: folders are nouns in kebab-case; PoC folders are `poc-NN-<slug>` where the slug is the planning doc's title; packages are importable names (`chassis`, `chassis_contracts`, `fake_model_server`).

## Steps

### 1. Repo, move, git

- `git init` on `main`; `.gitignore` (`.venv`, `__pycache__`, `.pytest_cache`, `.mypy_cache`, `.ruff_cache`, `.DS_Store`, `.claude/settings.local.json`, `*.egg-info`, `dist/`). `.editorconfig` (LF, 2-space YAML/JSON, 4-space Python, final newline).
- `mv tmp/slm-agent-platform-epic docs/planning`; remove `tmp/`. Move the current root `CLAUDE.md` content to `docs/planning/CLAUDE.md` with paths adjusted ("Run from `docs/planning/`"). Verify `python3 docs/planning/tools/check.py` passes from the new location.
- One initial commit after everything below passes: `chore: scaffold PoC monorepo with PoC-1 day 0`. No remote, no push.

### 2. Workspace and tooling

- Root `pyproject.toml`: `[tool.uv.workspace] members = ["packages/chassis", "packages/contract-suites", "packages/fake-model-server"]`; `requires-python = ">=3.12,<3.13"`; `[dependency-groups] dev` pinned exactly after the first `uv lock` (pytest, pytest-asyncio, pytest-socket, httpx, ruff, mypy, import-linter, pyyaml, types-PyYAML); `[tool.pytest.ini_options]` `testpaths = ["packages", "pocs"]`, `asyncio_mode = "auto"`, `addopts = "-p chassis_contracts"`, markers `network` and `slow`; `[tool.ruff]` py312, line 100, `format.exclude = ["*.md"]`; `[tool.mypy] strict = true`; `[tool.importlinter]` contracts:
  - layers inside `chassis`: `core` imports nothing from `ports`, `fakes`, `adapters`; `ports` imports only `core`; `fakes` and `adapters` may import both.
  - forbidden anywhere under `chassis`: `pydantic_ai`, `langgraph`, `agents`, `smolagents`, `claude_agent_sdk`, `crewai`.
  - forbidden in `chassis.core` and `chassis.ports`: `fastapi`, `starlette`, `httpx`, `openai`, `anthropic`, `litellm`, `a2a`, `opentelemetry`.
- `.python-version` = `3.12`.
- `Makefile` targets: `help` (default), `setup` (`uv sync`, install the git hook), `fmt`, `fmt-check`, `lint` (ruff and `lint-imports`), `type`, `test` (offline through `scripts/check_offline.sh`), `test-poc POC=01` (`pytest pocs/poc-$(POC)-*/tests`), `quick` (fmt-check, lint, tests of changed packages), `check` (fmt-check, lint, type, test, `planning-check`, `harness-lint`), `planning-sync`, `planning-check`, `schemas` (regenerate JSON Schemas), `harness-lint`, `fake-model-server`, `clean`.
- `scripts/git-hooks/pre-commit` runs `make quick`; `make setup` installs it. `scripts/check_offline.sh` runs pytest with `--disable-socket` and with every `*_API_KEY` and `*_TOKEN` variable unset.
- `.github/workflows/ci.yml`: ubuntu, pinned action SHAs, `uv sync --frozen`, `make check`. `.github/PULL_REQUEST_TEMPLATE.md`: what, which PoC and exit criteria it moves, evidence, planning updates.

### 3. `packages/chassis`, day 0

`src/chassis/`:

- `core/events.py`: `Start`, `Delta`, `ToolCall`, `Metrics`, `End`, `Error` with a `type` discriminator and `schema_version: "0"`; `Event` union; `parse_event`.
- `core/envelope.py`: `TaskInput`, `Budget`, `Versions`, `Context`, `Request`, `Response` per G.1, `status: ok | retry | fallback | error`, `versions` always set.
- `core/collector.py`: `collect(events) -> Response`. `core/handle.py`: the `Handle` type and an `echo` handle.
- `ports/`: `ModelPort`, `EngineConnector` (`kind`, `capabilities`, `setup`, `run`, `close`), `ConfigPort`, `TelemetryPort`. Protocols, no network code.
- `fakes/`: `ScriptedModel`, `FakeEngine` (scripted stream, or wraps any `Handle` by a direct call: a test double, not a lane), `InMemoryConfig`, `InMemoryTelemetry`.
- `profiles.py`: `spec.adapters`, profiles `fake`, `local`, `cloud`, `build_ports`. Day 0 resolves only `fake`; the others raise `AdapterNotAvailable` naming the PoC that adds them. `inprocess` refused outside `fake` and `local`.
- `schemas/`: `events.v0.json`, `task_input.v0.json`, `context.v0.json`, `envelope.v0.json`, generated by `make schemas`; a test fails on drift; a hand-written non-Python fixture (`tests/fixtures/events_from_typescript.jsonl`) must validate.
- `tests/`: unit tests plus `test_contracts.py` binding each fake to its suite. `CLAUDE.md`.

### 4. `packages/contract-suites` and `packages/fake-model-server`

- `chassis_contracts` pytest plugin: `model.py`, `engine.py`, `config.py`, `telemetry.py`. Each defines a contract class with test methods over a fixture; an adapter binds it by subclassing and providing the fixture. Cases: streaming and complete agree; usage reported; scripted error becomes `Error`; tool call round trip; engine streams `start` first and `end` last and honours cancel; config reload gives a new version; telemetry records one span per call with the request id. Self-tests run the suites against the fakes. `CLAUDE.md`.
- `fake_model_server`: FastAPI `POST /v1/chat/completions` (SSE and complete, `tool_calls`, `usage`) and `GET /health`, driven by a YAML script; `uv run fake-model-server --script scripts/example.yaml --port 8081`; tests over the httpx ASGI transport. `CLAUDE.md` documents the script format.

### 5. PoC folders

- `pocs/README.md`: how an iteration runs: read the planning doc, fill the README from the template, write one scenario test per exit criterion, keep `notes/` for measurements, record the demo in `demo/`, close with evidence per criterion and update `CURRENT`.
- `pocs/CURRENT` = `poc-01-walking-skeleton`.
- Every PoC folder: `README.md` (question, scope checklist, exit criteria as a checklist, how to run, status), `CLAUDE.md` (from `docs/templates/poc-claude.md`: the planning doc to read, the packages this iteration touches, what "done" means, what not to touch), `tests/`, `demo/`, `notes/`. Empty folders hold `.gitkeep`.
- `poc-01-walking-skeleton/tests/test_day0.py`: profile `fake` builds all four ports; every fake passes its suite; `inprocess` refused outside `fake`/`local`; schemas validate the non-Python fixture; the fake model server streams and completes the same text and returns a tool call; `lint-imports` passes (marked `slow`). Its README marks the day-0 items done and the walking-skeleton items open.

### 6. Docs

- Root `README.md`: what this is, `make setup`, `make test`, `make help`, the map above, link to `docs/`.
- `docs/plans/`: plans and reviews under version control. `README.md` sets the rule: an approved plan is copied here as `YYYY-MM-DD-<slug>.md` with a `Status:` line (`approved`, `in progress`, `done`, `superseded`) before the work starts, and its status is updated when the work lands. Seeded with `2026-09-29-poc-track-review.md` (today's review of the PoC track and ADR-001: findings, the edits made, the open evaluator-gate concern) and `2026-09-29-monorepo-scaffold.md` (this plan, status `in progress`, set to `done` in the initial commit). The issues are already files under `docs/planning/issues/` and are versioned by the same commit; publishing them to GitHub Issues waits for a remote, and their frontmatter is already shaped for it.
- `docs/README.md`, `docs/guides/local-dev.md`, `docs/guides/testing.md` (the test layers table, what runs where, how offline is enforced), `docs/guides/adding-a-port.md`, `docs/contracts/contract-v0.md` (PoC-1's written contract: envelope, events, `handle`, four ports, `EngineConnector` draft), `docs/templates/{poc-readme,poc-claude,adr}.md`.
- Style: plain words, short sentences, US spelling, tables only where they help.

### 7. Claude layer

- Root `CLAUDE.md` (under 70 lines): what the repo is, the map, the commands, hard rules (tests offline with no keys; every dependency behind a port with a fake; the chassis imports no framework; secrets never in files; planning is edited through its tools; the epic is read-only), delegation table, "read the folder's `CLAUDE.md` before editing there".
- Folder `CLAUDE.md` files (under 40 lines each): `packages/chassis`, `packages/contract-suites`, `packages/fake-model-server`, `packages/workloads`, `deploy`, `pocs`, each `pocs/poc-*`, `docs/planning` (the old root content).
- `.claude/settings.json`: `outputStyle: "poc-engineer"`; allow `Read, Grep, Glob, Edit, Write`, `Bash(make *)`, `Bash(uv *)`, `Bash(pytest *)`, `Bash(ruff *)`, `Bash(mypy *)`, `Bash(lint-imports *)`, `Bash(git status *|diff *|log *|add *|commit *|switch *|checkout -b *|branch *)`, `Bash(docker compose *)`, `Bash(python3 docs/planning/tools/*)`, `Bash(ls *)`, `Bash(cat *)`; deny `git push --force*`, `git push * main*`, `git reset --hard*`, `rm -rf /*`, `curl *`, `wget *`, `pip install *`, `sudo *`, `Read(.env*)`, `Read(**/secrets/**)`, `Edit(.claude/settings.json)`, `Write(.claude/settings.json)`, `Edit(docs/planning/slm-agent-platform-epic-v3.md)`. Two hooks: `SessionStart` → `hooks/session_start.sh` (prints `pocs/CURRENT`, `git status --short | head`, "run `make help`"); `PostToolUse` on `Edit|Write` → `hooks/format_py.sh` (ruff format on the edited `.py` file only). Shell plus `python3 -c` for JSON, so they run outside the venv.
- `.claude/agents/` (frontmatter `name, description, tools, model: inherit, effort, maxTurns, skills`): `developer` (one scoped change, failing test first, `make quick`, evidence), `tester` (unit, contract, and PoC scenario tests against exit criteria; `make test`; gap report), `reviewer` (read-only; PoC exit criteria, ADR-001 hard requirements, import rules, doc style; `accept | retry | escalate`), `chassis-architect` (ports, event schema, A2A mapping, ADR-001; may write ADRs and contracts), `platform-security` (hard requirement 1, credentials, network policy, gVisor, trust rule, pins by hash), `eval-expert` (evaluator gate, judge versus encoder, promptfoo/DeepEval, golden set, the open cost concern), `observability-expert` (OpenTelemetry, OpenInference, Langfuse, `traceparent`, request correlation), `docs-editor` (house style across README, `CLAUDE.md`, PoC READMEs, planning; runs `planning-sync` and `planning-check`).
- `.claude/skills/`: `poc-iteration`, `contract-suite`, `adr`, `git-flow` (branch `poc-NN/<topic>`, conventional commits, `make quick` before commit, `make check` before PR, never push to `main`; an approved plan is saved to `docs/plans/` in the first commit of the work), `planning-sync`.
- `.claude/output-styles/poc-engineer.md`: outcome first; evidence blocks with command and output; plain words; bullets for parallel items; no headers under 500 words; never claim a test passed without the run.
- `scripts/harness_lint.py`: agents have the required frontmatter and their skills exist; hooks named in `settings.json` exist and are executable; every skill has `SKILL.md`; every package and every PoC folder has `CLAUDE.md`, `README.md`, and `tests/`; `pocs/CURRENT` names an existing folder. Runs in `make check` and CI.

### 8. Gaps closed in the harness

- Offline is enforced, not assumed (pytest-socket plus key-stripping wrapper).
- Schema drift is a test; a non-Python fixture must validate.
- Import rules gate from day 0 (`import-linter`).
- The harness lints itself (`harness_lint.py`).
- One entry point for every check: `make`, git hook, CI.
- Planning stays checked inside `make check`.
- Plans and reviews are versioned next to the code in `docs/plans/`, so decisions survive the session that made them. `harness_lint.py` checks that every file there has a `Status:` line.
- PoC status is visible: `pocs/CURRENT`, each README's checklist, printed at session start.

## Verification

1. `make setup` installs Python 3.12 and the workspace; `uv.lock` is committed.
2. `make check` passes: fmt-check, lint (ruff, `lint-imports`), type, test (socket disabled, no keys), `planning-check`, `harness-lint`.
3. `make test-poc POC=01` passes and lists the day-0 scenarios.
4. `python3 docs/planning/tools/sync.py` reports "no changes" from the new location.
5. `git log --oneline` shows one commit on `main`; `git status` is clean; `tmp/` is gone.
6. `ls docs/plans` shows the README, the review, and this plan, each with a `Status:` line.
7. `find pocs -name .gitkeep | wc -l` equals the number of empty `tests/`, `demo/`, `notes/` folders (27 for nine PoCs minus PoC-1's `tests/`); `scripts/harness_lint.py` exits 0.
