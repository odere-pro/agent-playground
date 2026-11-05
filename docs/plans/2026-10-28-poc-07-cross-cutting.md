# PoC-7: what is left

Status: in progress
Date: 2026-10-29
Source: [007-PoC-7-cross-cutting-decisions.md](../planning/poc/007-PoC-7-cross-cutting-decisions.md). Tracking: [PoC-7 README](../../pocs/poc-07-cross-cutting-decisions/README.md).

The code, the tests, and the CI runs are done. Seven of eight exit criteria have evidence. This list is only what is still open.

## 1. Accept the four ADRs (exit criterion 3)

The owner reads and accepts:

- [ADR-007](../planning/adr/007-observability-otel-collector-langfuse.md) observability
- [ADR-008](../planning/adr/008-security-identity-guardrails-defense-in-depth.md) security
- [ADR-009](../planning/adr/009-feedback-langfuse-scores-event.md) feedback
- [ADR-010](../planning/adr/010-evals-offline-tool-online-gate.md) evals

Each ADR ends with its open questions. Answer them first. The most urgent one: today, any caller with the feedback scope who knows a `request_id` can score that call. Should feedback be tied to the caller who made the call?

When they are accepted: set each ADR to Accepted, tick criterion 3 in the README, and set the README status to done.

## 2. Run on the Mac

These need Hugging Face and a hosted model key. The cloud container has neither.

- Run the red-team set with Llama Prompt Guard 2, not the stand-in. Today it blocks 12 of 18 cases.
- Measure the encoder and the hosted judge: agreement with the human labels, cost, and latency.
- Put the numbers in `pocs/poc-07-cross-cutting-decisions/notes/` and in ADR-008 and ADR-010.

## 3. Turn on the real Snyk Agent Scan

Add a `SNYK_TOKEN` secret to the repo. Then the `poc07-agent-scan` job runs a real scan, not only the inspect step.

## 4. Pending checks (from review finding R7-09)

One concrete check for each "waits on" item in the README. The README checkboxes are not changed here.

- ADR-007 to ADR-010: each ADR's status line reads "Accepted, 2026-10-30, by the user". The README criterion 3 line and status still need to match them; that is the owner's edit.
- Observability, a LiteLLM span in the same trace: PoC-8 holds it, on Jaeger in the `inprocess` lane: `pocs/poc-08-cross-cutting-build/tests/test_poc08_stack_litellm.py::test_one_run_is_one_trace_with_a_litellm_span_under_the_model_proxy`, `poc08-litellm` [run 41011333394](https://github.com/odere-pro/agent-playground/actions/runs/41011333394) ([note](../../pocs/poc-08-cross-cutting-build/notes/2026-10-30-stack-drills.md)).
- Observability, how Langfuse shows `user.id` and `session.id`: a new stack test in `pocs/poc-07-cross-cutting-decisions/tests/test_poc07_stack.py` that reads both from the run's trace. Not written yet; `poc07-stack` run 40735435727 has none.
- Security and redaction, LiteLLM's real `presidio` hook: PoC-8 holds it: `pocs/poc-08-cross-cutting-build/tests/test_poc08_stack_litellm.py::test_pii_reaches_the_model_masked_by_litellms_presidio_hook`, `poc08-litellm` [run 41011333394](https://github.com/odere-pro/agent-playground/actions/runs/41011333394).
- Security, Llama Prompt Guard 2: the Mac run (section 2). A `SNYK_TOKEN` repo secret: section 3.
- Evals, Langfuse datasets and experiments on a real Langfuse: a new stack test in `pocs/poc-07-cross-cutting-decisions/tests/test_poc07_stack.py`. Not written yet; `poc07-stack` run 40735435727 has none.
- Redaction, named-entity redaction in the Collector log pipeline: regex only today (`tests/test_poc07_stack_static.py::test_collector_logs_pipeline_masks_emails_and_phones`; ADR-007 item 7). It waits on PoC-8 [open question 6](../../pocs/poc-08-cross-cutting-build/notes/backlog-changes.md), the user's decision.
- Criterion 4, the same join across two containers on a real Collector: a new stack test in `pocs/poc-07-cross-cutting-decisions/tests/test_poc07_stack.py`. Not written yet; `poc07-stack` run 40735435727 and the PoC-8 `poc08-litellm` drill both use the `inprocess` lane.

## 5. Close

- Move `pocs/CURRENT` to the next PoC.
- Apply `pocs/poc-07-cross-cutting-decisions/notes/backlog-changes.md` with the `planning-sync` skill.
- Set this plan to done.
