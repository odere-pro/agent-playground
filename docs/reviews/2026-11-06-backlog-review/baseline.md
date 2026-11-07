# Baseline

The gates and the backlog numbers before the review, so the close can compare.

- Date: 2026-11-06.
- Commit: `b1cd681` on `main` (PR #67 merged), with only the review docs added.
- Machine: the user's Mac. Other sessions were running at the same time.
- Setup: `uv sync --all-packages`, then `UV_NO_SYNC=1 make check`.

## Gates

| Gate | Result |
| ---- | ------ |
| `make check` | green |
| `make planning-sync planning-check` | `no changes`; `127 issues, 113 epic stories`, `OK` |
| `make harness-lint` | `harness-lint: ok` |

```
uv run mypy               → Success: no issues found in 645 source files
make test                 → 6535 passed, 455 skipped, 25 xfailed, 35 warnings in 671.19s (0:11:11)
python3 tools/check.py    → 127 issues, 113 epic stories / OK
harness_lint.py           → harness-lint: ok
```

## Backlog numbers

From `docs/planning/tools/backlog.py` `ROWS`, with `DAYS = {S: 1, M: 2.5, L: 5}` from `sync.py`. A read-only snippet over `backlog.build()` and the issue files produced them.

| Wave | Issues | Days |
| ---- | ------ | ---- |
| W0 Decisions | 1 | 1 |
| W1 Router and baseline | 5 | 11 |
| W2 Chassis MVP | 20 | 56.5 |
| W3 Simplifier SLM | 13 | 30 |
| W4 Simplifier agent live | 10 | 17.5 |
| W5 Chassis completion | 14 | 29.5 |
| W6 Golden set and evaluator SLM | 12 | 26 |
| W7 Registry and governance gate | 18 | 36.5 |
| W8 Platform MCP server | 4 | 7 |
| W9 Orchestrator | 18 | 46.5 |
| W10 Production readiness | 7 | 18.5 |
| W11 Compliance evidence | 2 | 3.5 |
| W12 Later | 3 | 15 |
| **W0–W11** | 124 | **283.5** |
| All | 127 | 298.5 |

- Acceptance boxes: 869 in all, 19 ticked, 850 open.
- Issues with a ticked box: 7. They are 002 (1 of 7), 008 (2 of 9), 009 (5 of 8), 010 (4 of 11), 012 (2 of 10), 013 (4 of 8), and 054 (1 of 11).
- Issues with a "Status after PoC-N" bullet: 33.
- `suggested:` markers in issue files: 137.
- Critical path to first savings (A-3): 31 days. The longest chain ends at 112 O-16.

## Known before the review

These go to the consolidation session with the findings.

- **B-1, conflict.** `packages/chassis/src/chassis/server/evaluator_gate.py` fails open when the evaluator is down; 042 A-2 wants fail-closed. Goes to `proposals.md`.
- **B-2, tool.** `docs/planning/tools/check.py` counts only open boxes (`ac.count("- [ ]") < 3`), so an issue with every box ticked fails today. PR-2 fixes the count before any issue can retire.
- **B-3, inputs never applied.** The PoC-7 backlog changes are still a draft, and no issue has a "Status after PoC-7" bullet. The PoC-6c "to apply" list has no "Status after PoC-6c" bullet in any issue.
- **B-4, the product plan.** `docs/plans/2026-11-06-agent-container-product.md` is not on `main` yet. Review sessions start after it merges.
