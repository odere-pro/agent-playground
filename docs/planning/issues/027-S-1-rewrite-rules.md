---
title: "S-1: Rewrite rules and banned-word list"
labels: ["story", "priority:P0", "phase:2-simplifier-slm", "area:slm", "size:S", "layer:platform"]
milestone: "W3 Simplifier SLM"
index: 27
epic_id: S-1
depends_on: ["001 DEC-1"]
blocks: ["028 S-3", "029 S-2"]
epic_refs: [C.1, F.9]
---

## Why

The rewrite rules define a good simplification, so the teacher (029 S-2), the metrics (028 S-3), and the reviewers (030 S-4) all use the same standard. The issue opens the SLM wave, and this data work can start during W2, in parallel with the harness. It builds on the first banned-word list from 001 DEC-1.

## What

- Written rewrite rules for the simplifier, based on its purpose: rewrite text in plain language and keep all facts (numbers, names, dates, and conditions).
- Output style rules, agreed with the owner (for example sentence length and tone).
- The banned-word list, starting from the DEC-1 first version, as a machine-readable file where each entry has the word, a reason, and a plain alternative (suggested: YAML).
- Both files versioned in the config store, so data, prompts, and models can record which version they used. Long-term memory holds the same settings later (F.9).
- A system prompt for the simplifier built from the rules and stored in the config store (for example `s3://agent-config/prompts/simplifier/v1.md`).
- A good and a bad example for each rule.
- A named owner who approves rule changes, and a changelog.

## Out of scope

- Scoring outputs against the rules (028 S-3).
- Generating training data (029 S-2).
- Loading settings from long-term memory at run time (113 O-9).

## Acceptance criteria

- [ ] The rewrite rules are written, approved by the owner, and versioned.
- [ ] The banned-word list is a machine-readable file in the config store, with a version, and every entry has a plain alternative.
- [ ] The simplifier system prompt is in the config store with a version, and names the rules version it was built from.
- [ ] Every rule has at least one good and one bad example.
- [ ] A dry run of the prompt on a small set of real outputs (suggested: 20) through `big-default` is read by a reviewer, and the rules are adjusted from the findings.
- [ ] Every change to the rules or the list bumps the version and adds a changelog line.

## Dependencies

- Depends on: [001 DEC-1](001-DEC-1-resolve-open-decisions.md)
- Blocks: [028 S-3](028-S-3-simplifier-metrics.md), [029 S-2](029-S-2-training-data-generation.md)

## References

- Epic story: [S-1 in Phase 2](../slm-agent-platform-epic-v3.md#phase-2-simplifier-slm-and-model-lifecycle)
- Epic context: [C.1](../slm-agent-platform-epic-v3.md#c1) · [F.9](../slm-agent-platform-epic-v3.md#f9)
- Backlog plan: [000-plan.md](000-plan.md)
