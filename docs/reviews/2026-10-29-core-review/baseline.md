# Baseline

The gates before the review, so Wave 4 can compare.

- Date: 2026-10-29.
- Commit: `d11769f` on `claude/poc-core-review-plan-cd1b5e`, with only the review docs added.
- Machine: the user's Mac. Four review sessions were running at the same time, so the machine was under load.
- Setup: `uv sync --all-packages --no-install-package hadolint-py`, then every gate with `UV_NO_SYNC=1`.

## Results

| Gate | Result |
| ---- | ------ |
| `make check` | red: 1 failed, 4,128 passed, 418 skipped, 6 xfailed (see B-1) |
| `make lint-extra` | red at hadolint only (see B-2); shellcheck and actionlint passed |
| `make ts-check` | green |
| `make test-poc POC=01` | 34 passed, 2 skipped |
| `make test-poc POC=02` | 142 passed, 6 skipped |
| `make test-poc POC=03` | 182 passed, 34 skipped |
| `make test-poc POC=04` | 99 passed, 17 skipped, 5 xfailed |
| `make test-poc POC=05` | 529 passed, 155 skipped, 1 xfailed |
| `make test-poc POC=06` | no tests collected (see B-3) |
| PoC-6a tests, run directly | 223 passed, 19 skipped |
| PoC-6b tests, run directly | 236 passed, 86 skipped |
| PoC-6c tests, run directly | 71 passed, 1 skipped |
| `make test-poc POC=07` | 105 passed, 8 skipped |

```
= 1 failed, 4128 passed, 418 skipped, 6 xfailed, 21 warnings in 400.17s (0:06:40) =
FAILED pocs/poc-04-stateless-scalable/tests/test_killed_replica.py::test_a_retry_after_a_dead_owner_runs_once_after_the_lease
E       AssertionError: replica 1 ran before the dead claim could expire: 0.04s
```

## Baseline findings

These go to R8 with the R0 to R7 findings.

### B-1 [MEDIUM] [tests] pocs/poc-04-stateless-scalable/tests/test_killed_replica.py:94
Problem: the test asserts on wall-clock timing and failed once under load.
Why it matters: a flaky test in `make check` teaches people to rerun until green.
Fix: drive the lease with a fake clock, or widen the margin. Keeps behavior: yes.
Spec: "given a dead owner's claim, when a retry arrives, then it runs once, after the lease ends, on any machine load".
Status: open
Evidence: it passed alone afterwards (`3 passed in 9.62s`).
Same pattern: `test_timeout_budget_per_engine.py::test_past_timeout_ms_the_run_ends_with_a2a_timeout_and_the_task_is_cancelled[echo_langgraph]` failed in the pre-commit `make quick` run under load (`1 failed, 4113 passed`) and passed alone (`7 passed, 1 xfailed in 9.63s`).

### B-2 [LOW] [shared] pyproject.toml (dev group: hadolint-py)
Problem: the `hadolint-py` wheel for macOS failed to unpack twice ("deflate decompression error: invalid block type"), even with a fresh uv cache.
Why it matters: `make setup` fails on this Mac, so every `uv run` fails until the user skips the package.
Fix: find out whether the cause is the wheel or the local network, then pin a working version or install hadolint another way. Keeps behavior: yes.
Spec: "given a clean clone on macOS, when `make setup` runs, then it exits 0".
Status: open

### B-3 [MEDIUM] [shared] Makefile:45
Problem: `make test-poc POC=06` uses the glob `pocs/poc-$(POC)-*/tests`, which matches none of `poc-06a`, `poc-06b`, `poc-06c`.
Why it matters: the documented PoC gate collects nothing for PoC-6 and exits 4.
Fix: change the glob to `pocs/poc-$(POC)*/tests`. Keeps behavior: yes.
Spec: "given POC=06, when `make test-poc` runs, then it runs the 6a, 6b, and 6c tests".
Status: open
