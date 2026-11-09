# ADR-014: A LangGraph agent keeps no state between runs; session state, when needed, sits behind the chassis's `StatePort`

- **Date:** 2026-11-09
- **Deciders:** the user (epic owner) accepts; proposed by the phase A session for `chassis-architect`, from [128 CH-9](../issues/128-CH-9-langgraph-checkpointer-interrupts.md)
- **Format:** Michael Nygard's template: Status, Context, Decision, Consequences
- **Related:** [ADR-001](001-chassis-delivery-model.md) hard requirement 1 (only the chassis holds credentials) and the stateless replica; [ADR-006](006-agent-engines-default-supported-lanes.md) (LangGraph is the default engine); [ADR-015](015-interrupts-end-the-run.md) (interrupts, which need this decision first); [the product plan](../../plans/2026-11-06-agent-container-product.md), phase C and gap P3; [the gap review's option](../../reviews/2026-11-06-backlog-review/findings-W1012-production-later.md); [140 CH-20](../issues/140-CH-20-session-memory-tools-and-saver.md) (the session memory tools and the saver, from this ADR), [114 P-2b](../issues/114-P-2b-mcp-run-memory-config-tools.md) (the platform MCP server's memory tools, the orchestrator's, not these), [125 L-1](../issues/125-L-1-shared-memory-at-scale.md) (starts from this ADR), [113 O-9](../issues/113-O-9-orchestrator-memory.md) and [100 O-2](../issues/100-O-2-temporal-checkpoints.md) (the orchestrator's own state, not this ADR's); [the contract](../../specs/contract.md) (`session.id`, `context_ref`, idempotency)

## Status

Proposed 2026-11-09. The user accepts or picks another option; 128 CH-9's first box ticks on acceptance. `chassis-architect` reviewed the proposal on 2026-11-09 (the key bound to the principal, the fingerprint, the owner issue, and the latest-only rule are its findings); item 2's auth-off rule wants a `platform-security` look before acceptance.

## Context

The default engine for a new agent is LangGraph (ADR-006, amended 2026-11-06). The template's graph is a `StateGraph(MessagesState)` compiled with no checkpointer (`packages/workloads/echo-langgraph/src/echo_langgraph/handle.py`, `_graph`): one run is one graph invocation on the request's text, and nothing is kept when it ends. LangGraph's own way to keep state is a checkpointer, a `BaseCheckpointSaver` that writes the graph state after every super-step, keyed by a `thread_id`; interrupts and resumes stand on it.

Facts that shape the choice:

- **Only the chassis holds credentials** (ADR-001 hard requirement 1). A LangGraph saver that talks to a store itself (`RedisSaver`, `PostgresSaver`) needs that store's credential in the workload container. That is the one thing the design forbids, and the PoC-5 admission rules refuse a Secret in the workload (rule 6c).
- **A replica keeps nothing a retry needs** (`packages/chassis/CLAUDE.md`; PoC-4 exit criterion 8). An in-process `InMemorySaver` in the workload is hidden state: a second replica, or a restart, loses it. The same rule holds for the workload container.
- **The chassis already has a store behind a port.** `StatePort` (Valkey in `local` and `cloud`, in memory in `fake`, one contract suite) holds the idempotency claims and the rate-limit buckets, keyed per agent. Every workload reaches the chassis's services through the tool proxy on the proxy port (`/mcp`), where the chassis adds the credential and the per-key tool grants apply (ADR-008).
- **The run already carries a session.** `x-session-id` (`spec.observability.session_header`) lands on the run span and in baggage as `session.id`; `context_ref` is in the envelope, accepted and passed through, unused; the idempotency fingerprint includes it.
- **No tool holds agent state today.** The workload's tool proxy (`/mcp` on the proxy port, `CHASSIS_TOOL_URL`, contract section 7) lists what `ToolPort` lists: the gateway's tools. [114 P-2b](../issues/114-P-2b-mcp-run-memory-config-tools.md) is a different surface: the platform MCP server on `/v1/mcp` for outside clients, whose memory tools are the orchestrator's (113 O-9). [125 L-1](../issues/125-L-1-shared-memory-at-scale.md) designs shared memory at scale and starts from this ADR. The orchestrator's execution state (100 O-2, 113 O-9) is the orchestrator's, not the agent's.
- **The session header is the caller's.** `x-session-id` is any printable string up to 256 bytes; the chassis checks nothing about it. The rate-limit key already binds a caller's data to the principal with `sha256(Principal.subject)[:32]` (P-27). The idempotency fingerprint is `{agent, input, context_ref}` and has no session in it. The tool endpoint keys a write by the run key, the idempotency scope, so a takeover run repeats the same write (contract section 7).
- **The gap review** (2026-11-07) suggested a fourth shape: the chassis exposes the store as MCP tools scoped by `session.id`, with writes keyed by the run, and LangGraph's saver runs over those tools in the template.

Options considered:

| Option | What it is | Hard requirement 1 | Stateless replica | Cost |
| ------ | ---------- | ------------------ | ----------------- | ---- |
| 1. None | The agent is stateless per run. The caller carries the context (the OpenAI and Anthropic interfaces already send the whole conversation; `context_ref` names a context held elsewhere) | Holds | Holds | None. No interrupt resume (ADR-015) |
| 2. A LangGraph saver on the store | `RedisSaver` on Valkey, or `PostgresSaver`, in the workload | Breaks: a store credential in the workload | Holds | A new dependency per engine |
| 3. `InMemorySaver` in the workload | LangGraph's in-process saver | Holds | Breaks: state in one process | None |
| 4. A saver over chassis tools | The state behind `StatePort`; the workload reads and writes it through chassis MCP tools on its tool proxy, scoped to the run's session and principal by the chassis; a `BaseCheckpointSaver` over those tools in the template | Holds | Holds | Tool calls per super-step; the tools and the saver to build (140 CH-20) |

## Decision

1. **A LangGraph agent keeps no state between runs by default.** The template's graph compiles with no checkpointer. One run is one request and one result; the caller carries the conversation, as the OpenAI and Anthropic interfaces do, or names it with `context_ref`. Option 1 is what ships today and what `agentctl new` renders.
2. **When an agent must keep state between runs, the state is a session's**, named by `session.id` (`spec.observability.session_header` on the request) and bound to the caller: the store key is `chassis:mem:v1:<agent>:<user.id>:<session.id>` (suggested), with `user.id` the `sha256(Principal.subject)[:32]` hash the rate limit and the baggage already use (P-27). One principal cannot read or write another's session by sending its id. With auth off there is no `user.id`, so a session is unprotected; suggested: the config loader refuses session memory when auth is off in `cloud`, and allows it in `fake` and `local`. A run with no session header has no state between runs.
3. **The state lives behind the chassis's `StatePort`**, in the same store as the idempotency claims, with a TTL from the last write and a size cap per session as `spec.*` keys (suggested: `spec.memory.ttl_s` 86400, as `spec.idempotency.ttl_s`; `spec.memory.max_bytes` 262144; a write past the cap is refused with a clear tool error). Only the latest checkpoint per session is kept (suggested): LangGraph's saver writes one checkpoint per super-step, and a history would fill any cap in a few steps.
4. **The workload reaches it only through chassis tools on its tool proxy** (`/mcp` on the proxy port; suggested names: `memory.get`, `memory.put`, `memory.delete`). The chassis scopes every call to the run's session and principal, so the workload names no session and needs no fact it does not have; the saver uses one constant `thread_id`. Each write is keyed by the run key (the idempotency scope, as the tool endpoint already keys writes), so a retried or taken-over run repeats its own write and never another run's. `memory.put` and `memory.delete` are write tools under section 7's rule. The workload holds no store credential (ADR-001 hard requirement 1).
5. **What this touches in the chassis**, built by [140 CH-20](../issues/140-CH-20-session-memory-tools-and-saver.md): a second tool source behind `/mcp` next to `ToolPort`'s gateway tools (or a `ToolPort` extension), the `spec.memory` keys, and the idempotency fingerprint, which gains `session.id` when session memory is on (suggested; a `spec.idempotency` touch), so two sessions with the same input and client key never replay each other's answer. Not the envelope, not the event schema, no `schema_version` bump. `context_ref` keeps its role: the caller's pointer to read context the chassis resolves into `ctx` (125 L-1), caller-owned and in the fingerprint; session memory is agent-written. The two do not overlap.
6. **The template ships a `BaseCheckpointSaver` over those tools** when 140 CH-20 lands, as an opt-in (suggested: `agentctl new --memory session`). Until then the template has no saver; a rendered project's `docs/decisions.md` names this ADR.
7. **`InMemorySaver` is for tests only.** It never appears in a rendered project's `handle`, and the workload image never holds a store client.
8. **The state is agent data.** A checkpoint holds the graph's messages and the agent's own fields. The model proxy never hands the workload a key, so none can land there. What the agent writes is its own; the chassis's redaction applies to what leaves through a run.
9. **The orchestrator's state is not this.** A workflow's execution state and approvals are the orchestrator's (100 O-2, 113 O-9, 109 O-8). An agent's checkpoint never stands in for them. When the public A2A endpoint is decided (001 DEC-1), A2A's `contextId` is the native session carrier and maps to `session.id` (suggested).

## Consequences

- Easier: today's agents stay simple and horizontal. No new dependency in any workload image, no credential outside the chassis, nothing a replica must hold. The one path for every platform service, the tool proxy, stays the one path.
- Easier: when session memory comes, it is one more tool source on an existing port, with the fake and the contract suite the port already has, and one saver class in the template.
- Harder: an interrupt and resume in LangGraph needs a checkpointer, so it waits for item 6 (ADR-015 decides what happens until then: the run ends with a clear error).
- Harder: a saver over MCP tools pays per super-step. In the `inprocess` lane the fake store costs nothing; in `sidecar` it is loopback MCP calls (a `put` and a `put_writes`, each its own streamable-HTTP call with its `initialize`; suggested budget: 10 ms p50 for the pair, a sidecar number); in `remote` each is a network hop to the proxy on the pod IP under the per-run secret, so a graph with many small nodes feels it. The saver can batch per run; that is 140 CH-20's design.
- Cost: the chassis owns one more kind of data in its store, bounded by the TTL and the cap, under a key that binds it to the caller as the rate limit's key does.
- Open: a long-term memory across sessions (125 L-1) and the orchestrator's memory (113 O-9) start from here and may add layers; they do not change items 1 to 4.

## Revisit

- When 140 CH-20 builds the tools: measure the saver's cost per super-step in the `sidecar` lane. Above the suggested 10 ms p50, or above 20% of a run's latency on the template's lookup task, reopen item 4 (a batched saver, or a chassis-side checkpoint store with its own port).
- When a product agent needs state between runs before 140 CH-20 lands: build the tools first; do not pick option 2 or 3 as a shortcut.
- If LangGraph changes its checkpointer interface in a way the saver cannot follow, or ADR-006 reopens the default engine.
