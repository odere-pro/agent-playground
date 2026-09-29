---
title: "G-5: Cost model: GPU hosting cost vs API savings, with the break-even point"
labels: ["story", "priority:P0", "phase:1-router", "area:router", "size:S", "layer:platform"]
milestone: "W3 Simplifier SLM"
index: 31
epic_id: G-5
depends_on: ["005 G-3"]
blocks: ["035 S-6"]
epic_refs: [J, risks]
---

## Why

The top risk in the epic is that GPU costs more than the tokens saved. This cost model is the mitigation for that risk, placed right before the first GPU spend on training and serving. It also completes the Phase 1 done-when: the baseline is measured, the break-even point is known, and the dashboard shows live token spend.

**Open concern (review 2026-09-29), to explore until we align:** the break-even must count the evaluator gate. Until 075 E-3, every gated call adds a big-model judge call. Show the break-even three ways: without the gate, with the big-model judge, and with the encoder SLM's CPU cost.

## What

- Inputs: baseline spend and volume from 005 G-3, and the share of spend that the simplifier and history swap could move to an SLM.
- GPU costs: training per run, and hosting for serving, with on-demand and spot or preemptible prices.
- The cost rules from J: idle pools scale to zero, encoders run on CPU, and GPUs are used only for serving and training.
- Options compared: a rented GPU host, a GPU node in the first cloud, and a local GPU.
- Outputs: cost per 1,000 requests (router cost plus GPU hosting cost) now and after the switch, and the break-even point.
- Sensitivity to fallback rate (target < 10%), GPU use, and token reduction per request.
- All inputs in one place (suggested: a small script or sheet), so the model can be re-run with live numbers.
- A go or no-go recommendation for training.

## Out of scope

- The baseline itself (005 G-3).
- GPU setup on Kubernetes (039 X-2).
- The cost dashboard per agent and pool (120 X-7).

## Acceptance criteria

- [ ] The model uses the 005 G-3 baseline, and every other input has a source or a stated assumption.
- [ ] It gives the break-even point as requests per day and as monthly spend.
- [ ] It shows cost per 1,000 requests before and after, including GPU hosting cost.
- [ ] It shows how break-even moves with fallback rate (0%, 10%, 20%) and with spot vs on-demand GPUs.
- [ ] The go or no-go for training is recorded with the owner's sign-off.
- [ ] The Phase 1 done-when is checked: the baseline is published, the break-even point is known, and the savings dashboard shows live token spend.

## Dependencies

- Depends on: [005 G-3](005-G-3-baseline-report.md)
- Blocks: [035 S-6](035-S-6-train-candidates.md)

## References

- Epic story: [G-5 in Phase 1](../slm-agent-platform-epic-v3.md#phase-1-baseline-and-llm-router)
- Epic context: [J](../slm-agent-platform-epic-v3.md#app-j) · [Risks](../slm-agent-platform-epic-v3.md#risks)
- Backlog plan: [000-plan.md](000-plan.md)
