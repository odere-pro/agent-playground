# Backlog review 2026-11-06: re-verify the backlog against the agent container product plan

Status: done
Date: 2026-11-06

Done on 2026-11-07. Merged as PRs #67, #69, #74, #77, #82, #88, #93, and the close. The results and what is left for owners are in [verification.md](../reviews/2026-11-06-backlog-review/verification.md).

## Context

The PoC track (PoC-1 to PoC-9) is built. The long-term plan is the backlog in `docs/planning/issues/000-plan.md`: 127 issues, waves W0–W12, 283.5 engineer-days, written on 2026-09-28 before any PoC ran. The PoCs touched 33 issues through "Status after PoC-N" bullets, but only 7 issues have a ticked box, and 8 issues that say "built" have every box open. W2 "Chassis MVP" (20 issues, 56.5 days) is mostly delivered; W5 partly; W1 and W3 barely; W0 `001 DEC-1` (broker, first cloud, public A2A, banned words and targets, router home, owners) is fully open and blocks W1 and W3. The estimate was never re-sized. One code-vs-plan conflict is known: `evaluator_gate.py` fails open, `042 A-2` requires fail-closed.

Outcome: a backlog that shows true remaining work and serves the product plan. Delivered issues are retired into a Done section, SLM-track issues are parked, and every live issue is mapped to a product phase. Numbers are kept. Issues are re-sized where the PoCs changed the picture. A phase map says which issues each phase closes, and a trimmed DEC-1 decision pack covers what the phases need.

User decisions (2026-11-06): all waves deep; tick met criteria with evidence and retire fully met issues; keep issue numbers (no renumber); per-wave read-only review sessions, one findings file each, then apply through `planning-sync`.

**Amended 2026-11-06: the product plan leads.** The same day, the user wrote the product plan `docs/plans/2026-11-06-agent-container-product.md` in the "Universal agent container scaling" session. It parks the SLM track (PoC-6c, 012 H-3, 127 L-3), makes LangGraph the default engine and LangSmith a backend (ADR-006, -007, -009, -010 amended), and sets the build order as phases A to F. The user chose: the product plan leads and this review serves it. So:

- "First build is DEC-1 then W1" is dropped. The first build is the product plan's phase A and phase B.
- Every live issue gets a product phase (A to F) or `later`. SLM-track issues get `parked`.
- The W1 build brief becomes `phase-map.md`: the issues each phase closes, their open boxes, and their days.
- The DEC-1 pack keeps only the decisions a phase needs.
- Review sessions start after the product plan's PR merges. Until then that session owns `tools/plan-template.md`, `issues/000-plan.md`, `poc/000-plan.md`, and the ADRs.

## Pre-steps (before the review starts)

1. **Done: the what-is-left consolidation.** `docs/planning/poc/011-what-is-left.md` merged in PR #67 on 2026-11-06 (`b1cd681`). Two hook runs failed first: smolagents calls `load_dotenv()` at import, which read a new `~/Git/.env` and put three keys back into the offline run. `main` already had the fix (`PYTHON_DOTENV_DISABLED=1` in `scripts/check_offline.sh`).
2. **Not ours: the core-review plan close.** The "PoC 0-7 core review plan" session closes `docs/plans/2026-10-29-core-review.md` in its own PR. This review does not edit it.

## Folder and files

`docs/reviews/2026-11-06-backlog-review/`, same shape as `docs/reviews/2026-10-29-core-review/`:

| File | Content |
| ---- | ------- |
| `README.md` | what the folder is, the session table, how to read |
| `rubric.md` | the per-issue checks (below) |
| `findings-template.md` | copy of the core-review template, plus the mandatory per-issue table |
| `baseline.md` | gates green before the review (`make planning-sync planning-check`, `make check`, `make harness-lint`), per-wave day sums, tick counts |
| `findings-W01-decisions-router.md` (001–006), `findings-W2a-chassis-core.md` (007–016), `findings-W2b-chassis-security-deploy.md` (017–026), `findings-W3-simplifier-slm.md` (027–039), `findings-W4-agent-live.md` (040–049), `findings-W5-chassis-completion.md` (050–063), `findings-W6-golden-evaluator.md` (064–075), `findings-W7-registry.md` (076–093), `findings-W89-mcp-orchestrator.md` (094–115), `findings-W1012-production-later.md` (116–127) | one per session |
| `story.md` | one paragraph per wave, written by the consolidation session (review sessions never share a file) |
| `proposals.md` | behavior changes the review must not make: fail-open gate vs 042 A-2; `inprocess` batches events vs CH-1; `rate_limit_on_unavailable: fail_open` vs H-6 |
| `fix-plan.md` | the retire list, resizes, dep changes, per-wave body edits, the PR sequence |
| `phase-map.md`, `decision-pack-DEC-1.md` | see below |
| `verification.md` | the checks in the last section, with output |

Plus `docs/plans/2026-11-06-backlog-review.md` (`Status: in progress` → `done`), written from this plan.

## Rubric (one row per issue, mandatory; findings only for non-`keep` rows; cap 30 findings per file)

Row: `| NNN ID | boxes met/total | phase | verdicts | evidence or reason |`. Verdicts: `retire`, `tick`, `tick-blocked`, `keep`, `parked`, `resize S|M|L`, `deps`, `drift`, `moot`, `split`, `cite`, `conflict`.

1. **Evidence rule.** A box is ticked only as `- [x] ... · Delivered in PoC-N: <evidence>` where evidence is a test id that runs in `make test`, `make test-poc`, or a named workflow; a dated `pocs/poc-NN-*/notes/` or `demo/` record; or a contract anchor in `docs/contracts/contract-vN.md`. A status bullet, a README sentence, or "built" is not evidence → `tick-blocked`, and the finding names the test to write. Sources: `docs/guides/chassis-reference.md`, PoC READMEs, `docs/contracts/contract-v7.md`, `pocs/*/notes/backlog-changes.md`.
2. **Retire.** Every box tickable → `retire`; add `Delivered by: PoC-N, PoC-M (date)` as the first line under `## Acceptance criteria`.
3. **Size.** Count open boxes after ticks; re-size against S ≤1 day, M 2–3, L 4–5, citing the open boxes.
4. **Deps.** Still needed for the open boxes? Add deps the code revealed (e.g. 006 G-6 on 014 H-7's `/metrics` families). Deps must stay lower-numbered.
5. **ADR drift.** `## What` vs ADR-001…010: Dapr text in H-17/H-20/H-23 (ADR-004), container roles in CH-3 (PoC-5), Collector path in H-7 (ADR-007), guardrails per key in H-6 (ADR-008), retry semantics in H-4 (ADR-010). Fix: one "Decided in ADR-00N" sentence.
6. **`suggested:` values.** Settled by a PoC or ADR → drop the marker and cite. Still open and blocking W1–W4 → into the DEC-1 pack.
7. **Epic citation** still describes the scope after re-scoping (H-11, H-5, CH-*).
8. **Moot by an ADR** (H-11 stock image, the Dapr half of H-23, H-5 if DEC-1 says no). Coverage forbids deleting a story: rewrite criteria to what the ADR made true and retire with the ADR as evidence, or re-scope and keep.
9. **Split** when open work exceeds 5 days or has two owners (`a`/`b` like X-1a/b; extend the `(X-1|P-2)[ab]` regex in `check.py` if used).
10. **Status bullets** are folded: facts into ticks, open items into open boxes or `## Out of scope`, user questions into DEC-1 or the owner list; the bullet is removed. Inputs that were never applied: `pocs/poc-07-cross-cutting-decisions/notes/backlog-changes.md` (draft), the PoC-6c "to apply" list (063 S-8, 036 S-7, 013 CH-2), `pocs/poc-09-agent-mvp-template/notes/2026-10-30-w2-w5-report.md`, `poc/011-what-is-left.md` sections 3 and 5.
11. **Code conflicts** → `conflict`, filed in `proposals.md`; box stays open; no code change in this review.
12. **Order rule**: no criterion depends on a higher-numbered issue.
13. **Parked.** The issue is SLM-track work the product plan parks: training, serving, or routing to an SLM (W3 S-*, M-1, C-8, X-2's GPU half, M-5, S-8, 012 H-3's vLLM half, 127 L-3), or a step whose only purpose is SLM savings. Verdict `parked`. A live issue may not depend on a parked one: drop the dependency or split the issue.
14. **Phase.** Every live issue names one product phase, A to F, from the product plan's "The order of work", or `later` when no phase needs it. A gap under the product plan's "Gaps the PoC track did not cover" with no issue gets a finding: a new issue or a home in an existing one.

Severity: HIGH = wrong size or dep on the critical path, criterion contradicted by code; MEDIUM = drift, stale bullet, size; LOW = citation, wording.

## Sessions

Start condition, amended: the user chose on 2026-11-06 to start before the product plan's PR merges. Every session reads the product plan pinned at `70244ad` (`git show 70244ad:docs/plans/2026-11-06-agent-container-product.md`) and the specs it links, under `docs/specs/` at the same commit. A finding that depends on a phase is re-checked if the plan changes before it merges.

| ID | Issues | Extra inputs | Tier | Batch |
| -- | ------ | ------------ | ---- | ----- |
| S0 coordinator (this session) | — | writes the folder, baseline, plan file; PR-0 | default | 0 |
| W01 | 001–006 | `deploy/compose/litellm/*`, `token_log.py`, PoC-1 and PoC-6c notes, ADR-004; drafts "what the PoCs learned" for the decision pack; marks the SLM-baseline parts of W1 parked | default | 1 |
| W2a | 007–016 | `chassis-reference.md`, contracts v0–v3, PoC-1..4 READMEs, the PoC-9 W2–W5 report | default | 1 |
| W2b | 017–026 | contracts v3–v7, PoC-4/5/7/8 READMEs and backlog-changes, ADR-004/005/007/008/010, `deploy/helm/README.md` | default | 1 |
| W3 | 027–039 | PoC-6c notes, PoC-8 eval-gate note, `deploy/compose/litellm/config.local.yaml` | cheaper | 2 |
| W4 | 040–049 | `evaluator_gate.py`, ADR-010, PoC-8 gate tests, the PoC-7 draft (A-2) | cheaper | 2 |
| W5 | 050–063 | contracts v4–v5, PoC-5/6b notes, ADR-005/006, `packages/agentctl`, PoC-9 report | default | 2 |
| W6 | 064–075 | ADR-009/010, `packages/fake-langfuse`, PoC-8 golden set | cheaper | 3 |
| W7 | 076–093 | ADR-005 (trust), PoC-5 admission | cheaper | 3 |
| W89 | 094–115 | H-13 MCP work (PoC-3), ADR-001 follow-up (b) | cheaper | 3 |
| W1012 | 116–127 | PoC-4 load results (X-5), PoC-8 dashboards, `deploy/kind` | cheaper | 3 |
| C1 consolidation | all findings | `story.md`, `fix-plan.md`, `proposals.md`, the retire list and ROWS changes | default | 4 |
| C2 phase map + decision pack | all findings' phase column, the product plan, ADRs, PoC notes | the two files below | default | 4, parallel with C1 |

Session rules (from the core review): read only the brief, `rubric.md`, `findings-template.md`, and its issue files; an `Explore` subagent per 3–4 issues finds evidence and returns table rows only; never open a file over 400 lines in the main thread; write one findings file; run nothing that writes (no `sync.py`); commit nothing; `use_worktree: false`. A batch runs in parallel in one checkout; files are disjoint.

## Retire mechanism, numbers kept (smallest change that passes `check.py`)

- `docs/planning/tools/backlog.py`: `WAVES` gets two entries: first `"WD": "Done: delivered by the PoC track"`, last `"WP": "Parked: the SLM track (2026-11-06)"`. A parked row keeps its number and size; `note` says the old wave. `sync.py` leaves `WP` out of the totals and the critical path, as it does `WD`; `check.py` refuses a live issue that depends on a `WP` row. A retired row keeps its position and size; `wave` → `"WD"`; `note` → `"Was W2. Retired 2026-11-DD: delivered by PoC-2 and PoC-5."`. No new assertion: deps still point lower.
- `docs/planning/tools/sync.py`: `DONE = "WD"`. `critical_paths`: a `WD` row contributes 0 days and no arrow step. `build_plan`: `core` excludes `WD` and `W12`; new values `N_DONE`, `DONE_DAYS`; `rng(w)` returns `—` for an empty wave (so `{{R:WD}}` cannot raise on `min()` before the first retirement). The per-wave table already lists rows by wave in index order, so scattered `WD` rows render as one Done table. `main()` prints `N done (D days)` and the open total. `renumber_refs` unchanged.
- `docs/planning/tools/check.py`: count boxes as `re.findall(r"^- \[([ x])\]", ac, re.M)` (today `ac.count("- [ ]") < 3` fails a fully ticked issue). New rules: a ticked line must contain `Delivered in PoC-` or `Delivered by:` and a backtick token or link; a backtick path under `pocs/`, `packages/`, `deploy/`, `docs/`, `.github/` (split at `::`) must exist. A cited `::test_name` must be defined: extend `check_cited_tests` in `scripts/harness_lint.py` to also scan `docs/planning/issues/*.md` (it already scans PoC READMEs with `CITED_TEST` and `defined_test_names()`), so a renamed test fails `make harness-lint`. Milestone `Done: ...` ⇒ no open box and a `Delivered by:` line; milestone not Done and no open box ⇒ error only under `--strict` (on by default after PR-R).
- `docs/planning/tools/plan-template.md`: "How to read" says numbers are stable and done issues keep theirs; a Waves-table first row `Done: delivered by the PoC track | {{N_DONE}} issues | {{DONE_DAYS}} days of the first estimate, not counted below`; "Estimate check" shows `{{TOTAL_DAYS}}` open and `{{DONE_DAYS}}` done; "Open decisions" gains the router home and the ADR-001 leftovers, pointing at the decision pack; "Importing into GitHub": create the Done milestone closed.
- `docs/planning/CLAUDE.md` and `.claude/skills/planning-sync/SKILL.md`: the `WD` wave, the evidence rule, and "to retire: set the row's wave to `WD`, add `Delivered by:`, run sync, run check".
- `docs/planning/poc/000-plan.md` "After the MVP" (hand-written): replaced by "done by the backlog review on 2026-11-DD; the build order is the product plan's phases A to F".

## Apply phase: PR sequence and gates

| PR | Branch | Content | Gate |
| -- | ------ | ------- | ---- |
| PR-A | `claude/poc-plans-consolidation-d29862` | pre-step 1: done, PR #67 | hook (`make quick`), `make planning-sync planning-check` |
| PR-0 | `claude/backlog-review-setup` | folder, rubric, template, baseline, README, `docs/plans/2026-11-06-backlog-review.md` | `make harness-lint` |
| PR-1 | `docs/backlog-review-findings` | ten findings files, `story.md`, `fix-plan.md`, `proposals.md`, the decision pack, the W1 brief. Nothing under `docs/planning` changes | user reads and merges |
| PR-2 | `docs/planning-tools-retire` | the tooling changes above, no row changed; fix the few existing ticked lines (002, 008, 009, 010, 012, 013, 054) to the evidence form | `make planning-sync planning-check`, `make check`, `make harness-lint` |
| PR-3…PR-9 | `docs/backlog-ticks-<group>`, one worktree each: W01, W2a, W2b, W3W4, W5, W6W7, W8toW12 | ticks with evidence, folded bullets, drift fixes, `suggested:` cleanup, `Delivered by:` on issues that will retire. No `ROWS`, no template edit, so no conflicts between them | `make planning-sync planning-check` (sync prints `no changes` or only `updated NNN-...`), `make harness-lint` |
| PR-R | `docs/planning-retire`, alone, after PR-3…9 | `ROWS`: `wave='WD'` for the retire list, resizes and dep changes from `fix-plan.md`; `--strict` on; `make planning-sync` regenerates `000-plan.md` and frontmatter | `make planning-sync planning-check`, `make check`, `make harness-lint` |
| PR-P | `docs/planning-plan-prose` | template prose (waves table, "Why the order differs", critical path, estimate check, open decisions); `poc/000-plan.md` "After the MVP"; `001 DEC-1` body from the decision pack; the waves table and "Why the order differs" say the build order is the product plan's phases, with a link to `phase-map.md` | same as PR-2 |
| Close | — | plan file → `Status: done`; `poc/011-what-is-left.md` section 5 ticked; `verification.md` filled | — |

Never run `make lint-fix` in a planning PR: `codespell -w docs` can rewrite the epic and break its checksum.

## DEC-1 decision pack (`decision-pack-DEC-1.md`)

One section per open box of `001 DEC-1` that a product phase needs: Question / Options / What the PoCs learned (paths) / `suggested:` / Unblocks / Where to record. Boxes only the parked SLM track needs (banned words for the simplifier, the router's home for the SLM baseline) are marked parked, not decided.

| Decision | PoC evidence | `suggested:` |
| -------- | ------------ | ------------ |
| Event broker | ADR-004 broker client; Kafka adapter passes all `EventPortContract` cases; Kafka on kind with SASL/SCRAM (`deploy/kind/poc05/platform/kafka.yaml`); NATS never run; bar for a NATS adapter is ADR-004 item 8 | Kafka. Writes into 019 H-17, 020 X-8, 060 H-23 |
| First cloud | no AWS-vs-GCP evidence; cluster shape proven on kind (gVisor RuntimeClass, NetworkPolicy CNI, ValidatingAdmissionPolicy per ADR-005, native sidecars ≥1.33; `pocs/poc-05-sandboxed/notes/2026-10-09-gvisor-overhead.md`) | the cloud the existing services run in; it must give those three. Writes into 038 X-1a |
| Public A2A endpoint | A2A inbound exists (PoC-3); plain-A2A to third-party agents (PoC-6, contract v5) | stays P2 unless a named external consumer exists; 061 H-5 unchanged |
| Banned words, metric targets | PoC-8 golden set (`packages/bakeoff/src/bakeoff/golden/cases-gate.yaml`) and red-team set exist; no banned-word list anywhere | first list drafted from the gate cases; targets stay the epic's drafts until 005 G-3 |
| Reuse stack | Langfuse v4 used (ADR-007/009); lakeFS/DVC and the registry base untested | accept Langfuse; defer lakeFS-vs-DVC to 069 D-7, registry base to 076 R-1 |
| Dapr | ADR-004 accepted 2026-10-08 | tick now |
| Where the router runs | PoC-1 Compose (`deploy/compose/litellm/config.yaml`, `token_log.py`, no DB); PoC-8 `poc08-litellm` mints a scoped virtual key (`deploy/compose/poc08-litellm/stack.sh`) | the `local` Compose variant on one host the services reach, master key from env, one virtual key per service, until 038 X-1a |
| ADR-001 leftovers | VAP (ADR-005), gVisor, kagent-adk and Claude Agent SDK on kind (PoC-6b), `echo-typescript`, `packages/code-runner` fresh sandbox per call (PoC-5), guardrails per route (ADR-008 settles (d)) | record as decided by build |
| Owners (data, reviewer) | none | the user fills the table |
| Follow-ups (a), (b), (e), (f) | (e) partly by the gate's fallback route; (f) by the Collector regex pipeline (PoC-8); (a), (b) untouched | (e), (f) decided by build; (a), (b) stay open with homes 040 D-0 and 100 O-2 |

## Phase map (`phase-map.md`)

One section per product phase, A to F, from the product plan's "The order of work". Each lists:

- the issues it closes, with their open boxes after the review and their days;
- the product plan's gaps ("Gaps the PoC track did not cover") it covers, with the issue that owns each one, or "new issue";
- what it reuses from the PoC track (paths);
- the phase's "done when", copied from the product plan, and the evidence that will show it.

Then a table of the live issues with phase `later`, and the parked list with each issue's old wave. The totals per phase replace the old per-wave estimate as the build estimate.

## Verification (`verification.md`)

- Every issue has one findings row: `grep -h "^| [01][0-9][0-9] " docs/reviews/2026-11-06-backlog-review/findings-*.md | cut -d'|' -f2 | sort -u | wc -l` → 127, no duplicate across files.
- `make planning-sync planning-check` → `OK`; the sync last line shows `N done (D days)` and the open total; `make check` and `make harness-lint` green after PR-R and PR-P.
- Days recomputed per wave with a read-only snippet over `backlog.build()`, pasted next to the baseline numbers; `fix-plan.md` totals match.
- `grep -rn "Status after PoC" docs/planning/issues | wc -l` → 0.
- Sample re-check of 10 ticked criteria: pick 10 `- [x]` lines at random, run the cited test (`UV_NO_SYNC=1 uv run pytest <path>::<name>`) or open the cited note; record pass/fail per line.
- `001 DEC-1` has an owner and a date on every box, or a named blocker.

## Risks

- **Parallel sessions on one file.** Review sessions write only their findings file; tick PRs own disjoint issue files and never touch `ROWS` or the template; PR-R runs alone.
- **Epic checksum.** Never edited; `check.py` guards it; the `make lint-fix` trap is named above. If touched: `git checkout docs/planning/slm-agent-platform-epic-v3.md`.
- **Evidence rots.** A renamed test breaks a ticked line; the path-exists check catches deleted files, not renamed tests. Keep the rule: a renamed test updates every line that cites it.
- **The flaky key-leak test** (pre-step 1) can block any commit through the hook. If it recurs twice, file it as a HIGH finding against 015 H-8 and fix it in its own PR before the tick PRs.
- **Done issues sit in the middle of the order.** Accepted with "keep numbers": "work top to bottom" now skips Done rows; the plan text says so.
