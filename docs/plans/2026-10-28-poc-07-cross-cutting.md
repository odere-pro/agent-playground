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

## 4. Close

- Move `pocs/CURRENT` to the next PoC.
- Apply `pocs/poc-07-cross-cutting-decisions/notes/backlog-changes.md` with the `planning-sync` skill.
- Set this plan to done.
