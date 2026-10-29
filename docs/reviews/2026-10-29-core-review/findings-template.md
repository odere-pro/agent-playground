# Findings RN: <area>

Session: R<N>. Area: <paths>. PoC: <folder or none>.
Rubric: [rubric.md](rubric.md).

## Summary

Two or three sentences: the state of the area, and the top risk.

## Findings

Most severe first. At most 30. Severity: CRITICAL, HIGH, MEDIUM, LOW.
Lens: simple, secure, maintainable, docs, tests. Add `[shared]` when the fix touches
`Makefile`, `pyproject.toml`, `uv.lock`, CI, or the root `CLAUDE.md`.

### RN-01 [HIGH] [secure] path/to/file.py:120
Problem: one sentence.
Why it matters: one sentence.
Fix: one sentence. Keeps behavior: yes | no (then it is a proposal).
Spec: "given ..., when ..., then ..." (the test that proves the fix).
Status: open | fixed (PR) | deferred (reason) | proposal
