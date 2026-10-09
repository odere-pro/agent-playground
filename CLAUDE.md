# Agent platform monorepo

The service chassis for the SLM agent platform and the nine PoC iterations that build it. Design: `docs/planning/` (the epic is read-only, ADR-001 decides the delivery model, `poc/000-plan.md` is the PoC track). Before you edit inside a folder, read that folder's `CLAUDE.md`.

## Map

- `packages/chassis` the chassis: `core` (envelope, events, collector, `handle`), `ports` (Protocols), `fakes`, `adapters`, `profiles.py`, `schemas/`.
- `packages/contract-suites` one pytest suite per port; fakes and real adapters bind the same class; `containers/` starts the real services for `make test-integration`.
- `packages/fake-model-server` scripted OpenAI-compatible server for offline tests.
- `packages/fake-mcp-server` test MCP server: in process for offline tests, behind the MCP gateway on kind (PoC-5).
- `packages/code-runner` the PoC-5 code-execution tool: an MCP server with one tool, `run_python`, in a gVisor sandbox pod.
- `packages/workloads` what runs behind the chassis, one folder per workload.
- `packages/workload-a2a` the template A2A server a Python workload ships with; never imports `chassis` (ADR-002).
- `pocs/poc-NN-<slug>` one iteration: README (checklist), CLAUDE.md, `tests/`, `demo/`, `notes/`. `pocs/CURRENT` names the one in progress.
- `deploy/` compose (PoC-4 scale stack: `compose/scale.sh`), kind (PoC-4: `kind/run.sh`; PoC-5: `kind/poc05/`, `make kind-poc05`), helm (PoC-9, empty). `docs/` planning, contracts, guides (chassis modules: `guides/chassis-reference.md`, PoC-4: `guides/poc-04-how-it-works.md`, PoC-5: `guides/poc-05-how-it-works.md`), templates, `plans/`.

## Commands

`make setup` · `make quick` (before every commit) · `make check` (what CI runs) · `make test` · `make test-poc POC=01` · `make test-integration` (Docker) · `make load-test` · `make kind-poc04` · `make kind-poc05` · `make ts-check` (the TypeScript workload) · `make record-cassettes` · `make schemas` · `make planning-sync` · `make planning-check` · `make harness-lint` · `make lint-extra` (shellcheck, actionlint, hadolint, codespell, yamllint, detect-secrets; its own CI job, not in `check`) · `make fake-model-server`. Run `make help` for the rest.

## Hard rules

- Tests run offline with no keys. `make test` disables sockets and strips `*_API_KEY` variables. A test that needs a socket is marked `network` and is not part of the gate.
- Every external dependency sits behind a port with an in-memory fake. The fake and every real adapter pass the same contract suite. Adapters are picked by `spec.adapters`, never in code.
- No agent framework anywhere in `chassis`; no product SDK or web framework in `chassis.core` or `chassis.ports`. `make lint` enforces both.
- Only the chassis holds credentials (ADR-001 hard requirement 1). No secret in any file, prompt, or log.
- One wire contract: `handle` is served over A2A in every lane. The event schema is versioned; the previous major stays accepted.
- `docs/planning/slm-agent-platform-epic-v3.md` is never edited. Backlog order, sizes, and dependencies live in `docs/planning/tools/backlog.py`; run `make planning-sync planning-check` after any planning change.
- Values the epic does not give are marked `suggested:`.
- Evidence over narrative: paste the command and its output. Never claim a test passed without the run.
- Commit only when asked. Never push to `main`, never force-push.

## Delegation

| Need | Agent |
| ---- | ----- |
| Write code for one scoped change | `developer` |
| Tests: unit, contract bindings, PoC scenarios | `tester` |
| Read-only review before merge | `reviewer` |
| A contract, port, schema, or lane question | `chassis-architect` |
| Anything touching keys, egress, sandboxing, images | `platform-security` |
| Evaluator gate, judge, golden set, eval CI | `eval-expert` |
| Spans, correlation, Langfuse, OTel | `observability-expert` |
| README, CLAUDE.md, PoC checklists, planning docs | `docs-editor` |

Skills: `poc-iteration`, `record-measurement`, `demo-record`, `contract-suite`, `adr`, `git-flow` (also parallel worktrees), `planning-sync`, `poc-05-operate` (the PoC-5 kind cluster).

## Style

Plain words, short sentences, US spelling, one idea per sentence. Docs match `docs/planning`. Code: ruff, mypy strict, line length 100, Python 3.12.
