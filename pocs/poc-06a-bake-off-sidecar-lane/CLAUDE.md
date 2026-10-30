# pocs/poc-06a-bake-off-sidecar-lane

Context for this iteration. The root `CLAUDE.md` and `pocs/CLAUDE.md` still apply.

- **Read first:** `docs/planning/poc/006-PoC-6-framework-bake-off.md` (question and what is left), then `README.md` here (status and evidence). This README holds exit criteria 1, 2 (the TypeScript agent), 3, 4, 5, and 7 as a draft. Criteria 6, 8, and the final 7 are in the 6b README.
- **Touches:** `packages/workloads/*` (OpenAI Agents SDK, the full TypeScript agent), the scorecard in `notes/`, the bake-off ADR draft. Code that outlives the iteration goes there, not here.
- **Done means:** every exit criterion in `README.md` has evidence, `make test-poc POC=06a` is green, the demo is in `demo/`, and `notes/backlog-changes.md` exists.
- **Do not touch:** the scope of another iteration. If a criterion needs it, record it under notes and stop.
- **Ask:** `chassis-architect` before a contract change, `platform-security` before anything with keys or egress, `eval-expert` for scoring and the evaluator gate, `observability-expert` for spans and correlation.
