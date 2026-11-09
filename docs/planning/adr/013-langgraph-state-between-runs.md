# ADR-013: A LangGraph agent keeps no state between runs; when it must, the state sits behind the chassis's `StatePort`, reached through chassis tools and keyed by `session.id`

- **Date:** 2026-11-09
- **Deciders:** the user (epic owner) accepts; proposed by the phase A session for `chassis-architect`, from [128 CH-9](../issues/128-CH-9-langgraph-checkpointer-interrupts.md)
- **Format:** Michael Nygard's template: Status, Context, Decision, Consequences
- **Related:** [ADR-001](001-chassis-delivery-model.md) hard requirement 1 (only the chassis holds credentials) and the stateless replica; [ADR-006](006-agent-engines-default-supported-lanes.md) (LangGraph is the default engine); [ADR-014](014-interrupts-end-the-run.md) (interrupts, which need this decision first); [the product plan](../../plans/2026-11-06-agent-container-product.md), phase C and gap P3; [the gap review's option](../../reviews/2026-11-06-backlog-review/findings-W1012-production-later.md); [114 P-2b](../issues/114-P-2b-mcp-run-memory-config-tools.md) (the memory tools), [125 L-1](../issues/125-L-1-shared-memory-at-scale.md) (starts from this ADR), [113 O-9](../issues/113-O-9-orchestrator-memory.md) and [100 O-2](../issues/100-O-2-temporal-checkpoints.md) (the orchestrator's own state, not this ADR's); [the contract](../../specs/contract.md) (`session.id`, `context_ref`, idempotency)

## Status

Proposed 2026-11-09. The user accepts or picks another option; 128 CH-9's first box ticks on acceptance.

## Context

The default engine for a new agent is LangGraph (ADR-006, amended 2026-11-06). The template's graph is a `StateGraph(MessagesState)` compiled with no checkpointer (`packages/workloads/echo-langgraph/src/echo_langgraph/handle.py`, `_graph`): one run is one graph invocation on the request's text, and nothing is kept when it ends. LangGraph's own way to keep state is a checkpointer, a `BaseCheckpointSaver` that writes the graph state after every super-step, keyed by a `thread_id`; interrupts and resumes stand on it.

Facts that shape the choice:

- **Only the chassis holds credentials** (ADR-001 hard requirement 1). A LangGraph saver that talks to a store itself (`RedisSaver`, `PostgresSaver`) needs that store's credential in the workload container. That is the one thing the design forbids, and the PoC-5 admission rules refuse a Secret in the workload (rule 6c).
- **A replica keeps nothing a retry needs** (`packages/chassis/CLAUDE.md`; PoC-4 exit criterion 8). An in-process `InMemorySaver` in the workload is hidden state: a second replica, or a restart, loses it. The same rule holds for the workload container.
- **The chassis already has a store behind a port.** `StatePort` (Valkey in `local` and `cloud`, in memory in `fake`, one contract suite) holds the idempotency claims and the rate-limit buckets, keyed per agent. Every workload reaches the chassis's services through the tool proxy on the proxy port (`/mcp`), where the chassis adds the credential and the per-key tool grants apply (ADR-008).
- **The run already carries a session.** `x-session-id` (`spec.observability.session_header`) lands on the run span and in baggage as `session.id`; `context_ref` is in the envelope, accepted and passed through, unused; the idempotency fingerprint includes it.
- **Memory tools are planned.** [114 P-2b](../issues/114-P-2b-mcp-run-memory-config-tools.md) adds run, memory, config, and workflow tools behind the tool proxy. [125 L-1](../issues/125-L-1-shared-memory-at-scale.md) designs shared memory at scale and starts from this ADR. The orchestrator's execution state (100 O-2, 113 O-9) is a different thing: the orchestrator's, not the agent's.
- **The gap review** (2026-11-07) suggested a fourth shape: the chassis exposes the store as MCP tools scoped by `session.id`, with writes keyed by the run, and LangGraph's saver runs over those tools in the template.

Options considered:

| Option | What it is | Hard requirement 1 | Stateless replica | Cost |
| ------ | ---------- | ------------------ | ----------------- | ---- |
| 1. None | The agent is stateless per run. The caller carries the context (the OpenAI and Anthropic interfaces already send the whole conversation; `context_ref` names a context held elsewhere) | Holds | Holds | None. No interrupt resume (ADR-014) |
| 2. A LangGraph saver on the store | `RedisSaver` on Valkey, or `PostgresSaver`, in the workload | Breaks: a store credential in the workload | Holds | A new dependency per engine |
| 3. `InMemorySaver` in the workload | LangGraph's in-process saver | Holds | Breaks: state in one process | None |
| 4. A saver over chassis tools | The state behind `StatePort`; the workload reads and writes it through chassis MCP tools on the tool proxy, scoped to the run's session by the chassis; a `BaseCheckpointSaver` over those tools in the template | Holds | Holds | One tool round trip per super-step; the tools (114 P-2b) and the saver to build |

## Decision

1. **A LangGraph agent keeps no state between runs by default.** The template's graph compiles with no checkpointer. One run is one request and one result; the caller carries the conversation, as the OpenAI and Anthropic interfaces do, or names it with `context_ref`. Option 1 is what ships today and what `agentctl new` renders.
2. **When an agent must keep state between runs, the state is keyed by `session.id`**, the value of `spec.observability.session_header` on the request, never by an id the workload makes up. A run with no session header has no state between runs.
3. **The state lives behind the chassis's `StatePort`**, in the same store as the idempotency claims, under its own key prefix (suggested: `chassis:mem:v1:<agent>:<session.id>`), with a TTL from the last write (suggested: 24 h) and a size cap per session (suggested: 256 KiB; a write past it is refused with a clear tool error). The workload reaches it only through chassis tools on the tool proxy (114 P-2b; suggested names: `memory.get`, `memory.put`, `memory.delete`). The chassis scopes every call to the run's session and keys each write by the run id, so a retried run overwrites its own write and never another run's. The workload holds no store credential (ADR-001 hard requirement 1).
4. **The template ships a `BaseCheckpointSaver` over those tools** when 114 P-2b lands, as an opt-in (suggested: `agentctl new --memory session`), with `thread_id` = `session.id`. Until then the template has no saver, and `docs/decisions.md` in a rendered project says so and names this ADR.
5. **`InMemorySaver` is for tests only.** It never appears in a rendered project's `handle`, and the workload image never holds a store client.
6. **The state is agent data, not a credential store.** A checkpoint holds the graph's messages and the agent's own fields. The model proxy never hands the workload a key, so none can land there; the chassis's output filter and guardrails apply to what leaves through a run, not to the store. What the agent writes is its own.
7. **The orchestrator's state is not this.** A workflow's execution state and approvals are the orchestrator's (100 O-2, 113 O-9, 109 O-8). An agent's checkpoint never stands in for them.

## Consequences

- Easier: today's agents stay simple and horizontal. No new dependency in any workload image, no credential outside the chassis, nothing a replica must hold. The one path for every platform service, the tool proxy, stays the one path.
- Easier: when memory comes, it is one more tool family on an existing port, with the fake and the contract suite the port already has, and one saver class in the template.
- Harder: an interrupt and resume in LangGraph needs a checkpointer, so it waits for item 4 (ADR-014 decides what happens until then: the run ends with a clear error).
- Harder: a saver over MCP tools pays one round trip per super-step (suggested budget: 10 ms p50 on the loopback proxy). A graph with many small nodes feels it. The saver can batch writes per run; that is the saver's design, in 114 P-2b.
- Cost: the chassis owns one more kind of data in its store, with a TTL and a size cap to keep it bounded, and a per-session key the rate limit and idempotency keys already share a prefix style with.
- Open: a long-term memory across sessions (125 L-1) and the orchestrator's memory (113 O-9) start from here and may add layers; they do not change items 1 to 3.

## Revisit

- When 114 P-2b's memory tools are built: measure the saver's write latency per super-step on the loopback proxy. Above the suggested 10 ms p50, or above 20% of a run's latency on the template's lookup task, reopen item 3 (a batched saver, or a chassis-side checkpoint store with its own port).
- When a product agent needs state between runs before 114 P-2b lands: build the tools first; do not pick option 2 or 3 as a shortcut.
- If LangGraph changes its checkpointer interface in a way the saver cannot follow, or ADR-006 reopens the default engine.
