# pocs/poc-06b-bake-off-remote-lane

Context for this iteration. The root `CLAUDE.md` and `pocs/CLAUDE.md` still apply.

- **Read first:** `docs/planning/poc/006-PoC-6-framework-bake-off.md` (question, scope, exit criteria), then `README.md` here (status and evidence).
- **Touches:** `packages/workloads/*` (Claude Agent SDK, smolagents), the `remote` cloud auth adapter, the completed bake-off ADR. Code that outlives the iteration goes there, not here.
- **Done means:** every exit criterion in `README.md` has evidence, `make test-poc POC=06b` is green, the demo is in `demo/`, and `notes/backlog-changes.md` exists.
- **Do not touch:** the scope of another iteration. If a criterion needs it, record it under notes and stop.
- **Ask:** `chassis-architect` before a contract change, `platform-security` before anything with keys or egress, `eval-expert` for scoring and the evaluator gate, `observability-expert` for spans and correlation.
