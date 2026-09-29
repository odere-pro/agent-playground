---
title: "O-2: Temporal workflows with a checkpoint per step"
labels: ["story", "priority:P1", "phase:7-orchestrator", "area:orchestrator", "size:M", "layer:platform"]
milestone: "W9 Orchestrator"
index: 100
epic_id: O-2
depends_on: ["099 O-1", "050 CH-5"]
blocks: ["101 O-13", "102 O-7", "103 O-11", "104 O-3", "107 O-6", "109 O-8", "112 O-16", "113 O-9"]
epic_refs: [F.8]
---

## Why

Run state must live outside the orchestrator process, so any pod can resume any run. Temporal gives durable state, retries, and a checkpoint after every step. Most of the orchestrator wave builds on it, and it serves the "run recovery 100%" success metric.

## What

- Temporal in Docker Compose and on Kubernetes (suggested: the Temporal Helm chart, with persistence on the CloudNativePG Postgres). No epic story owns Temporal itself, so it is set up here.
- The Temporal worker runs in the orchestrator's chassis, which alone holds the Temporal credential. It calls the workload once per step through the connector (suggested; gap (b) in [the gaps in the backlog plan](000-plan.md#adr-001-follow-ups)). Each run is a Temporal workflow with `run_id` as its workflow ID.
- A run takes an ordered list of steps (agent plus input). Each step is a Temporal activity, with a timeout and a retry policy, that calls the agent through the agent port.
- A checkpoint after every step, stored by Temporal. Minimal run state: `run_id`, steps, and step status.
- Workflow code does no I/O. All calls happen in activities, so replays are deterministic.
- Resume: a failed run continues from the last checkpoint on any pod (suggested: Temporal reset to the last completed step).
- A run API to start, read, and resume runs (suggested: `POST /v1/runs`, `GET /v1/runs/{run_id}`, `POST /v1/runs/{run_id}/resume`). These are declared operations (050 CH-5).
- `POST /v1/runs` returns a `run_id` at once, because the chassis timeout rules out long calls.

## Reuse

- **Use:** Temporal. Its event history is the checkpoint, and resume is a replay. suggested: the Temporal Helm chart with Postgres persistence.
- **Build:** the workflow and activity definitions.
- Details: [reuse analysis](../poc/010-reuse-analysis.md)

## Out of scope

- Stable step keys, so a resume never repeats a side effect (102 O-7).
- Workflow files and the `chain`, `fan_out`, and `compare` patterns (103 O-11).
- The full execution state with plan and input and output references (113 O-9).
- Cancel (109 O-8).

## Acceptance criteria

- [ ] Temporal runs in Docker Compose and on Kubernetes, and the orchestrator worker connects in both.
- [ ] Killing the orchestrator pod during step 2 of a three-step run: the run finishes on another pod, and step 1 is not called again.
- [ ] A run that fails at step 2 is resumed after the fault is fixed, and only steps 2 and 3 run.
- [ ] A replay test in CI runs recorded workflow histories against the current code and fails on a non-deterministic change.
- [ ] The run status API returns each step and its status.
- [ ] `POST /v1/runs` returns a `run_id` at once, before the first step ends.
- [ ] Injected failures (pod kill, agent timeout, transient error) resume 100% of runs from a checkpoint, and a run recovery metric shows on `/metrics`.
- [ ] Workflows are tested offline with Temporal's time-skipping test environment.

## Dependencies

- Depends on: [099 O-1](099-O-1-orchestrator-agent.md), [050 CH-5](050-CH-5-service-operations.md)
- Blocks: [101 O-13](101-O-13-version-pinning.md), [102 O-7](102-O-7-idempotent-steps.md), [103 O-11](103-O-11-workflow-engine.md), [104 O-3](104-O-3-agent-pools-keda.md), [107 O-6](107-O-6-run-budgets.md), [109 O-8](109-O-8-cancel-approval.md), [112 O-16](112-O-16-event-triggers.md), [113 O-9](113-O-9-orchestrator-memory.md)

## References

- Epic story: [O-2 in Phase 7](../slm-agent-platform-epic-v3.md#phase-7-orchestrator)
- Epic context: [F.8](../slm-agent-platform-epic-v3.md#f8)
- Backlog plan: [000-plan.md](000-plan.md)
