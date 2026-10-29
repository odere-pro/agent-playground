---
title: "PoC-6: Framework bake-off, in two parts: which agent SDKs and frameworks to support"
labels: ["poc", "priority:P0", "area:harness", "type:decision"]
milestone: "Agent MVP"
index: 6
iteration: PoC-6
timebox: "2 weeks: part A 1 week after PoC-3, part B 1 week after PoC-5"
depends_on: ["PoC-3", "PoC-5"]
backlog_refs: ["008 H-14", "054 H-16", "063 S-8", "058 CH-8", "055 CH-6"]
---

## Question

Which agent SDKs and frameworks should the platform support, and which one is the default for new agents? Does the chassis front an agent in another language and a remote solution with no change to its core?

## Status

Part A ([6a README](../../../pocs/poc-06a-bake-off-sidecar-lane/README.md)) and part B ([6b README](../../../pocs/poc-06b-bake-off-remote-lane/README.md)) are in progress. Everything that runs without a provider key is done: the contract, load, and hostile suites in each lane, the scorecard, and the ADR draft.

## What is left

- **Run every engine on a hosted big model (6a and 6b exit criterion 5).** This gives the real token overhead against plain Python per engine. The provider key stays on the user's Mac, so only the user can run it: `make poc06-mac ARGS="--only hosted --only kind"`. Then record the numbers in the scorecard and check the default in ADR-006.
- **Accept ADR-006 (6a and 6b exit criterion 7).** The ADR is written and its status is Proposed. The user accepts it, or asks for changes. The hosted numbers can still reopen the default.
- **Follow-ups found on the way, in [the 6a backlog changes](../../../pocs/poc-06a-bake-off-sidecar-lane/notes/backlog-changes.md):** find the cause of the rare 502 under load at 4 pairs, and add a test that every workload image accepts the scale stack's command.

## Where the rest is

- The evidence for every exit criterion is in [the PoC README](../../../pocs/poc-06a-bake-off-sidecar-lane/README.md).
- The full plan as it was (scope, demo, exit criteria) is in git: `git show 23ffbdc:docs/planning/poc/006-PoC-6-framework-bake-off.md`.
