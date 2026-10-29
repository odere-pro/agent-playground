# Core review of PoC-0 to PoC-7

Status: in progress
Date: 2026-10-29

## Context

PoC-0 to PoC-7 built one chassis, one PoC at a time. Each PoC added a layer and left a folder of tests, harness code, deploy files, and docs. The code is now large: about 18k lines of chassis source, 24k lines of chassis tests, 31k lines in the PoC folders, and 30k lines of Markdown. Some harness code lives in PoC test folders and is shared through 20 `sys.path` inserts. Some tests are long and hard to read as specs. Some docs are long.

The goal is a codebase a new engineer can read in story order and trust:

- **Simple.** Small functions and files, one way to do each thing, no dead code.
- **Secure.** ADR-001 hard requirements hold, no secrets, inputs checked at the boundary, pinned images.
- **Maintainable.** Shared code in `packages/`, not in PoC folders. Clear names. Import rules hold.
- **Docs in simple English.** Short sentences, one idea per sentence, US spelling.
- **Tests read like specs.** The name states the behavior. The body is arrange, act, assert.

The PoCs tell one story. The review follows that story and makes it visible in the docs.

Decisions from the user:

- PoC-0 is the scaffold and the harness: `Makefile`, `pyproject.toml`, `.github/workflows/`, `scripts/`, `.claude/`, the root `CLAUDE.md`, and the gates.
- Fix sessions simplify and keep behavior. No change to the wire contract, the event schema, or the meaning of an exit criterion. A finding that needs a behavior change becomes a proposal, not a fix.
- Each fix session opens one small PR against `main`.

## How the work is split

This session is the coordinator. It never reads the code areas itself. It starts sessions, reads only their findings files, and runs the gates between waves. Every session keeps its own context small by sending file sweeps to subagents and keeping only their conclusions.

```
Wave 0  coordinator: rubric, story file, baseline gate          (this session)
Wave 1  R0..R7: review, read-only, one findings file each       (8 sessions, 2 batches of 4)
Wave 2  R8: story review and fix plan                           (1 session)
        -- user reads findings and proposals, merges findings PR --
Wave 3  F*: fix sessions, one PR each, ordered by file ownership (worktree sessions)
Wave 4  V: verify on main, story pass on docs, close the plan   (1 session)
```

## Wave 0: set up (coordinator)

1. Copy this plan to `docs/plans/2026-10-29-core-review.md` with `Status: in progress`.
2. Create `docs/reviews/2026-10-29-core-review/` with:
   - `rubric.md`: the checks below, as a checklist.
   - `story.md`: one paragraph per PoC, filled by the review sessions (template only at first).
   - `findings-template.md`: the finding format below.
   - `proposals.md`: empty; behavior changes go here.
3. Run the baseline and save the tail to `baseline.md`: `make check`, `make lint-extra`, `make ts-check`, and `make test-poc POC=NN` for 01 to 07 (06 covers 6a, 6b, 6c by glob). Any red gate is finding zero.
4. Commit on this branch. Do not push until Wave 2 is done.

## The rubric

**Simple**
- Function under 50 lines. File under 800 lines (today `test_model_proxy_messages.py` is 1,195). Nesting at most 4 levels.
- No dead code, no unused option, no second helper that does what one in `packages/` already does.
- No abstraction with one caller and no planned second.

**Secure** (lens agents: `platform-security` for `deploy/`, CI, images, egress; `security-reviewer` for server input handling)
- ADR-001 hard requirement 1: credentials only in the chassis; default-deny egress; no service account token in the workload.
- Hard requirement 2: every lane tested on every commit.
- Inputs validated at each inbound adapter. Errors do not leak internals.
- Images and actions pinned by hash. No secret in any file, prompt, log, or cassette.

**Maintainable**
- Code that outlives a PoC is in `packages/`. No `sys.path` insert in `pocs/*/tests`.
- Ports have fakes, and both pass one contract suite. Import rules hold (`make lint`).
- Names say what a thing is in domain words. No PoC number in a package name or a marker.
- Duplicated config across `deploy/` is listed, not silently copied.

**Docs in simple English**
- The first three sentences of a README say what it is, why it exists, and how to run it.
- Sentences under about 20 words (suggested:). One idea per sentence. US spelling. No marketing words.
- Every PoC README has a `Builds on:` line and a `Hands to:` line, so the story reads in order.
- A doc says a thing once. Others link to it.

**Tests read like specs**
- Name: `test_<subject>_<does what>_when_<condition>`, readable as a sentence. `when_` is optional when there is no condition.
- Docstring: one line in plain words. A PoC scenario test names its exit criterion word for word (rule from `pocs/CLAUDE.md`).
- Body: arrange, act, assert, split by blank lines. One behavior per test. `parametrize` instead of loops. No `if` or `for` around asserts.
- Helpers live in a harness and have domain names.
- A renamed test updates every README line that cites it as evidence.

## Finding format

One entry per finding, in the area's findings file:

```
### R3-07 [HIGH] [secure] packages/chassis/src/chassis/adapters/openai_compat/inbound.py:120
Problem: one sentence.
Why it matters: one sentence.
Fix: one sentence. Keeps behavior: yes | no (then it is a proposal).
Spec: "given ..., when ..., then ..." — the test that proves the fix.
Status: open | fixed (PR) | deferred (reason) | proposal
```

Severity uses the house scale: CRITICAL, HIGH, MEDIUM, LOW. At most 30 findings per file. An area with more splits into two files.

## Wave 1: review sessions R0 to R7

Each session is read-only on code. It writes one file, `docs/reviews/2026-10-29-core-review/findings-RN-<area>.md`, and appends its PoC paragraph to `story.md` (only its own heading). It runs in this checkout (`use_worktree: false`), commits nothing, and switches no branch. Files are disjoint, so the sessions run in parallel. Run R0 to R3 first, then R4 to R7.

| ID | Area | Code and docs | PoC folder |
| -- | ---- | ------------- | ---------- |
| R0 | Scaffold and harness (PoC-0) | `Makefile`, `pyproject.toml`, `.github/workflows/`, `scripts/`, `.claude/`, root `CLAUDE.md`, `docs/guides/local-dev.md`, `docs/guides/testing.md`, open items of `docs/plans/2026-10-09-parallel-poc-work.md` | none |
| R1 | Walking skeleton | `chassis/core`, `ports`, `fakes`, `profiles.py`, `schemas/`, `contract-suites`, `fake-model-server`, `docs/contracts/contract-v0.md` | `poc-01` |
| R2 | Two engines, one contract | `chassis/server` (pipeline, connectors, model proxy), `workload-a2a`, `workloads/echo-*` | `poc-02` |
| R3 | One interface, every client | `chassis/adapters` (OpenAI, Anthropic, MCP, A2A inbound), `server/interfaces` | `poc-03` |
| R4 | Stateless and scalable | `server/idempotency.py`, `server/config.py`, shutdown, `deploy/compose` scale files, `deploy/kind` PoC-4 | `poc-04` |
| R5 | Sandboxed | `code-runner`, `fake-mcp-server`, `deploy/kind/poc05`, NetworkPolicy, admission | `poc-05` |
| R6 | Framework bake-off | `bakeoff`, the bake-off workloads, `deploy/kind/poc06`, `deploy/compose/poc06`, `scripts/poc06_mac.sh` | `poc-06a`, `06b`, `06c` |
| R7 | Cross-cutting decisions | `guardrail-server`, `fake-langfuse`, `evals/`, `deploy/compose/poc07`, `deploy/agent-scan` | `poc-07` |

Rules inside a review session, to keep its context small:

1. Read the brief, `rubric.md`, the PoC planning doc's "Question" and "Exit criteria" sections, and `story.md`. Nothing else whole.
2. Send an `Explore` subagent to map the area: files with line counts, entry points, who imports what.
3. Send lens subagents in parallel, one per lens, each with the map, the rubric section, and a cap of 15 findings: `python-reviewer` (simple and maintainable), the security agent named in the rubric, `pr-test-analyzer` (tests as specs), and a `general-purpose` agent for docs. Each returns findings in the template. None edits files.
4. Merge, dedupe, and rank. Drop any finding without a file and line. Write the findings file.
5. Never open a file over 400 lines in the session's own thread. A subagent reads it.
6. Mark findings that touch shared root files (`Makefile`, `pyproject.toml`, `uv.lock`, CI, root `CLAUDE.md`) with `[shared]`.
7. PoC-6 and PoC-7 are still open. Do not touch their checkboxes or evidence. A finding about an open criterion goes to that PoC's plan, not to a fix session.

## Wave 2: story review R8

One session. It reads only `story.md`, the eight findings files, `pocs/README.md`, `docs/planning/poc/000-plan.md`, and the PoC READMEs' first screens. It produces `fix-plan.md`:

- Contradictions between findings, and duplicates across areas (for example one helper flagged in R2 and R4), merged into one finding.
- Story gaps: a PoC that does not say what it builds on, a contract version step (`contract-v0` to `v6`) with no PoC that explains it, harness code that one PoC copies from another.
- Behavior changes moved to `proposals.md`.
- The fix sessions, each with a finding list and the files it owns. **No two sessions in one wave own the same file.** Shared root files belong to F0 only.
- The waves and their order.

Then the coordinator commits the findings, opens one PR ("docs: core review findings"), and stops. The user reads the findings and the proposals and merges the PR. Fix sessions start from `main` after that.

## Wave 3: fix sessions

Expected shape (R8 sets the final list):

- **F0 scaffold and harness, first and alone on shared files.** Promote the PoC harness to a package, remove the `sys.path` inserts and PoC-named markers (items WS1.3 and WS1.4 of the parallel-poc-work plan, if R0 confirms they are still open). Fix R0 findings. Every other session waits for F0 when it needs a harness import.
- **F1 to F3, chassis by subfolder**, in parallel with F0 where files are disjoint: core and ports; server; adapters.
- **F4 to F7, per PoC folder and its packages and deploy files**, after F0 lands: test renames to the spec style, test splits, README rewrites with `Builds on:` and `Hands to:`.

Rules inside a fix session:

1. Own worktree (`use_worktree: true`), branch `review/<area>`, base `main`.
2. One finding cluster per `developer` subagent call. A test-only change goes to `tester`. The session's own thread runs gates and keeps the findings file current.
3. Behavior stays the same. A changed test name or docstring is fine. A changed assertion needs a reason in the PR, or it is a proposal.
4. Gate per cluster: `make quick`. Before the PR: `make check`, and `make test-poc POC=NN` for the PoC it touches and every later PoC, because later PoCs build on earlier ones.
5. `reviewer` agent on the diff. `platform-security` too when the diff touches `deploy/`, CI, or credentials. Fix every CRITICAL and HIGH.
6. Mark each finding `fixed (PR link)` or `deferred (reason)`. Open the PR with the `git-flow` skill. Commit subject at most 72 characters, no attribution.
7. If the session's context gets long, it commits the work so far and hands off the remaining finding IDs.

## Wave 4: verify and close

One session on `main` after the fix PRs merge:

- Full baseline again: `make check`, `make lint-extra`, `make ts-check`, `make test-poc POC=01` to `07`. Compare with `baseline.md`.
- `grep -rn "sys.path" pocs/*/tests` returns nothing (if F0 did the promotion).
- Every finding is `fixed`, `deferred`, or `proposal`. No `open`.
- A `docs-editor` pass reads `pocs/README.md` and the PoC READMEs in order and checks the story reads end to end.
- `docs/plans/2026-10-29-core-review.md` moves to `Status: done`.

## Critical files

- Rules the rubric builds on: root `CLAUDE.md`, `pocs/CLAUDE.md`, `packages/chassis/CLAUDE.md`, `docs/planning/adr/001-chassis-delivery-model.md`.
- The open overlap: `docs/plans/2026-10-09-parallel-poc-work.md` (harness promotion not done; 20 `sys.path` inserts remain).
- Existing agents reused: `reviewer`, `developer`, `tester`, `platform-security`, `docs-editor`, `chassis-architect` (consulted when a finding nears a contract). Skills: `git-flow`, `poc-iteration`.
- Gates reused: `make quick`, `make check`, `make test-poc`, `make harness-lint`, `make lint-extra`, `make ts-check`.

## Verification

- Each wave ends with a gate the coordinator runs and pastes: Wave 0 baseline, Wave 3 per-PR `make check`, Wave 4 the full baseline compared with Wave 0.
- No test count drops between baseline and Wave 4 unless a finding removed a duplicate, and the findings file says which.
- `make harness-lint` stays green, so plan status lines and README wiring stay valid.
