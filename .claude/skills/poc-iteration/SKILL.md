---
name: poc-iteration
description: Open, run, and close one PoC iteration in pocs/. Use when starting a PoC, adding scenario tests for its exit criteria, recording measurements, or closing it with evidence.
---
# PoC iteration

The planning doc is the source; the PoC folder tracks execution.

## Open
1. Read `docs/planning/poc/00N-*.md` and `pocs/CURRENT`. Set `CURRENT` to the folder you open.
2. Fill `pocs/poc-NN-*/README.md` from `docs/templates/poc-readme.md`: question, scope checklist, exit criteria checklist, how to run, status `in progress`.
3. Write `CLAUDE.md` from `docs/templates/poc-claude.md`: what to read, the packages this iteration touches, what "done" means, what not to touch.
4. One scenario test per exit criterion in `tests/`, docstring naming the criterion. A criterion that cannot be tested yet is `xfail(strict=True)` with the reason.

## Run
- Work in small steps: `make quick` after each; `make test-poc POC=NN` before ticking a box.
- Measurements (latency, tokens, memory) go to `notes/` as dated markdown with the command that produced them.
- A decision that outlives the iteration becomes an ADR (skill `adr`), never a note only.

## Close
1. Every exit criterion has evidence: the test name or the command output, linked from the README.
2. The demo script and its output are in `demo/`.
3. `notes/backlog-changes.md` lists what the backlog issues should change, with `NNN ID` references. Apply them with skill `planning-sync`.
4. README status `done`; `pocs/CURRENT` moves to the next folder; `make check` green.
