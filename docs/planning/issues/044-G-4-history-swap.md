---
title: "G-4: History swap in the chassis: long past answers replaced by simplified versions"
labels: ["story", "priority:P1", "phase:1-router", "area:router", "size:M", "layer:agent-profile"]
milestone: "W4 Simplifier agent live"
index: 44
epic_id: G-4
depends_on: ["012 H-3", "040 D-0", "013 CH-2"]
blocks: ["045 A-4"]
epic_refs: [F.1, J]
---

## Why

Chat history is sent again on every turn, so long past answers cost tokens again and again. Swapping them for their simplified versions is the second savings lever in the epic's Value section. It comes after the recorder (040 D-0), because the short versions come from stored records, and it stays off until 045 A-4 switches it on.

## What

- A step in the model call path: before a model call, past assistant answers above a length threshold are replaced by their simplified versions.
- In the `sidecar` lane, the model call path is the chassis model proxy (013 CH-2). Running the swap there goes beyond a thin pass-through ([ADR-001](../adr/001-chassis-delivery-model.md) item 6). This issue decides whether it runs in the proxy or as a LiteLLM pre-call hook. suggested: a LiteLLM pre-call hook. This is gap (e) in [the gaps in the backlog plan](000-plan.md#adr-001-follow-ups).
- The system prompt and the latest user turn are never changed.
- Works for native, OpenAI, and Anthropic message formats.
- Short versions are looked up by a hash of the original text through `StorePort`, from records the recorder wrote. Only records that passed the `facts_kept` gate (≥ 0.95) are used.
- Suggested config: `harness.history_swap.enabled` (default `false`) and `harness.history_swap.min_tokens`, reloaded without a restart.
- A missing short version, a lookup error, or a timeout keeps the original answer. The call never fails because of the swap.
- The response reports which messages were swapped and the record IDs used, so the call can be replayed.
- Suggested metric: `history_tokens_saved` per agent, next to the router's token counts (004 G-2).

## Out of scope

- Switching the swap on and making sure short versions exist (045 A-4).
- Session context in Valkey at scale (125 L-1).
- Token and cost counting itself (004 G-2).

## Acceptance criteria

- [ ] Where the swap runs (model proxy or LiteLLM pre-call hook) is decided and written down, with the reason.
- [ ] With the swap on, a history with a long past answer that has a stored short version reaches the model with the short version, in all three formats.
- [ ] The system prompt, the latest user turn, and short answers pass through unchanged.
- [ ] A short version whose `facts_kept` is below 0.95 is never used.
- [ ] The swap is off by default and can be switched by config without a restart.
- [ ] With the store down, calls still succeed with the original history.
- [ ] Each response lists the swapped messages and the record IDs used.
- [ ] Tokens saved by the swap show as a metric per agent.

## Dependencies

- Depends on: [012 H-3](012-H-3-model-port.md), [040 D-0](040-D-0-recorder-agent.md), [013 CH-2](013-CH-2-outbound-model-proxy.md)
- Blocks: [045 A-4](045-A-4-history-swap-on.md)

## References

- Epic story: [G-4 in Phase 1](../slm-agent-platform-epic-v3.md#phase-1-baseline-and-llm-router)
- Epic context: [F.1](../slm-agent-platform-epic-v3.md#f1) · [J](../slm-agent-platform-epic-v3.md#app-j)
- Backlog plan: [000-plan.md](000-plan.md)
