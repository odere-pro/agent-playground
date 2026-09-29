# pocs/poc-07-cross-cutting-decisions

Context for this iteration. The root `CLAUDE.md` and `pocs/CLAUDE.md` still apply.

- **Read first:** `docs/planning/poc/007-PoC-7-cross-cutting-decisions.md` (question, scope, exit criteria), then `README.md` here (status and evidence).
- **Touches:** `packages/chassis` (`AuthPort`, `GuardrailPort`, `EvaluatorPort`, `FeedbackPort`, telemetry adapter), `deploy/compose` (OTel Collector, Langfuse), four ADRs. Code that outlives the iteration goes there, not here.
- **Done means:** every exit criterion in `README.md` has evidence, `make test-poc POC=07` is green, the demo is in `demo/`, and `notes/backlog-changes.md` exists.
- **Do not touch:** the scope of another iteration. If a criterion needs it, record it under notes and stop.
- **Ask:** `chassis-architect` before a contract change, `platform-security` before anything with keys or egress, `eval-expert` for scoring and the evaluator gate, `observability-expert` for spans and correlation.
