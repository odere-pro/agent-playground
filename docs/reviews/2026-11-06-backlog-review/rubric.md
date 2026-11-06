# Rubric

Every review session checks each of its issues against this list, and against the product plan, `docs/plans/2026-11-06-agent-container-product.md`. The product plan leads; this review serves it. Every issue gets one row in the session's issue table. A row that is not `keep` also gets a numbered finding that cites the rule.

## Verdicts

`retire`, `tick`, `tick-blocked`, `keep`, `parked`, `resize S|M|L`, `deps`, `drift`, `moot`, `split`, `cite`, `conflict`. A row may carry more than one.

## Checks

1. **Evidence rule.** A box is ticked only as `- [x] <criterion> · Delivered in PoC-N: <evidence>`. Evidence is at least one of:
   - a test id, `path/to/test_x.py::test_name`, that runs in `make test`, `make test-poc`, or a named workflow;
   - a dated record, `pocs/poc-NN-*/notes/<date>-*.md` or `pocs/poc-NN-*/demo/<date>-*.md`;
   - a contract section, `docs/contracts/contract-vN.md#anchor`.

   A "Status after PoC" bullet, a README sentence, or the word "built" is not evidence. Verdict `tick-blocked`; the finding names the test that would prove it.
   Where to look: [chassis-reference.md](../../guides/chassis-reference.md), each PoC README's exit criteria, [contract v7](../../contracts/contract-v7.md), and `pocs/*/notes/backlog-changes.md`.
2. **Retire.** Every box is tickable under rule 1. Verdict `retire`. The finding gives the `Delivered by: PoC-N, PoC-M (date)` line.
3. **Size.** Count the open boxes after the ticks. Re-size against S (up to 1 day), M (2–3 days), L (4–5 days). Cite the open boxes; do not re-size on a feeling.
4. **Deps.** For each dependency in `docs/planning/tools/backlog.py` `ROWS`: is it still needed for the open boxes? Add a dependency the code revealed. Every dependency must point to a lower number.
5. **ADR drift.** Compare `## What` with ADR-001 to ADR-010. Flag a bullet an ADR overrode. Known spots: Dapr text in H-17, H-20, H-23 (ADR-004); container roles in CH-3 (PoC-4, PoC-5); the Collector path in H-7 (ADR-007); guardrails per key in H-6 (ADR-008); retry and fallback in H-4 (ADR-010). Fix: one "Decided in ADR-00N" sentence.
6. **`suggested:` values.** A value a PoC or an ADR settled: drop the marker and cite. A value still open that blocks W1 to W4: list it for the DEC-1 decision pack.
7. **Epic citation.** The `refs` and the epic story link still describe the scope after re-scoping (H-11, H-5, the CH issues).
8. **Moot.** An ADR made the work disappear (H-11 is the stock image; the Dapr half of H-23). An epic story cannot be deleted. Propose one: rewrite the criteria to what the ADR made true and retire with the ADR as evidence, or re-scope and keep.
9. **Split.** Open work over 5 days, or two clear owners. Split into `a` and `b`, as X-1a and X-1b.
10. **Status bullets.** Every "Status after PoC-N" bullet is folded: facts into ticks, open items into open boxes or `## Out of scope`, user questions into DEC-1 or the owner list. List each bullet the session folds. Also read the inputs never applied: `pocs/poc-07-cross-cutting-decisions/notes/backlog-changes.md` (a draft), the PoC-6c "to apply" list in `pocs/poc-06c-pretrained-slm/notes/backlog-changes.md`, `pocs/poc-09-agent-mvp-template/notes/2026-10-30-w2-w5-report.md`, and sections 3 and 5 of [011-what-is-left.md](../../planning/poc/011-what-is-left.md).
11. **Code conflict.** The code contradicts a criterion. Verdict `conflict`; the finding goes to `proposals.md`; the box stays open. No code change in this review. Known: `packages/chassis/src/chassis/server/evaluator_gate.py` fails open, and 042 A-2 wants fail-closed.
12. **Order.** No criterion depends on a higher-numbered issue.
13. **Parked.** The product plan parks the SLM track on 2026-11-06: PoC-6c, the vLLM adapter in 012 H-3, and the router SLM in 127 L-3. An issue that trains, serves, or routes to an SLM, or whose only purpose is SLM savings, gets `parked`. Likely: the W3 issues S-1 to S-8, M-1, C-8, the GPU half of X-2, M-5, and parts of W1 and W4. A live issue may not depend on a parked one. Drop the dependency, or split the issue.
14. **Phase.** Every live issue names one product phase, A to F, from the product plan's section 11 ("Order of work"), or `later` when no phase needs it. A gap in the product plan's section 9 with no issue gets a finding: a new issue, or a home in an existing one.

## Severity

- HIGH: a wrong size or dependency on the critical path; a criterion the code contradicts.
- MEDIUM: ADR drift, a stale bullet, a size change off the critical path.
- LOW: a citation or wording.

## Out of scope

- Any edit under `docs/planning/`. The apply PRs make the edits from `fix-plan.md`.
- Any code change. Code findings go to `proposals.md`.
- The epic, `docs/planning/slm-agent-platform-epic-v3.md`. It is never edited.
