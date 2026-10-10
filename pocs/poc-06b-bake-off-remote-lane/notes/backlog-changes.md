# Backlog changes when PoC-6b closes

Status: **Applied 2026-10-09** with skill `planning-sync`; the kind results are not in yet. Each change is a "Status after PoC-6" bullet at the end of `## What`. No acceptance box was ticked, and no order, size, or dependency changed.

Evidence: [Claude CLI capture](2026-10-09-claude-cli-capture.md), [kagent probe](2026-10-09-kagent-probe.md), [engines on kind](2026-10-09-lanes-b-kind.md), [contract v5](../../../docs/contracts/contract-v5.md), [ADR-006](../../../docs/planning/adr/006-agent-engines-default-supported-lanes.md).

## Applied

- **055 CH-6**: plain-A2A mode (`spec.engine.protocol: a2a`), the remotes on gVisor, `kagent-adk` as the remote solution (full kagent is a no-go), the one `trustedRepositories` addition, and what the chassis cannot see.
- **013 CH-2**: `POST /v1/messages` on the model proxy; a refused route answers 403 `model_route_denied`. This closes the contract v4 open question.
- **054 H-16**: tool names through the gateway and the Claude CLI; tool calls are invisible in plain-A2A mode.
- **058 CH-8**: mappings for smolagents and the Claude Agent SDK.
- **008 H-14**: see 6a.

## Carry: findings with their issue

- **013 CH-2**: the chat route still sends `ModelError.message`, while the Anthropic route sends fixed text. Aligning them is a separate change.
- **013 CH-2 / port**: `ModelPort` has no finish reason, so `stop_reason: max_tokens` cannot be sent (contract v5, A.12).
- **Open in contract v5**: the CLI's reaction to 4xx and 5xx is untested; the Bash tool's child sees the remote token; version negotiation when `schema_version` `"1"` exists.
- **Gap**: no offline `EngineConnectorContract` binding for `echo-smolagents` and `echo-claude-agent` in the `remote` lane. suggested: add one in a later task.
- **055 CH-6 (final security review, MEDIUM, not fixed here)**: the remote connector caps neither the bytes of a remote's output nor the event count, in both modes. A hostile remote can stream for the whole `timeout_ms` and grow its chassis's memory. Harm is bounded: one chassis serves one remote (admission rule 6a), so only that remote's own service fails, at its memory limit. suggested: an output cap across deltas and snapshots, with `error{a2a.output_too_large}` and a cancel, in a later task.
- **055 CH-6 (LOW, not fixed here)**: the connector's `except` for a malformed stream (`ValueError`, `ParseError`) wraps the whole read loop, so a chassis bug that raises `ValueError` there reads as a retryable `a2a.transport`. Narrow it to the SDK iterator.
- **054 H-16 (LOW, not fixed here)**: the smolagents and openai-agents workloads build their httpx clients with `follow_redirects=True`. The bearer is dropped on a cross-origin redirect, `traceparent` is not. The chassis never redirects, but the MCP gateway answered `/mcp` with a 307 in PoC-5, so switching it off needs a test of the exact tool URL first.

## Not applied: waits on kind CI or the user

- The kind results (`poc06-kind.yml`) for every engine, and the `kagent-adk` cells of the scorecard.
- ADR-006 acceptance, by the user at the PR.
