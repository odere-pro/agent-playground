---
title: "PoC-7: Confirm the cross-cutting stack: security, observability, feedback, and evals"
labels: ["poc", "priority:P0", "area:security", "area:observability", "area:evals", "type:decision"]
milestone: "Agent MVP"
index: 7
iteration: PoC-7
timebox: "2–3 weeks"
depends_on: ["PoC-6"]
backlog_refs: ["022 H-6", "014 H-7", "017 H-4", "037 M-2", "023 C-5"]
---

## Question

Does the chosen stack give security, observability, feedback, and evals to every engine, in every lane, added once at the chassis and LiteLLM, with no engine-specific code?

## Status

In progress. The code, the tests, and the CI runs are done. Seven of eight exit criteria have evidence. ADR-007 to ADR-010 are written and Proposed.

## What is left

- **Accept the four ADRs (exit criterion 3).** The user reads and accepts [ADR-007](../adr/007-observability-otel-collector-langfuse.md) (observability), [ADR-008](../adr/008-security-identity-guardrails-defense-in-depth.md) (security), [ADR-009](../adr/009-feedback-langfuse-scores-event.md) (feedback), and [ADR-010](../adr/010-evals-offline-tool-online-gate.md) (evals). Each ADR ends with its open questions. The most urgent one: should feedback be tied to the caller who made the call?
- **The Mac run.** It needs Hugging Face and a hosted model key, which only the user's Mac has. Run the red-team set with Llama Prompt Guard 2, not the pattern stand-in (today it blocks 12 of 18 cases). Run LiteLLM's real `presidio` hook. Measure the encoder and the hosted judge: agreement, cost, and latency per gated call. Put the numbers in the PoC notes and in ADR-008 and ADR-010.
- **A real Snyk Agent Scan.** Add a `SNYK_TOKEN` repo secret, so the `poc07-agent-scan` job runs a real scan, not only the inspect step.
- **Look at the Langfuse UI.** Check how it shows `user.id` and `session.id` on a trace.
- **Close.** Move `pocs/CURRENT` to PoC-8. Apply the PoC's `notes/backlog-changes.md` with the `planning-sync` skill.

## Moved to PoC-8

- A LiteLLM span in the same trace. LiteLLM is not in the PoC-7 stack.
- Named-entity redaction in the OpenTelemetry Collector log pipeline. Today it masks only emails and phone numbers by regex (ADR-007 item 7).

## Where the rest is

- The evidence for every exit criterion is in [the PoC README](../../../pocs/poc-07-cross-cutting-decisions/README.md).
- The full plan as it was (scope, reuse, demo, exit criteria) is in git: `git show d11769f:docs/planning/poc/007-PoC-7-cross-cutting-decisions.md`.
