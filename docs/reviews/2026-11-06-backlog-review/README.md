# Backlog review of 2026-11-06

The working folder of the backlog review. It checks every issue in [docs/planning/issues/](../../planning/issues/000-plan.md) against what the PoC track built, and maps it to the product plan, `docs/plans/2026-11-06-agent-container-product.md`. The product plan leads; this review serves it. Plan: [2026-11-06-backlog-review.md](../../plans/2026-11-06-backlog-review.md).

- [rubric.md](rubric.md): what every review session checks, per issue.
- [findings-template.md](findings-template.md): the issue table and the format of one finding.
- [baseline.md](baseline.md): the numbers and gates before the review starts.
- `findings-<group>.md`: one file per review session (table below).
- `story.md`: one paragraph per wave, written by the consolidation session.
- `proposals.md`: findings that need a code behavior change. The user decides.
- `fix-plan.md`: the retire list, resizes, dep changes, body edits, and the PR order.
- `phase-map.md`: for each product phase, A to F, the issues it closes, their open boxes, and their days.
- `decision-pack-DEC-1.md`: each open decision in 001 DEC-1 that a phase needs, with options, PoC evidence, and a suggested default.
- `verification.md`: the checks that prove the review is complete.

## Sessions

Review sessions read the product plan pinned at `70244ad` (the user chose on 2026-11-06 to start before its PR merges). They are read-only. Each writes only its own findings file. A batch runs in parallel.

| Session | Issues | Findings file | Batch |
| ------- | ------ | ------------- | ----- |
| W01 | 001–006 | `findings-W01-decisions-router.md` | 1 |
| W2a | 007–016 | `findings-W2a-chassis-core.md` | 1 |
| W2b | 017–026 | `findings-W2b-chassis-security-deploy.md` | 1 |
| W3 | 027–039 | `findings-W3-simplifier-slm.md` | 2 |
| W4 | 040–049 | `findings-W4-agent-live.md` | 2 |
| W5 | 050–063 | `findings-W5-chassis-completion.md` | 2 |
| W6 | 064–075 | `findings-W6-golden-evaluator.md` | 3 |
| W7 | 076–093 | `findings-W7-registry.md` | 3 |
| W89 | 094–115 | `findings-W89-mcp-orchestrator.md` | 3 |
| W1012 | 116–127 | `findings-W1012-production-later.md` | 3 |
| C1 | all | `story.md`, `fix-plan.md`, `proposals.md` | 4 |
| C2 | all findings | `phase-map.md`, `decision-pack-DEC-1.md` | 4 |

## Rules for a review session

- Read only: the brief, [rubric.md](rubric.md), [findings-template.md](findings-template.md), and the session's issue files. Find evidence through `Explore` subagents, one per 3 or 4 issues. They return table rows, not file dumps.
- Never open a file over 400 lines in the main thread.
- Write one file: the session's findings file. Edit nothing else. Run nothing that writes: no `sync.py`, no `make lint-fix`.
- Commit nothing. The coordinator commits the findings in one PR.
