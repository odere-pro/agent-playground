# Backlog changes when PoC-6a closes

Status: **Applied 2026-10-09** with skill `planning-sync`. Each change is a "Status after PoC-6" bullet at the end of `## What`. No acceptance box was ticked, and no order, size, or dependency changed, so `backlog.py` is not touched.

Evidence: the [scorecard](2026-10-09-scorecard.md), [ADR-006](../../../docs/planning/adr/006-agent-engines-default-supported-lanes.md) (Proposed), [contract v5](../../../docs/contracts/contract-v5.md).

## Applied

- **058 CH-8**: mappings for the OpenAI Agents SDK and the TypeScript agent, with mapping sizes in code lines. No event change needed. Proposed default: PydanticAI. LangGraph keeps a stand-in MCP client.
- **008 H-14**: the engines behind one `handle`, the proposed default, and the freeze.
- **054 H-16**: the second read-only tool `acronym_expand`; the gateway's `<server>-<tool>` names against the OpenAI Agents SDK.
- **028 S-3**: the bake-off task checks are string matches that stand in for `facts_kept`.
- **025 H-10**: the TypeScript agent's inbound token flag and task pruning (PoC-5 and PoC-2 gaps closed).

## Not applied: waits on the Mac run

- Hosted-model tokens, latency, and time to first token per engine (criterion 5). When they exist, update the scorecard and the default in ADR-006, then 058 CH-8 and 008 H-14.
- Done on 2026-10-28, no longer waiting: the load run per new sidecar engine ran in CI (`poc06-load.yml`, [notes/2026-10-28-sidecar-load-in-ci.md](2026-10-28-sidecar-load-in-ci.md)), and the kind hostile suite passed (`poc06-kind.yml` run 38010290978).

## Proposed

- **Rare 502 under load at 4 pairs, not diagnosed.** 1 in 4,611 calls on `echo-typescript` at 4 pairs in CI; PoC-4 had 4 in 11,596 on the TypeScript echo and 1 in 4,512 on `echo-python` at 2 pairs. The chassis answers 502 for a non-retryable workload failure, and Traefik answers 502 for a broken backend connection; no run kept the container logs. Suggested: a follow-up under the PoC-4 debt (keep chassis and Traefik logs for a failed scenario in `run_matrix.py`, then find the source). It does not block a criterion: the load suites accept up to 0.1% failed calls (suggested).
- **The workload flag contract.** The scale stack passes `--drain-timeout-s` to every workload image. The TypeScript agent refused it from `764a25c` until `ce9274d`, and no offline test ran the image with the stack's command. Suggested: a static test that every workload image's entry point accepts the Compose `command` of `docker-compose.scale.yaml` (the TypeScript side is now `test/auth.test.ts`, "starts with the PoC-4 scale stack's command").
