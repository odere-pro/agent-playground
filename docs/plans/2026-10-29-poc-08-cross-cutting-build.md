# PoC-8: what is left

Status: in progress
Date: 2026-10-29
Source: [008-PoC-8-build-cross-cutting.md](../planning/poc/008-PoC-8-build-cross-cutting.md). Tracking: [PoC-8 README](../../pocs/poc-08-cross-cutting-build/README.md).

The code, the scenario tests, the notes, the offline demo, and the two stack drills are done. PR 39 added the workload-side JSON logs, the Prometheus and Grafana drill, and the real-LiteLLM drill. `notes/backlog-changes.md` is applied to the issues (2026-10-30). This list is only what is still open.

## 1. The CI runs on `bc67179`

None had finished when this was written. Put each run id, its result, and the commit in the README next to the items it backs. Until then, those items stand on offline tests and local runs.

- `poc08-stack` run 41011346864: Prometheus and Grafana load the dashboards with data, the rate limit in Valkey, and the latency pair (Observability scope item, criterion 3).
- `poc08-litellm` run 41011346881: a `litellm` span under `chassis.model.call` in one trace, and Presidio in LiteLLM masking PII (Security and Observability scope items, criterion 3).
- `check` run 41011346866: the offline gate on the merged tree.
- `poc08-evals` run 41011346893: evals, regression, and red team per engine (criterion 1).

Then the user ticks the boxes. `poc08-evals` as a required check on `main` is a repository setting, the user's call.

## 2. The user's decisions

The open questions in [`notes/backlog-changes.md`](../../pocs/poc-08-cross-cutting-build/notes/backlog-changes.md), carried as "open, user decision" in the issues:

- Does `/metrics` stay open by default? (014 H-7)
- Does the output URL allow-list stay default deny? (022 H-6)
- Does `CloudEvent` accept unknown extensions, so a new one such as `aigenerated` does not break an older consumer? (023 C-5)
- Keep the `User feedback by label` panel, or leave feedback to Langfuse only? (014 H-7)
- Presidio in the Collector log pipeline for named entities, or accept the regex-only gap for workload logs? (014 H-7)
- Rate limits with caller auth off: local and test profiles only, or an anonymous bucket? (022 H-6)

## 3. Run on the Mac

These need a Hugging Face token or a hosted model key. CI has neither.

- The red-team set on Llama Prompt Guard 2 in place of the pattern stand-in. Today: 14 of 18 on the stand-in.
- Numbers on a hosted model, and a real judge's agreement with the golden set's labels.

## 4. Close

- The user ticks the remaining boxes from the CI results and sets the README status to done.
- Set this plan to done.
