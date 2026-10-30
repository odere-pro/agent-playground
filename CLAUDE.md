# Agent platform monorepo

The service chassis for the SLM agent platform, and the nine PoC iterations that build it.

- Design lives in `docs/planning/`. ADR-001 decides the delivery model. `docs/planning/poc/000-plan.md` is the PoC track.
- Before you edit inside a folder, read that folder's `CLAUDE.md`.

## Map

- `packages/chassis` the chassis: `core` (envelope, events, collector, `handle`), `ports` (Protocols), `fakes`, `adapters`, `profiles.py`, `schemas/`.
- `packages/contract-suites` one pytest suite per port; fakes and real adapters bind the same class; `containers/` starts the real services for `make test-integration`.
- `packages/fake-model-server` scripted OpenAI-compatible server for offline tests.
- `packages/fake-mcp-server` test MCP server: in process for offline tests, behind the MCP gateway on kind (PoC-5).
- `packages/code-runner` the PoC-5 code-execution tool: an MCP server with one tool, `run_python`, in a gVisor sandbox pod.
- `packages/workloads` what runs behind the chassis, one folder per workload (PoC-6 adds `echo-openai-agents`, `echo-smolagents`, `echo-claude-agent`).
- `packages/bakeoff` the PoC-6 bake-off kit: `python -m bakeoff smoke|run`, one `/v1/run` per engine, lane, and task.
- `packages/workload-a2a` the template A2A server a Python workload ships with; never imports `chassis` (ADR-002).
- `packages/guardrail-server` the injection and secret check behind LiteLLM's guardrail API (PoC-7). Prompt Guard 2, or a pattern stand-in offline; plus a LiteLLM stub.
- `packages/fake-langfuse` a fake of the Langfuse scores and OTLP routes for offline tests (PoC-7).
- `evals/` the eval tool configs run on the golden set: `deepeval/` (the CI pick) and `promptfoo/` (PoC-7 trial).
- `pocs/poc-NN-<slug>` one iteration: README (checklist), CLAUDE.md, `tests/`, `demo/`, `notes/`. `pocs/CLAUDE.md` defines `pocs/CURRENT` and the open PoCs.
- `deploy/` how the stacks run. Read `deploy/CLAUDE.md` first.
  - PoC-4: the Compose scale stack (`deploy/compose/scale.sh`) and kind (`deploy/kind/run.sh`).
  - PoC-5: `deploy/kind/poc05/` (`make kind-poc05`).
  - PoC-6: `deploy/kind/poc06/` (`make kind-poc06`; kagent-adk built from pinned source) and `deploy/compose/poc06/` for the Mac run.
  - PoC-7: `deploy/compose/poc07/stack.sh` (Langfuse v4, OTel Collector, Jaeger) and `deploy/agent-scan/` (Snyk Agent Scan in a sandbox).
  - PoC-8: `deploy/dashboards/` (written by `python -m chassis.dashboards`). PoC-9: `deploy/helm/` (empty).
- `scripts/` the gate scripts, the git hook, and `scripts/poc06_mac.sh`, the one Mac command (`make poc06-mac`).
- `docs/` contracts, guides, templates, and `plans/`. Start at `docs/README.md`.
  - Chassis modules: `docs/guides/chassis-reference.md`.
  - How a PoC works: `docs/guides/poc-04-how-it-works.md`, `poc-05-how-it-works.md`, `poc-06-how-it-works.md`.

## Commands

- `make setup` once.
- `make quick` before every commit. It does not run mypy.
- `make check` before a PR. It is the CI `check` job, mypy included.
- `make test` and `make test-poc POC=NN` while you work.
- `make lint-fix` repairs formatting and typos that the hook or `make lint-extra` reports.
- `make help` lists every other target, with what it does.

Offline rules, markers, and the pre-commit hook: `docs/guides/testing.md`. Gates and CI jobs: `docs/guides/local-dev.md`.

## Hard rules

- Tests run offline with no keys. `make test` disables sockets and strips key variables (`docs/guides/testing.md`).
- A test that needs a socket is marked `network` and is not in the gate.
- Every external dependency sits behind a port with an in-memory fake.
- The fake and every real adapter pass the same contract suite. Adapters are picked by `spec.adapters`, never in code.
- No agent framework anywhere in `chassis`; no product SDK or web framework in `chassis.core` or `chassis.ports`. `make lint` enforces both.
- Only the chassis holds credentials (ADR-001 hard requirement 1). No secret in any file, prompt, or log.
- One wire contract: `handle` is served over A2A in every lane. The event schema is versioned; the previous major stays accepted.
- `docs/planning/slm-agent-platform-epic-v3.md` is never edited.
- Backlog order, sizes, and dependencies live in `docs/planning/tools/backlog.py`. Run `make planning-sync planning-check` after any planning change.
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
