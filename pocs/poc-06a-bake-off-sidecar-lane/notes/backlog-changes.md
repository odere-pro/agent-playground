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
- The load run per sidecar engine (Docker) and the kind hostile suite (CI). Record them with skill `record-measurement`.
