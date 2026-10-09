---
name: demo-record
description: Write and run a PoC demo as one script that produces a dated record in demo/. Use when a PoC's planning doc has a "Demo" section to show, or when closing a PoC.
---
# Record a PoC demo

One shape for every PoC from PoC-5 on. Model: `pocs/poc-04-stateless-scalable/demo/demo.sh`.

## Files

- `pocs/poc-NN-*/demo/demo.sh`: the script. It is the only demo entry point.
- `pocs/poc-NN-*/demo/YYYY-MM-DD-demo-<topic>.md`: the record the script writes. It is committed.
- No Python demo client, unless the demo needs one. If it does, `demo.sh` calls it.

## The script

1. Header comment:
   - the planning doc section it follows (`docs/planning/poc/00N-*.md`, "Demo");
   - the numbered steps;
   - what it needs first (images, a running cluster);
   - its usage line.
2. `set -euo pipefail`. Find the repo root from the script path, never from the current directory.
3. A `trap cleanup EXIT` that tears down what the script started and nothing else.
4. A `run` helper that prints `$ <command>`, runs it, and keeps its output in the record. Keep a `step` helper that prints a `##` heading for each step.
5. Exit non-zero on the first failed step. A demo that "mostly worked" is not a record.
6. Use existing entry points (`make kind-poc0N`, `deploy/kind/poc0N/run.sh`, `deploy/compose/*.sh`). Do not copy their logic.
7. Pass `--context kind-pocNN` to every `kubectl`.

## The record

- First line: what the demo shows and where it ran (lane, cluster or Compose project).
- The commit and the date at the top.
- Each step's command and output, in order.
- A last section, "What this shows", with one line per exit criterion it backs.
- No secret: redact keys, tokens, key suffixes, and hashes.

## Rules

- A demo that uses Docker or kind runs in one worktree at a time (skill `git-flow`, "Parallel work in worktrees").
- Run `shellcheck -x` through `make lint-extra` before you commit the script.
- Link the record from the PoC README in the same commit as the box it ticks.
