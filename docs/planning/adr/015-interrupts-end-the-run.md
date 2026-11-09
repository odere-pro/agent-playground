# ADR-015: An interrupt ends the run with a clear error; an agent run never waits for a person

- **Date:** 2026-11-09
- **Deciders:** the user (epic owner) accepts; proposed by the phase A session for `chassis-architect`, from [128 CH-9](../issues/128-CH-9-langgraph-checkpointer-interrupts.md)
- **Format:** Michael Nygard's template: Status, Context, Decision, Consequences
- **Related:** [ADR-014](014-langgraph-state-between-runs.md) (no checkpointer by default, which a resume would need); [ADR-006](006-agent-engines-default-supported-lanes.md); [ADR-008](008-security-identity-guardrails-defense-in-depth.md) (write tools per key); [the contract](../../specs/contract.md) (`a2a.unsupported_state`; the A2A task states); [109 O-8](../issues/109-O-8-cancel-approval.md) (human approval steps in the orchestrator); [095 P-4](../issues/095-P-4-mcp-scopes-audit.md) (MCP elicitation, waits on this ADR); proposal P-28 in the [backlog review proposals](../../reviews/2026-11-06-backlog-review/proposals.md) (a person on writes with outside effect); [the product plan](../../plans/2026-11-06-agent-container-product.md), phase C and gap P3

## Status

Proposed 2026-11-09. The user accepts or picks another option; 128 CH-9's second box ticks on acceptance. The template test for item 2 is in (`test_an_interrupt_is_refused_with_a_clear_error`).

## Context

A LangGraph graph pauses with `interrupt(value)`: the node stops, the value goes to the caller, and the graph resumes later with `Command(resume=...)` on the same thread. The resume needs a checkpointer; ADR-014 ships none by default. Measured on LangGraph 1.2.12 (2026-11-09): with no checkpointer, `interrupt()` does not raise. The stream yields an `updates` part `{"__interrupt__": (Interrupt(...),)}` and the graph stops. The template's event mapper ignored that part, so the run ended `metrics`, `end ok` with no answer: a silent failure.

The `handle` contract has no event for "ask the caller and wait". Its events are `start`, `delta`, `tool_call`, `metrics`, `end`, `error`. Over A2A, a workload can put a task in `INPUT_REQUIRED` or `AUTH_REQUIRED`; the chassis connector answers `error a2a.unsupported_state` and cancels the task (the sidecar lane) or leaves it open (plain mode). The public interfaces have no pause either: `/v1/run` is one request and one result; the OpenAI and Anthropic interfaces end a completion; `/v1/mcp` could use MCP elicitation (095 P-4 waits on this ADR); A2A has `input-required` natively. A paused run that ended with no `end` is not an idempotency hit, and an open task across a rollout fights the drain order (ADR-012).

Who decides when a person must step in: the product puts human approval in the orchestrator (109 O-8: an approval step type, an approve and reject API, a timeout), and P-28 asks that a write tool with outside effect is off unless the key names it, with an approval checkpoint decided by an ADR. 109 and 110 wait on this ADR to say whether an agent can ever ask for approval mid-run.

Options considered:

| Option | What it is | Contract change | Needs ADR-014's saver | Who holds the pause |
| ------ | ---------- | --------------- | --------------------- | ------------------- |
| 1. Refuse, clearly | An interrupt ends the run with one `error` event whose code names it; the chassis keeps `a2a.unsupported_state` for A2A pauses | None | No | Nobody: the agent answers with what it needs, the caller's next run carries the decision |
| 2. A pause in the contract | A new event `input_required {prompt, schema}`; the run ends, the task stays open, the caller resumes on the same session; mapped to A2A `input-required` and MCP elicitation | A schema bump (events v1), a mapping per interface, the idempotency cache, the drain | Yes | The chassis, for every open task |
| 3. The orchestrator owns pauses | An agent never pauses; approval is a workflow step (109 O-8) between agent runs | None for the agent | No | The orchestrator |

## Decision

1. **An agent run is one request and one result.** It never waits for a person or a caller mid-run. What needs a human decision is answered in the result (`end ok`, the output says what is needed) and the caller's next run carries the decision; across steps of a workflow, the orchestrator's approval step does it (109 O-8). Options 1 and 3 together.
2. **A LangGraph `interrupt()`, and any `__interrupt__` update, ends the run with one `error` event**: code `interrupt_unsupported` (suggested), `retryable: false`, a message that names this ADR. The `delta`s already sent stand; no `metrics` or `end` follows. The template's mapper does this (`EventMapper.on_part` raises `InterruptUnsupported`; `error_event` maps it), and the template test holds it. The message carries up to 200 characters of the interrupt value: agent data, model- or tool-derived, under the chassis's redaction like any error text; never a secret path. A graph that wants a decision asks for it in its answer instead.
3. **The chassis keeps `error a2a.unsupported_state`** for a workload task in `INPUT_REQUIRED` or `AUTH_REQUIRED`, as built and as the contract says, now with this ADR as the reason. The task is cancelled in the sidecar lane.
4. **A write with outside effect and no person** follows P-28: a write tool is off unless the key names it (ADR-008); the person who approves it sits in the orchestrator (109 O-8), never inside the agent's run.
5. **The door for a pause stays marked, not open.** When a product agent needs a pause that a next run cannot carry, a new ADR designs option 2: `input_required` as an events v1 addition, A2A `input-required` and MCP elicitation (095 P-4) as its two carriers, the OpenAI and Anthropic interfaces refusing it, and ADR-014's saver as the prerequisite. Not before both the saver and the need exist.

## Consequences

- Easier: no contract change now, no open tasks for the chassis to hold across a drain, no new idempotency state. The agent's failure mode is loud (one error with a name) instead of silent (`end ok` with nothing).
- Easier: 109 O-8 and 110 C-6 can be designed: an agent does not ask for approval mid-run, so approval is a step, with its own API and timeout.
- Harder: a graph author who wants `interrupt()` for human-in-the-loop gets a refusal and must shape the agent as two runs. A rendered project's `docs/decisions.md` names this ADR (the row is in the template).
- Harder for MCP callers: no elicitation from an agent until item 5 is designed (095 P-4 stays as it is).
- Cost: one exception class and one error code in the LangGraph mapping, in the workload and the template alike; one test each.

## Revisit

- When a product agent needs a mid-run pause that a next run cannot carry (the need named by a user story, not a framework feature), and ADR-014's saver exists: design item 5.
- If the A2A specification or MCP elicitation changes how a pause is carried, or if LangGraph's `interrupt()` starts to raise without a checkpointer (the mapper test would fail; the code path moves to `error_event`).
- If ADR-006 reopens the default engine: the chosen engine's pause primitive gets the same mapping.
