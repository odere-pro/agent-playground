# pocs/poc-04-stateless-scalable

Context for this iteration. The root `CLAUDE.md` and `pocs/CLAUDE.md` still apply.

- **Read first:** `docs/planning/poc/004-PoC-4-stateless-scalable.md` (question, scope, exit criteria), then `README.md` here (status and evidence).
- **Touches:** `packages/chassis` (config loader, idempotency, `StatePort`, `EventPort`, budgets, shutdown), `deploy/compose` (MinIO, Valkey, a broker), load tests in `notes/`. Code that outlives the iteration goes there, not here.
- **Done means:** every exit criterion in `README.md` has evidence, `make test-poc POC=04` is green, the demo is in `demo/`, and `notes/backlog-changes.md` exists.
- **Do not touch:** the scope of another iteration. If a criterion needs it, record it under notes and stop.
- **Ask:** `chassis-architect` before a contract change, `platform-security` before anything with keys or egress, `eval-expert` for scoring and the evaluator gate, `observability-expert` for spans and correlation.
