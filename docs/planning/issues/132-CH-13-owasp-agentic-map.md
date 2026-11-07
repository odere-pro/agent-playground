---
title: "CH-13: OWASP agentic threat list (ASI01 to ASI10) mapped to the controls"
labels: ["story", "priority:P1", "phase:9-governance", "area:governance", "size:S", "layer:platform"]
milestone: "W11 Compliance evidence"
index: 132
epic_id: CH-13
depends_on: ["026 CH-4", "022 H-6"]
blocks: []
epic_refs: [E.5, R12]
---

## Why

The OWASP agentic threat list (ASI01 to ASI10) and the vault's threat model are not mapped to the controls the chassis and the chart enforce (the [product plan](../../plans/2026-11-06-agent-container-product.md), gap P2, phase E). Without the map, no one can tell which threat has a control and a test, and which is a gap.

## What

- Map the OWASP agentic threat list (ASI01 to ASI10) and the vault's threat model to the controls the chassis and the chart enforce, in `docs/specs/security.md`, each with its test or note.
- Tool-description poisoning maps to Snyk Agent Scan in CI (`deploy/agent-scan/`; no real scan has run) and to 081 R-8 (`later`).
- Read `~/Git/sdlc-vault` first and name the note used (suggested: start at `wiki/security/Threat model.md`).

## Out of scope

- The AI Act and ISO 42001 table (124 C-9).

## Acceptance criteria

- [ ] A table lists ASI01 to ASI10, each with its control and its evidence (a test id or a dated note), or "gap" with the issue that owns it.
- [ ] The tool-description poisoning row names Snyk Agent Scan and 081 R-8.
- [ ] Every gap has an open box in an existing issue or a new issue.
- [ ] `platform-security` reviews the table, and the review is recorded.

## Dependencies

- Depends on: [026 CH-4](026-CH-4-chassis-only-credentials-egress.md), [022 H-6](022-H-6-security-middleware.md)
- Blocks: none

## References

- Source: [ADR-001](../adr/001-chassis-delivery-model.md), not an epic story. Epic phase: [Phase 9](../slm-agent-platform-epic-v3.md#phase-9-governance-and-compliance)
- Epic context: [E.5](../slm-agent-platform-epic-v3.md#e5) · [R12](../slm-agent-platform-epic-v3.md#r12)
- Backlog plan: [000-plan.md](000-plan.md)
