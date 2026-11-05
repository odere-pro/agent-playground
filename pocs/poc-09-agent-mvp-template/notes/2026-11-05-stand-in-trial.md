# A stand-in trial for criterion 6

Date: 2026-11-05. **This does not meet criterion 6.** The criterion asks for a person new to the code. This was a fresh AI agent given only the platform's docs, the `glossary-agent` task below, and no context from the PoC-9 work. It shows where a person would get stuck, so the docs can be fixed before the real trial.

## The task

Scaffold `glossary-agent` (kind `transformer`, the default framework), make `handle` expand the words of a small glossary, add tests and an eval case, run `make lint test load-smoke hostile redteam eval`, break the logic and see `make eval` fail, then `make image`.

## Result

About 7 min 44 s in all, with a warm uv cache and Docker running. Every step passed; `make lint` needed one fix in its own code (a long line).

| Step | Time | Last line |
| ---- | ---- | --------- |
| Scaffold, `make setup test` | 78 s | `28 passed in 34.25s` |
| Logic, tests, eval case | 80 s | |
| The six gates | 245 s | `1 passed in 23.40s` (eval) |
| Break, then restore | 39 s | `FAILED ...test_every_case_passes`, then `1 passed` |
| `make image` | 17 s | runs as uid 10002 |

It said it would finish in under a day, but only because it read the test kit's source.

## What it found, and what changed

1. **`agentctl` did not start as the docs said.** Fixed: the guide and the agentctl README say to run the platform's `make setup` first.
2. **The template's eval case could not fail on wrong logic.** The fake model answers `ok` to every call, and the case checked for `ok`. Fixed in the docs and the case's comment: it checks the plumbing only, and the agent's own cases must check what its logic adds. Not fixed: the fake model cannot be scripted per case. That needs a chassis change, added to the [kit's asks](2026-10-29-testkit-asks.md).
3. **The `evals/cases.yaml` format was only in a docstring.** Fixed: the guide and the file's header describe it.
4. **The guide's test example was wrong** (`await`, `result.text`). Fixed.
5. **No guidance on dropping the model call.** Fixed: the guide says which events `handle` must yield, and to remove the framework's pin.
6. **The guide's file table did not match** (`telemetry.py` only for two frameworks). Fixed.
7. **The Makefile's comment said the repo holds no local path,** while the default `PLATFORM_PATH` is one. Fixed: the comment says the default is the checkout you scaffolded from.
8. **The guide said `make hostile` checks the image.** It reads the files. Fixed.
9. **A `PytestRemovedIn10Warning`** from class-scoped fixtures in `agent_testkit.suites`. Fixed: class methods, as the contract already uses.
10. **No offline guidance.** Fixed: the guide says when `UV_OFFLINE=1` works.
11. Links in the agent's copy of the guide go to GitHub. Kept: an agent repo does not hold the platform's docs.

The handle's docstring cites `contract-v0.md` because it is a byte copy of the echo workload. Kept, so the drift test still holds.

## Next

Criterion 6 still needs a person new to the code, with [the trial steps](2026-10-30-new-user-trial.md).
