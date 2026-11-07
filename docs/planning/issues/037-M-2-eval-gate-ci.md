---
title: "M-2: Eval gate in CI: a change ships only if it passes the golden set against the baseline"
labels: ["story", "priority:P0", "phase:2-simplifier-slm", "area:lifecycle", "size:S", "layer:platform"]
milestone: "W3 Simplifier SLM"
index: 37
epic_id: M-2
depends_on: ["028 S-3"]
blocks: ["047 M-3", "049 M-5", "068 D-3", "131 CH-12"]
epic_refs: [E.5, principles]
---

## Why

This is P0 because of the epic principle: nothing ships without an eval. The gate makes the rule automatic: a change to the repo ships only if it passes the golden set against the recorded baseline. It is a repo gate on a change, run on every pull request. There is no model registry: routes are LiteLLM config, and no trained model is promoted.

## What

- A CI job, `poc08-evals`, that runs on every pull request: DeepEval on the golden set, the gate against the baseline, and the drop-facts regression, per engine.
- The golden set is 50 cases for the `simplifier` agent (`packages/eval-kit/src/eval_kit/golden/cases-gate.yaml` plus the 20 PoC-7 cases). The baseline is `evals/gate/baseline.json` (`eval-baseline.v0`), read by `python -m eval_kit.evalgate`.
- A pass rule: each target keeps its recorded pass rate, and the `must_pass` cases pass. suggested: no tolerance on the pass rate while the model is scripted.
- The absolute floor for a hosted judge is a phase B decision, in the DEC-1 decision pack.
- On fail: the job fails, with a report attached to the run.
- The golden set location as config, so another dataset needs no code change.
- Decided in [ADR-010](../adr/010-evals-offline-tool-online-gate.md) item 1: DeepEval is the one offline tool (trial in `pocs/poc-07-cross-cutting-decisions/notes/2026-10-28-eval-tool-trial.md`).

## Out of scope

- Shadow mode, canary rollout, and one-step rollback (047 M-3, 048 M-4).
- Evals for quantized variants (049 M-5).
- The golden set agent and versioned golden sets (064 D-1, 069 D-7).
- A model registry, MLflow promotion, and a `models.model.promoted.v1` event: parked with the SLM track (032 M-1).
- The judge's agreement with the hand labels, and the label of g08 (028 S-3).
- Making `poc08-evals` a required check on `main`: a repository setting, on the owner list ([011 section 2](../poc/011-what-is-left.md#2-repository-settings)).
- Uploading the golden cases to Langfuse datasets: `eval_kit.langfuse_datasets` is tested against a stub and called by no workflow or Makefile target. A phase D hand-off.

## Acceptance criteria

- [x] A pull request starts the eval gate in CI with no manual step. · Delivered in PoC-8: `.github/workflows/poc08-evals.yml` (on: pull_request with no paths filter; jobs evals, regression, redteam); `pocs/poc-08-cross-cutting-build/tests/test_poc08_c1_ci.py::test_poc08_evals_runs_the_gate_and_the_redteam_set_on_every_supported_engine`; run 40919685333 on `0457ae1` (`pocs/poc-08-cross-cutting-build/README.md`, exit criterion "Offline evals")
- [x] A change that drops facts fails the gate, and the merge is blocked with a report. · Delivered in PoC-8: `packages/eval-kit/tests/test_eval_kit_evalgate.py::test_eval_kit_evalgate_fails_the_drop_facts_logic_on_every_baseline_target` and `::test_eval_kit_evalgate_fails_when_a_known_bad_answer_starts_to_pass`; `pocs/poc-08-cross-cutting-build/tests/test_poc08_c5_regression.py::test_the_gate_fails_drop_facts_and_passes_normal_on_every_baseline_target`; `pocs/poc-08-cross-cutting-build/notes/2026-10-29-eval-gate.md` (drop-facts 0/50 against 0.780); `evals/gate/baseline.json`
- [ ] A pass rate below the baseline floor fails, with or without a previous run. (Proving test, suggested name: `test_a_run_below_the_floor_fails_with_no_previous_report`.)
- [x] Eval results are attached to the CI run. · Delivered in PoC-8: `.github/workflows/poc08-evals.yml`, upload-artifact steps `poc08-eval-reports-<engine>` and `poc08-eval-reports-drop-facts` (run 40919685333)
- [ ] A change to the simplifier prompt runs the same gate before release, and the prompt reaches the gate's input. (Proving test, suggested name: `test_a_changed_prompt_changes_the_gate_input`.)
- [ ] Pointing the gate at another dataset version needs a config change only. (Proving test, suggested name: `test_evalgate_reads_the_dataset_named_in_the_baseline`.)

## Dependencies

- Depends on: [028 S-3](028-S-3-simplifier-metrics.md)
- Blocks: [047 M-3](047-M-3-shadow-mode.md), [049 M-5](049-M-5-quantized-variants.md), [068 D-3](068-D-3-import.md), [131 CH-12](131-CH-12-golden-cases-datasets.md)

## References

- Epic story: [M-2 in Phase 2](../slm-agent-platform-epic-v3.md#phase-2-simplifier-slm-and-model-lifecycle)
- Epic context: [E.5](../slm-agent-platform-epic-v3.md#e5) · [Principles](../slm-agent-platform-epic-v3.md#principles)
- Backlog plan: [000-plan.md](000-plan.md)
