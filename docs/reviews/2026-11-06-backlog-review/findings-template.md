# Findings <group>: <wave names>

Session: <group>. Issues: NNN–NNN. Waves: Wn.
Rubric: [rubric.md](rubric.md).

## Summary

Two or three sentences: what the PoC track delivered for these waves, what is left, which product phases these issues feed, and the top risk.

## Issues

One row per issue, in index order. No cap. `Boxes` is met over total after the review's ticks. `Phase` is A to F from the product plan, `later`, `parked`, or `done`.

| Issue | Boxes | Phase | Verdicts | Evidence or reason |
| ----- | ----- | ----- | -------- | ------------------ |
| 007 H-1 | 8/10 | A | tick, resize M | `packages/chassis/tests/test_x.py::test_y`; 2 open boxes: ... |

## Ticks

One line per box this session ticks, ready to paste into the issue.

- `NNN ID`, box "<first words of the criterion>": `· Delivered in PoC-N: <evidence>`

## Status bullets folded

- `NNN ID`, "Status after PoC-N": tick | open box | out of scope | DEC-1 | owner list. One line on where it goes.

## Findings

Most severe first. At most 30. Severity: HIGH, MEDIUM, LOW.
Rule: the rubric check number, 1 to 14.

### <group>-01 [HIGH] [rule 4] docs/planning/issues/NNN-ID-slug.md
Problem: one sentence.
Why it matters: one sentence.
Fix: one sentence: the change to the issue body or to `ROWS`.
Status: open | applied (PR) | deferred (reason) | proposal
