---
title: "PoC-6c: Agent pods on a pre-trained SLM"
labels: ["poc", "priority:P0", "area:slm", "area:router", "type:decision"]
milestone: "Agent MVP"
index: 6
iteration: PoC-6c
timebox: "suggested: 1 week, after PoC-6a"
depends_on: ["PoC-6a"]
backlog_refs: ["003 G-1b", "036 S-7", "063 S-8", "028 S-3"]
---

## Question

Can the agent pods run their tasks on a pre-trained SLM, served once behind LiteLLM, with no change to the workload or the chassis? Which engines still do the work on it, and at what cost in quality, tokens, and latency next to the big model?

## Status

In progress. The SLM run is done in CI, on CPU: every engine did both tasks on Qwen3-1.7B (135 of 135 runs). ADR-006 has its SLM section.

## What is left

- **Put the SLM numbers next to the big-model numbers (exit criterion 2).** The SLM half is recorded. The big-model half comes from the hosted run on the user's Mac (see [PoC-6](006-PoC-6-framework-bake-off.md)).
- **Optional: latency on the Mac.** The CI numbers are CPU numbers. A run on the Mac with Metal gives real latency and time to first token: `make poc06-mac ARGS="--only slm"`.

## Where the rest is

- The evidence for every exit criterion is in [the PoC README](../../../pocs/poc-06c-pretrained-slm/README.md).
- The full plan as it was (scope, demo, exit criteria) is in git: `git show 23ffbdc:docs/planning/poc/006c-PoC-6c-pretrained-slm.md`.
