---
title: "R-8: Description scanning for hidden instructions on registration"
labels: ["story", "priority:P1", "phase:5-registry", "area:registry", "size:M", "layer:platform"]
milestone: "W7 Registry and governance gate"
index: 81
epic_id: R-8
depends_on: ["080 R-7"]
blocks: ["087 R-4", "088 R-5"]
epic_refs: [G.3, R5]
---

## Why

A tool description is read by a model, so an attacker can hide instructions in it (MCP tool poisoning [R5]). The platform standard says tool descriptions are data, never instructions (G.3). This issue scans every description when it is registered, so a poisoned entry goes to review instead of going live. The MCP and OpenAPI imports (087 R-4, 088 R-5) depend on it.

## What

- A scanner that runs on every create and every description change, for all entry types.
- It scans the `description`, the schema field names and descriptions, and any tool annotations.
- Rules for known patterns (suggested list): instructions aimed at the model ("ignore previous", "do not tell the user"), references to other tools, requests for files, secrets, or keys, hidden Unicode (zero-width, bidi control, and tag characters), HTML comments, encoded blobs, and very long descriptions.
- Result per entry: `clean` or `flagged`, with a list of findings (rule, field, matched text).
- A flagged entry goes to the approval queue (080 R-7) with its findings, even if it is `internal-signed`.
- Suggested: an optional model check through the evaluator port, off by default, that gets the description as quoted data, never as instructions.
- A fixture set of poisoned and clean descriptions, based on the OWASP examples [R5], versioned next to the rules.

## Reuse

- **Use:** Snyk Agent Scan in CI (tool poisoning, shadowing, toxic flows), and Llama Prompt Guard 2 as an in-house classifier on descriptions.
- **Build:** the scan at registration and the review flag.
- **Watch:** Snyk Agent Scan sends component data to Snyk and runs the servers it scans, so run it in a sandbox and keep the in-house check as the gate.
- Details: [reuse analysis](../poc/010-reuse-analysis.md)

## Out of scope

- Catching a description that changes after approval (082 R-9).
- Reading tools from MCP servers and OpenAPI specs (087 R-4, 088 R-5).
- The human review of flagged entries, which uses the approval queue (080 R-7).

## Acceptance criteria

- [ ] Every poisoned fixture (at least 30) is flagged, with the right rule and field in the findings.
- [ ] The false-positive rate on at least 100 clean descriptions from real tools is measured and written in this issue.
- [ ] A flagged `internal-signed` entry stays `pending` and shows its findings in the approval queue.
- [ ] Hidden Unicode in a schema field description is flagged.
- [ ] The scan runs again when a description changes, and the result is stored with the entry.
- [ ] Scan time per entry without the model check is measured and stays under 50 ms (suggested target).

## Dependencies

- Depends on: [080 R-7](080-R-7-trust-levels.md)
- Blocks: [087 R-4](087-R-4-mcp-import.md), [088 R-5](088-R-5-openapi-import.md)

## References

- Epic story: [R-8 in Phase 5](../slm-agent-platform-epic-v3.md#phase-5-service-registry)
- Epic context: [G.3](../slm-agent-platform-epic-v3.md#g3) · [R5](../slm-agent-platform-epic-v3.md#r5)
- Backlog plan: [000-plan.md](000-plan.md)
