# PoC-8: what is left

Status: in progress
Date: 2026-10-29
Source: [008-PoC-8-build-cross-cutting.md](../planning/poc/008-PoC-8-build-cross-cutting.md). Tracking: [PoC-8 README](../../pocs/poc-08-cross-cutting-build/README.md).

The code, the scenario tests, the notes, and the offline demo are done. Every exit criterion has offline evidence. This list is only what is still open.

## 1. The first GitHub run of `poc08-evals.yml` (exit criteria 1, 2, 5)

Push the branch and let `poc08-evals` run: the `evals`, `regression`, and `redteam` jobs, one per engine. Then put the run id and the commit in the README next to criteria 1, 2, and 5. Until then, those boxes stand on local runs of the same commands.

## 2. The user's review

Answer the open questions in [`notes/backlog-changes.md`](../../pocs/poc-08-cross-cutting-build/notes/backlog-changes.md). The three that change the contract or the defaults:

- Does `/metrics` stay open by default?
- Does the output URL allow-list stay default deny?
- Does `CloudEvent` accept unknown extensions, so a new one such as `aigenerated` does not break an older consumer?

## 3. Run on the Mac

These need Hugging Face, Docker with more memory, or a hosted key. The cloud container has none of them.

- The red-team set on Llama Prompt Guard 2 and LiteLLM's real Presidio hook, not the stand-ins. Today: 14 of 18 on the stand-in.
- A real LiteLLM in the trace: its span next to `chassis.model.route`.
- A real Langfuse: feedback on the trace in the UI.
- p50 and p95 added by the rate limit on Valkey and by the guard stages.

## 4. Gaps to build or accept

- Prometheus and Grafana loading `deploy/dashboards/` with data from a load run, including the `User feedback by label` panel.
- JSON logs with `trace_id` from the workload side (the template A2A server).
- Named-entity masking in the Collector log pipeline.

## 5. Close

- Tick the remaining boxes with evidence and set the README status to done.
- Apply `notes/backlog-changes.md` with the `planning-sync` skill.
- Move `pocs/CURRENT` to `poc-09-agent-mvp-template`.
- Set this plan to done.
