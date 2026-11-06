# Findings W6: Golden set and evaluator SLM

Session: W6. Issues: 064–075. Waves: W6.
Rubric: [rubric.md](rubric.md).

## Summary

The PoC track built none of the W6 pipeline. There is no golden set agent, recorder, review queue, split, import, export, dataset version, PII-on-write step, retention job, audit store, trained evaluator, or MLflow. What exists is adjacent: 50 hand-labeled gate cases (`cases.yaml` 20 plus `cases-gate.yaml` 30), a Langfuse dataset uploader tested against a stub, the chassis redaction, the `EvaluatorPort` contract suite bound to the fake, the judge, and the encoder, and the producer side of two audit-relevant events. One box ticks (075 box 8, after a rewrite); nothing retires. Three issues are `parked` (073, 074, 075: the encoder SLM, its agreement test, and the swap), and the other nine are `later`: no product phase A–F needs a golden set agent, a review UI, lakeFS, or an audit store. The top risk is a gap, not an issue: phase D's "LangSmith datasets against the 20 golden cases" and the Langfuse dataset stack test that ADR-010 item 2 waits on have no issue (W6-01). The second risk is 068 D-3 and 126 L-2, which depend on parked issues (W6-02, W6-03).

## Issues

| Issue | Boxes | Phase | Verdicts | Evidence or reason |
| ----- | ----- | ----- | -------- | ------------------ |
| 064 D-1 | 0/7 | later | keep, drift, deps, cite | A data agent with Postgres tables, an outbox, and review events; none exists (no `data` kind in `agentctl` or `packages/workloads/*`, no `oversight.review.*` in code or specs). Adjacent only: `eval_kit.langfuse_datasets` upserts dataset items by id (stub-tested, no box). The plan has no phase for it; 040 D-0 is `later` (W4). Scoring text names an `evaluator-facts` agent that ADR-010 item 11 replaced and the Langfuse "decision" is settled (W6-10); add 028 S-3 (W6-11); no phase (W6-09) |
| 065 D-2 | 0/6 | later | keep, cite | Review queue, approve/reject/edit, `oversight.review.decided.v1`, append-only history. None built; `fake-langfuse` has only scores, OTLP, and health routes, so no queue to test against. The nearest piece is `POST /v1/feedback`, which scores a trace and is not a review. Reviewer role is an open question (ADR-009 Q2, plan P3). Blocks 073 (parked) and 110 C-6 (W6-12) |
| 066 D-6 | 0/6 | later | keep, cite | Split by input hash, locked `holdout`, tag API. No `split`, `tags`, or `source` on `Case`; no code hashes an input to a split. Depends on 064. Both candidate dataset stores have native splits and tags (W6-13) |
| 067 D-5 | 0/6 | later | keep, cite | Per-task record schema, row-level rejects, hash dedup, near-dup flags, quality report. Only the eval case file is checked, by `eval_kit.golden.parse_cases` (`test_eval_kit_golden.py::test_eval_kit_golden_rejects_a_bad_file`); it stops at the first error, has no row numbers, no `source`, no dedup. Not a box |
| 068 D-3 | 0/7 | later | keep, deps, drift | Three import paths, four formats, a size limit, a bucket watch. None built; the chassis has no upload route and its only S3 code is the `ConfigPort` adapter. Depends on 030 S-4 (parked): replace with the 50-case gate set and `037 M-2` (W6-02) |
| 069 D-7 | 0/6 | later | keep, parked, cite | lakeFS (BSL since v1.87.0), a protected main, version list and diff. No lakeFS or DVC anywhere in code, deploy, or `uv.lock`; golden files are versioned by Git and a `version: 1` field. Box 6 (MLflow, 033 S-5) is parked with the SLM track (W6-16). The store choice is open and blocks nothing in A–F (W6-15) |
| 070 D-4 | 0/6 | later | keep, parked, cite | Export in JSONL, CSV, Parquet, Hugging Face; filters; version id. No export code; `pyarrow`, `pandas`, `datasets` are not in `uv.lock`; `Case` has no split or state. Depends on 066, 068, and 069, all `later`. Blocks 073 (parked), 096 P-2a, 126 L-2 (W6-17) |
| 071 D-8 | 0/7 | later | keep, tick-blocked, parked | PII removal on write, retention job, deletion on request. The chassis redaction exists (`chassis/redaction.py`; the Collector masks emails and phones) but no recorder, import, or review writer calls it; no `retention_days`, no deletion path, no deletion report. The MLflow bullet is parked (W6-16, W6-18) |
| 072 C-3 | 0/8 | later | keep, tick-blocked, drift, cite | Audit log data agent. Producer side only: `agents.task.completed.v1` and `agents.feedback.received.v1` are published and valid. No consumer (`spec.events.consume` is refused at load), no object lock, no hash chain, no `log_retention_days`, no export API. Dapr text overridden by ADR-004 (W6-19). Gap (a) unbuilt (W6-22). Size L stays (W6-20, W6-21) |
| 073 E-1 | 0/6 | parked | parked | Trains ModernBERT and DeBERTa on a versioned golden set. No MLflow, lakeFS, or training code; Hugging Face answered 403 in PoC-7, so the off-the-shelf NLI encoder behind TEI was never run either. The plan parks `EncoderEvaluator` and the SLM track. Depends on 033 S-5 (parked), 065, 070 (W6-04) |
| 074 E-2 | 0/7 | parked | parked, deps | Agreement of the evaluator SLM with ≥300 human labels. No kappa code; the method is written in `pocs/poc-07-cross-cutting-decisions/notes/2026-10-28-evaluator-gate.md` and every cell reads "waits on the Mac run". The judge's own agreement is owned by 028 S-3 box 5 (W3-02). Depends on 073 and 030 (both parked) (W6-05) |
| 075 E-3 | 1/8 | parked | parked, tick, conflict, cite | Swaps the encoder in for the judge. Box 8 ticks (the `EvaluatorPortContract` suite binds the fake, the judge, and the encoder). Box 6 (rollback with no restart) is contradicted: `spec.adapters` is restart-only. No shadow code, no `evaluator-facts` agent, no encoder-versus-judge numbers (W6-06 to W6-08) |

## Ticks

One line per box this session ticks, ready to paste into the issue. Conditional on the rewrite named in the line.

- `075 E-3`, box 8, rewritten to "The `EncoderEvaluator` adapter passes the same `EvaluatorPort` contract suite as the judge and the fake" (W6-07): `· Delivered in PoC-7: packages/contract-suites/src/chassis_contracts/evaluator.py (EvaluatorPortContract, four cases); packages/chassis/tests/test_evaluators.py::TestEncoderEvaluator and ::TestJudgeEvaluator; packages/chassis/tests/test_contracts_cross_cutting_ports.py::TestScriptedEvaluator; pocs/poc-07-cross-cutting-decisions/tests/test_poc07_evals_adapters.py::TestPoc07EvalsEncoderEvaluator; docs/planning/adr/010-evals-offline-tool-online-gate.md item 8`

Offline tests cited in this file were run on 2026-11-06:

```
$ UV_NO_SYNC=1 scripts/check_offline.sh <10 ids from this file> -q -p no:cacheprovider
..................................                                       [100%]
34 passed in 5.06s
```

The ten ids: `test_evaluators.py::TestEncoderEvaluator`, `::TestJudgeEvaluator`, `test_contracts_cross_cutting_ports.py::TestScriptedEvaluator`, `test_poc07_evals_adapters.py::TestPoc07EvalsEncoderEvaluator`, `test_result_events.py::test_a_completed_run_publishes_one_valid_cloudevent`, `test_read_only_compose.py::test_no_key_in_any_workload_env`, `test_config_loader.py::test_a_restart_only_change_is_refused`, `test_eval_kit_gate_golden.py::test_eval_kit_gate_golden_has_50_labelled_cases_holding_the_poc07_set`, `test_eval_kit_golden.py::test_eval_kit_golden_has_20_labelled_cases_of_both_tasks`, and `test_eval_kit_langfuse_datasets.py::test_eval_kit_langfuse_creates_the_dataset_and_one_run_per_report`. The class ids and the parametrized ones expand to 34 cases. Other ids named below were checked by `grep -rn "def test_name"` and not run: `test_kafka_refuses_the_workload_without_the_chassis_credential` (a kind test), `test_the_log_pipeline_masks_each_entity`, `test_a_completed_runs_event_holds_no_pii`, `test_after_max_attempts_the_event_goes_to_the_dead_letter_topic`, `test_sample_rate_zero_makes_no_evaluator_call`.

## Status bullets folded

None of 064–075 has a "Status after PoC-N" bullet (`grep` finds none). The inputs never applied do name some of them (W6-14):

- `064 D-1`, PoC-7 draft (`pocs/poc-07-cross-cutting-decisions/notes/backlog-changes.md` lines 15 and 68): "may consume `agents.feedback.received.v1`; 064's Langfuse decision reopens ADR-009". Open item, now answered by ADR-010 item 2 (Langfuse datasets, LangSmith a candidate). Goes into "Status after PoC-7" on 064.
- `064 D-1`, PoC-8 backlog changes (line 50): "About 50 cases per agent holds for one agent; every other agent needs its own set; that is 064 D-1's work." Out of scope for the golden set agent today; goes into "Status after PoC-8" as a fact (50 cases, `packages/eval-kit/src/eval_kit/golden/`).
- `072 C-3`, PoC-7 draft (line 15): `agents.feedback.received.v1` lists the audit log as a consumer. Fact for box 1; the event is published and valid, nothing consumes it.
- `073 E-1`, `074 E-2`, `075 E-3`, PoC-7 draft (line 71): "the encoder adapter and the gate cost formula (ADR-010 item 9) are where their numbers plug in; waits on encoder agreement from the Mac run". Goes into the parked bodies as a note; owner list (011 section 1).
- The PoC-6c list and the PoC-9 W2–W5 report name none of 064–075.

## Findings

### W6-01 [HIGH] [rule 14] docs/planning/issues/037-M-2-eval-gate-ci.md
Problem: Phase D's "LangSmith datasets against the 20 golden cases (ADR-010's trial rule)" has no issue, and neither has the Langfuse dataset stack test that ADR-010 item 2 waits on (`eval_kit.langfuse_datasets` is called by no workflow or Makefile target, and `test_poc07_stack.py` has no dataset test). Every W6 issue that touches datasets (064–070) is a store or an agent the plan does not need.
Why it matters: phase D's done-when ("one eval run visible in LangSmith") has no box that owns it, and the only code (`packages/eval-kit/src/eval_kit/langfuse_datasets.py`) is stub-tested.
Fix: add one small issue, "Golden cases in Langfuse and LangSmith datasets", size S, phase D, depends on `037 M-2`. Boxes: the 20 cases and one run per engine land in a LangSmith dataset through `fake-langsmith`; the verdicts match on two engines (ADR-010 note of 2026-11-06); a stack test for the Langfuse dataset and its runs; a workflow or Makefile target calls the upload; the dataset name comes from config (shares 037 box 6, W3-08). The alternative home is 037 M-2 itself, which would grow it past S; the coordinator picks. Do not rewrite 070 D-4 for this: its export design stays.
Status: open

### W6-02 [HIGH] [rule 13] docs/planning/issues/068-D-3-import.md
Problem: 068 D-3 depends on 030 S-4, which W3 parked (W3-03), and its box 4 and `## Why` are written around "the S-4 held-out test set (≥ 300 records)". No such set exists or will be built.
Why it matters: a `later` issue may not wait on a parked one, and box 4 is unreachable as written.
Fix: confirm W3-03: in `ROWS`, replace `S-4` with `M-2` (037, the gate set and `evals/gate/baseline.json`). Rewrite box 4 to "The 50-case gate set (`cases.yaml` plus `cases-gate.yaml`) imports in one step, marked as already reviewed, with split `holdout`", and the `## Why` sentence the same way. 030's box 4 (reviewer, date, reason per label) lands on 064 D-1 or 065 D-2 as W3-03 says; neither is live, so it waits with them.
Status: open

### W6-03 [HIGH] [rule 13] docs/planning/issues/126-L-2-system-prompt-improver.md
Problem: 126 L-2 depends on 075 E-3, which this review parks (and on 070 D-4, `later`). L-2 is phase `later` and builds on "this evaluator".
Why it matters: it is the one issue outside W6 that waits on a parked W6 issue.
Fix: in `ROWS`, replace `E-3` with `A-2` (042, the gate and the judge) and keep `D-4`; the W1012 session rewrites the text from "the evaluator SLM" to "the judge behind `EvaluatorPort`". Its `blocks` entry on 075 goes away by regeneration.
Status: open

### W6-04 [HIGH] [rule 13] docs/planning/issues/073-E-1-evaluator-slm.md
Problem: 073 E-1 trains ModernBERT and DeBERTa on an exported golden set version, logs MLflow runs, and logs compute (034 C-8). It depends on 033 S-5 (parked).
Why it matters: the plan parks `EncoderEvaluator` and the SLM track; nothing in A–F trains a model.
Fix: `parked`. Keep the body. Add a note: "unpark 034 C-8 first" (W3-18) and "the NLI cross-encoder behind TEI (`EncoderEvaluator`) is the code that exists; the Hugging Face model was never run (403 in PoC-7)". Its `## Why` open concern (the gate's cost) is answered by the judge at rate 0.1, about $5 per million requests (ADR-010 item 9, `docs/specs/models.md`).
Status: open

### W6-05 [HIGH] [rule 13] docs/planning/issues/074-E-2-evaluator-agreement-test.md
Problem: 074 E-2 tests the evaluator SLM against ≥300 human labels; it depends on 073 E-1 and 030 S-4, both parked.
Why it matters: the agreement test that phase B needs is the judge's, on the 50 labeled cases, and 028 S-3 box 5 (W3-02) owns it. Nothing else in 074 is needed.
Fix: `parked`. Keep the design (kappa, fail-class recall, a bar fixed before the first run, per-slice results) for a future encoder. Cross-link 028 S-3 box 5 in the parked note: "the judge's agreement is measured there".
Status: open

### W6-06 [HIGH] [rule 13] docs/planning/issues/075-E-3-evaluator-replaces-judge.md
Problem: 075 E-3 swaps a trained evaluator SLM in for the judge, with a shadow run (047 M-3, parked by W4) and a cost thesis ("judge cost per call drops").
Why it matters: the plan keeps the cheap judge on by default and parks the encoder; ADR-010 item 10 still says "with an encoder that passes 074 E-2's bar: `sync` at rate 1.0", which the plan no longer needs.
Fix: `parked`; keep box 8 ticked (W6-07). Add to the ADR-010 hand-off in `proposals.md`: item 10's encoder default is conditional on an unparked 074; the plan's default is the judge at 0.1 on `decision-small`.
Status: proposal

### W6-07 [MEDIUM] [rule 1] docs/planning/issues/075-E-3-evaluator-replaces-judge.md
Problem: Box 8 ("The evaluator SLM adapter passes the same `EvaluatorPort` contract suite as the LLM judge and the fake") is met by the encoder adapter, but is worded for a trained SLM, so it cannot be ticked as written.
Why it matters: the contract bar is done and shows as 0 of 8.
Fix: reword to "The `EncoderEvaluator` adapter passes ..." and tick with the evidence in Ticks. The four contract cases run against three bindings (the fake, `TestJudgeEvaluator`, `TestEncoderEvaluator`) and are rebound in PoC-7 (ran, 34 passed). The adapter is the off-the-shelf NLI stub over TEI, not a trained SLM; say so in the box.
Status: open

### W6-08 [MEDIUM] [rule 11] docs/planning/issues/075-E-3-evaluator-replaces-judge.md
Problem: Box 6 ("Rolling back to the big-model judge is one config change, with no restart") contradicts the code: `spec.adapters` is restart-only (`packages/chassis/tests/test_config_loader.py::test_a_restart_only_change_is_refused`, a `spec.adapters` case), and `RELOADABLE` lists `evaluator_sample_rate` but not the evaluator choice. ADR-010's "switching from judge to encoder is one `spec.adapters.evaluator` change" is true of the config key, not of a live reload.
Why it matters: a box the code contradicts must stay open and go to `proposals.md`; it applies if 075 is ever unparked.
Fix: leave the box open; note that a judge-model swap needs no restart once phase B lands (routes reload), and an adapter swap does. Verdict `conflict`; proposal: make `spec.adapters.evaluator` reloadable, or reword the box to "one config change and a rolling restart".
Status: proposal

### W6-09 [MEDIUM] [rule 14] docs/planning/issues/064-D-1-golden-set-agent.md
Problem: 064 D-1 has no phase: the plan's A–F have no data agent, no golden set tables, and no review events, and 040 D-0, the recorder it reads, is `later` (W4).
Why it matters: a data agent, a Postgres store, an outbox, and a review flow are 3 to 4 weeks of work the plan does not ask for.
Fix: phase `later`, size M stays (7 open boxes; no box is built, so nothing moves the estimate). Name in `## Out of scope`: "datasets in Langfuse and LangSmith for the gate set are phase D (see the new issue in W6-01)". The ADR-001 follow-up (a) decision (W6-22) must come first when this unparks.
Status: open

### W6-10 [MEDIUM] [rule 5] docs/planning/issues/064-D-1-golden-set-agent.md
Problem: Two `## What` bullets drift from ADR-010 items 2 and 11: the record is scored "through `EvaluatorPort` (the `evaluator-facts` agent, a big-model judge until 075 E-3)", and `## Reuse` says "Decision: confirm Langfuse as the golden set workspace at the start of this issue".
Why it matters: no `evaluator-facts` agent exists (the judge is `JudgeEvaluator` on a LiteLLM route inside the chassis, `packages/chassis/src/chassis/adapters/evaluators.py`), and ADR-010 item 2 already decided "Langfuse datasets hold the golden cases", with LangSmith datasets a candidate under the trial rule (ADR-010 note, 2026-11-06).
Fix: add "Decided in ADR-010 items 2 and 11"; rewrite the scoring bullet to "scores through `EvaluatorPort` on the judge route (`spec.model.judge_route`)"; delete the Decision bullet and keep "LangSmith datasets are a second store once the phase D trial passes".
Status: open

### W6-11 [MEDIUM] [rule 4] docs/planning/issues/064-D-1-golden-set-agent.md
Problem: The body scores records with "the S-3 metrics (028 S-3)" but `ROWS` lists only 017 H-4, 040 D-0, and 042 A-2.
Why it matters: the dependency W3 rewrites into the `facts_kept` and banned-word scorer (W3-04) is real and undeclared.
Fix: add `S-3` to the dependencies of D-1 in `ROWS` (028 is lower than 064); drop nothing. 042 A-2 depends on parked 041 and 028 (W4-03); the W4 session owns that.
Status: open

### W6-12 [MEDIUM] [rule 14] docs/planning/issues/065-D-2-review-ui.md
Problem: 065 D-2 has no phase, and its role model rests on a decision the plan lists as open (a reviewer role, ADR-009 Q2; plan P3 "Feedback: subject binding, a reviewer role, comment redaction").
Why it matters: `fake-langfuse` has no annotation-queue route, so the box "a reviewer opens the queue" cannot be tested offline even if built.
Fix: phase `later`, size M stays (6 open boxes). Add the `fake-langfuse` queue route to the body as the offline test seam. Blocks 110 C-6 (W9) and 073 (parked) by regeneration.
Status: open

### W6-13 [MEDIUM] [rule 14] docs/planning/issues/066-D-6-tags-splits.md
Problem: 066 D-6 and 067 D-5 build splits, tags, schema checks, and dedup as new services, and both depend on 064 D-1.
Why it matters: Langfuse and LangSmith datasets have items, metadata, and splits natively, so a store choice (phase D) changes both issues' boxes.
Fix: phase `later` for both, sizes S stay. Add one line to each `## Reuse`: "Decide with the dataset store (ADR-010 item 2): splits and tags may be dataset fields, not tables". No box change now.
Status: open

### W6-14 [MEDIUM] [rule 10] docs/planning/issues/064-D-1-golden-set-agent.md
Problem: Four never-applied inputs name W6 issues (see Status bullets folded): PoC-7 draft lines 15, 68, 71 and PoC-8 backlog changes line 50.
Why it matters: B-3 in the baseline says no issue carries a "Status after PoC-7" bullet.
Fix: add "Status after PoC-7" bullets to 064 D-1, 072 C-3, and the parked 073–075, and a "Status after PoC-8" bullet to 064 D-1, as listed above.
Status: open

### W6-15 [MEDIUM] [rule 6] docs/planning/issues/069-D-7-lakefs-versions.md
Problem: The lakeFS licence choice (BSL 1.1 from v1.87.0; keep, DVC, or Langfuse dataset versions) is recorded only as "Record the choice in DEC-1".
Why it matters: ADR-010 item 2 already puts the golden cases in Langfuse datasets, which version their runs, so the "Langfuse dataset versions" option is the live one; nothing in A–F needs lakeFS.
Fix: phase `later`, size M stays (6 boxes, box 6 parked). Keep the choice out of the DEC-1 pack: it blocks no W1 to W4 issue. Add a "Decided in ADR-010 item 2" sentence to the Decision bullet: "default Langfuse dataset versions; lakeFS only if exports must be byte-identical objects".
Status: open

### W6-16 [MEDIUM] [rule 13] docs/planning/issues/069-D-7-lakefs-versions.md
Problem: Four SLM remnants sit in live-numbered issues: 069 box 6 and the bullet "the training pipeline (033 S-5) reads a dataset by version ID, and MLflow records that ID"; 070 `## Why` and `## Out of scope` ("feeds the evaluator SLM", "training on exports"); 071 `## What` ("MLflow models trained on a withdrawn version are flagged"); 070's `blocks` on 073.
Why it matters: they make golden set work look tied to the parked SLM track.
Fix: mark 069 box 6, the MLflow bullet in 069, and the MLflow bullet in 071 as "parked with the SLM track" under `## Out of scope`; reword 070's `## Why` to "feeds the MCP golden set tools (096 P-2a)"; the `blocks` on 073 regenerates.
Status: open

### W6-17 [MEDIUM] [rule 4] docs/planning/issues/070-D-4-export-formats.md
Problem: 070 D-4 depends on 066, 068, and 069 (all `later`) and is the dependency of 096 P-2a (W8) and 126 L-2.
Why it matters: its first box already says "ties every export to an immutable lakeFS version"; if W6-15 picks Langfuse versions, "version ID" changes meaning.
Fix: phase `later`, size S stays. Reword box 1 and the `## What` bullet from "lakeFS version" to "dataset version id (the store decided with 069)". Dependencies keep 066, 068, 069 as is.
Status: open

### W6-18 [MEDIUM] [rule 1] docs/planning/issues/071-D-8-data-rules.md
Problem: Box 1 (a PII fixture is redacted in every store) and box 7 (the PII rules apply on every import, review, and export path) have only the function: `redact`, `redact_value`, and `redact_error_text` in `packages/chassis/src/chassis/redaction.py`. It masks emails, phones, cards, IBANs, and US SSNs, not names. It runs on chassis logs, published feedback events, and in the Collector log pipeline. No recorder, import, or review writer exists to call it. No `retention_days`, deletion, or deletion report exists.
Why it matters: the function is tested but no store write is, so no box can be ticked.
Fix: phase `later`, size M stays (7 open boxes). Verdict `tick-blocked` on box 1: the proving test would be `test_a_pii_fixture_is_redacted_in_every_store_write`, in the first data agent that exists. Cite the redaction tests in `## Reuse` so the rule is not rebuilt: `packages/chassis/tests/test_redaction_and_chunks.py::test_redact_masks_emails_phones_and_ids`, `test_collector_log_redaction.py::test_the_log_pipeline_masks_each_entity`. Names stay a gap until the Presidio question (011 section 3, Q6) is answered.
Status: open

### W6-19 [MEDIUM] [rule 5] docs/planning/issues/072-C-3-audit-log-agent.md
Problem: Two bullets are overridden by ADR-004 (events through a broker client, accepted 2026-10-08): "Events arrive through a Dapr subscription on the chassis port, never on the workload's. How Dapr's API is kept from the workload is gap (c)", and the `## Reuse` line "a Dapr subscription on the chassis port".
Why it matters: an engineer would build Dapr plumbing the product does not use.
Fix: add "Decided in ADR-004: a broker client in the chassis; the `dapr` adapter stays a tested alternative", and replace both Dapr mentions with "the chassis consumes the events through its broker client and hands them to the audit workload". Depends on 021 H-20, which is `later` and refused at load (`spec.events.consume`).
Status: open

### W6-20 [MEDIUM] [rule 14] docs/planning/issues/072-C-3-audit-log-agent.md
Problem: 072 C-3 has no phase. The plan's phase E (SLOs, container dashboard, guard-stage span, OWASP map, weekly record) has no audit store, and security.md has no "audit" entry.
Why it matters: its six dependencies are 008 H-14 (`later`, part), 019 H-17, 020 X-8, 021 H-20, 053 H-22 (`later`), and 022 H-6 (E); an audit store waits on the whole event chain.
Fix: phase `later`; size L stays (8 open boxes, 4 to 5 days; one owner holds the store, the agent, and the export). No split. Four issues (095, 110, 118, 124) depend on it; see W6-23.
Status: open

### W6-21 [MEDIUM] [rule 1] docs/planning/issues/072-C-3-audit-log-agent.md
Problem: Four boxes have partial evidence that is the wrong half. Box 1: the producer side only (`packages/chassis/tests/test_result_events.py::test_a_completed_run_publishes_one_valid_cloudevent`, ran; `agents.feedback.received.v1` too). Box 5: the contract rule "consumers dedupe by `id`" (`docs/specs/contract.md` section 6) and a fixed id per run and type, with no consumer. Box 6: redaction on the published event (`packages/chassis/tests/test_result_events_redaction.py::test_a_completed_runs_event_holds_no_pii`, checked), not on a stored entry. Box 7: the broker half (`pocs/poc-05-sandboxed/tests/test_poc05_kind_hardreq1.py::test_kafka_refuses_the_workload_without_the_chassis_credential`; `pocs/poc-04-stateless-scalable/tests/test_read_only_compose.py::test_no_key_in_any_workload_env`, ran), with no audit store to hold a credential.
Why it matters: none of the four is a tick, but all four will be easy to over-claim.
Fix: `tick-blocked` for boxes 1, 5, 6, and 7. The proving tests would be `test_each_audit_event_type_lands_in_the_store`, `test_a_duplicate_event_is_stored_once`, `test_a_stored_entry_holds_no_pii`, and a kind test that reads the audit workload's environment and mounts (suggested names). The dead-letter retry exists in `EventPortContract` (`packages/contract-suites/src/chassis_contracts/events.py`, `test_after_max_attempts_the_event_goes_to_the_dead_letter_topic`) and is a dependency (053), not a box here.
Status: open

### W6-22 [MEDIUM] [rule 5] docs/planning/issues/000-plan.md
Problem: ADR-001 follow-up (a), "services that write their own store", names 040 D-0, 064 D-1, 072 C-3, 076 R-1, and 113 O-9. The product plan says nothing about it, and the chassis has no Postgres store (only the MinIO `ConfigPort` adapter).
Why it matters: 064 and 072 both need the chassis to hold the store credential and expose it to the data agent as MCP tools; the suggested default is unbuilt and unowned.
Fix: no change to phases A–F. Keep (a) open in `000-plan.md`; add to 064 and 072 `## What`: "blocked on follow-up (a): decide before the first box". Add (a) to the owner list in `011-what-is-left.md` section 3 only when one of 040, 064, or 072 leaves `later`.
Status: deferred (the issues are `later`)

### W6-23 [MEDIUM] [rule 4] docs/planning/issues/072-C-3-audit-log-agent.md
Problem: Nine issues outside W6 depend on W6 issues that are now `later` or parked: 072 C-3 blocks 095 P-4, 110 C-6, 118 C-7, and 124 C-9; 070 D-4 blocks 096 P-2a and 126 L-2; 068 D-3 blocks 106 O-5 and 113 O-9; 065 D-2 blocks 110 C-6; 069 D-7 blocks 121 X-4.
Why it matters: those issues cannot be phase A–F work while their dependencies are `later`; the W8, W9, W10, and W11 sessions must see why.
Fix: no `ROWS` change here. Each dependent is `later` or goes through its own session: 095 P-4 (MCP audit log), 096 P-2a (MCP golden set tools), 106 O-5 and 113 O-9 (orchestrator, import), 110 C-6 (oversight), 118 C-7 (incidents), 121 X-4 (backups), 124 C-9 (control mapping), and 126 L-2 (W6-03). No live phase issue depends on a W6 issue.
Status: open

### W6-24 [LOW] [rule 1] docs/specs/models.md
Problem: `docs/specs/models.md` (the judge section) says the gate set is `golden/cases-gate.yaml`, "30 cases"; the gate set is 50 (`cases.yaml` 20 plus `cases-gate.yaml` 30; `test_eval_kit_gate_golden.py::test_eval_kit_gate_golden_has_50_labelled_cases_holding_the_poc07_set`, ran). The plan's phase D uses the 20-case subset for LangSmith datasets, which is the ADR-010 trial set.
Why it matters: phase B's agreement measurement is on the 50 labels (W3-02); a reader of the spec would plan 30.
Fix: spec edit, not a planning edit: change "30 cases" to "50 cases (the 20 trial cases plus 30)". Goes to `proposals.md`.
Status: proposal

### W6-25 [LOW] [rule 6] docs/planning/issues/064-D-1-golden-set-agent.md
Problem: The `suggested:` markers in 064–075 (uncertain-band thresholds, record states, split ratios 80/10/10, near-dup threshold, kappa ≥ 0.8, 100,000 rows, p95 under 100 ms) are unsettled and unowned.
Why it matters: none blocks a W1 to W4 issue, because nothing in W6 is live.
Fix: leave the markers in the bodies; list none for the DEC-1 pack. The one value a phase needs, the judge's agreement bar on the 50 labels, sits in 028 S-3 box 5.
Status: deferred (every issue is `later` or parked)
