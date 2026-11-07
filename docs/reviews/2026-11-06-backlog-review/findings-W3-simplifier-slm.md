# Findings W3: W3 Simplifier SLM

Session: W3. Issues: 027–039. Waves: W3.
Rubric: [rubric.md](rubric.md).

## Summary

The PoC track built none of the W3 SLM chain. The repo has no MLflow, vLLM, Unsloth, TRL, cost model, training data, or Terraform; PoC-6c ran a pre-trained Qwen3-1.7B on llama.cpp and nothing else. What it did build under other names is what two live issues need: 50 hand-labeled golden cases, DeepEval, `evals/gate/baseline.json`, the drop-facts regression, and a CI workflow (`poc08-evals`) serve 037 M-2 and 028 S-3 (phase B); the PoC-5 kind suites serve three of 038 X-1a's boxes. Ten issues are parked (027, 029–036, 039), 028 and 037 stay live in phase B after a rewrite, 038 is `later`, and nothing retires. The top risk is live issues that depend on parked ones: 041 A-1 on 036 S-7, 068 D-3 and 074 E-2 on 030 S-4, and 117 X-3 (phase E) on 038 X-1a.

## Issues

| Issue | Boxes | Phase | Verdicts | Evidence or reason |
| ----- | ----- | ----- | -------- | ------------------ |
| 027 S-1 | 0/6 | parked | parked | Rewrite rules and a banned-word list feed only the teacher, the metrics, and the reviewers of the SLM data. Nothing exists: no rules file, no list, no config-store prompt. The only banned words are per-case `banned:` lists in `packages/eval-kit/src/eval_kit/golden/*.yaml`, which stay with the golden set. Blocks 028 and 029; the 028 link is dropped (W3-04) |
| 028 S-3 | 0/6 | B | resize S, parked, deps, tick-blocked, cite | Tokens, readability, and cosine similarity are SLM data filters: parked. Live: `facts_kept` and banned words as the judge and gate score. Built in part: `JudgeEvaluator` with rubric `judge-facts-v1` (`packages/chassis/src/chassis/adapters/evaluators.py`), per-case `contains:` and `banned:` checks, 50 labeled cases. Open: a 0–1 `facts_kept` fixture test, a repeat-run test, the agreement run (Mac), route and rubric version in the report. Drop the 027 S-1 dependency. 4 live boxes, three already half built: S |
| 029 S-2 | 0/6 | parked | parked | Generates 2,000–5,000 training pairs with an open-weight teacher. No teacher, no pairs, no generator beyond `eval_kit.golden.generate` (golden fixtures). Depends on 003 G-1b (`teacher-open`, parked), 027, and 028 |
| 030 S-4 | 0/6 | parked | parked, deps | The 300-record held-out set and the training split serve only training. The 50-case gate set replaces it as the golden set (`evals/gate/baseline.json`). Box 4 (reviewer, date, reason per label) moves to 064 D-1 and 065 D-2. Blocks 068 D-3 and 074 E-2, both live: rewire (W3-03) |
| 031 G-5 | 0/6 | parked | parked, cite | GPU hosting cost against API savings, for the SLM go or no-go. No baseline (005 G-3 is parked), no GPU price, no model. `docs/specs/metrics.md:32` points a live target at it (W3-13) |
| 032 M-1 | 0/6 | parked | parked, deps | MLflow tracking and a model registry for trained models. Referenced by no code, compose file, pyproject, or lock file. The product has no trained model; routes are LiteLLM config. Blocks 037 (dropped, W3-05) and 123 C-4 (W3-14) |
| 033 S-5 | 0/6 | parked | parked | LoRA training with Unsloth or TRL. No training code; no `peft`, `trl`, or `unsloth` in `uv.lock`. Blocks 034, 035, 063 S-8 (parked), 073 E-1 (W6 decides) |
| 034 C-8 | 0/5 | parked | parked | Training compute against the GPAI one-third threshold, per fine-tune. Nothing is fine-tuned, so no compute exists to log. The duty starts with the first fine-tune: unpark this before 033 (W3-18) |
| 035 S-6 | 0/6 | parked | parked | Trains Qwen3, Gemma 3, and Llama 3.2 candidates and picks one. PoC-6c used Qwen3-1.7B untrained and no Gemma or Llama; its one measured fact is a 0% fallback share on two easy tasks (`pocs/poc-06c-pretrained-slm/notes/2026-10-28-slm-run.md`) |
| 036 S-7 | 0/6 | parked | parked, deps, cite | Serves the fine-tuned model with vLLM on `simplifier-slm`. PoC-6c serves a base model with llama.cpp on `local-small` instead; the route-switch property holds (`pocs/poc-06c-pretrained-slm/tests/test_poc06c_route_switch.py::test_only_the_route_changes_and_every_task_passes_both_times`, ran). Three PoC-6c lessons fold into this issue (W3-15). Blocks 041 A-1 and 091 R-11, both live (W3-01) |
| 037 M-2 | 3/6 | B | tick, resize S, deps, parked, tick-blocked, cite | The offline gate exists as `poc08-evals` plus DeepEval plus `evals/gate/baseline.json`. Boxes 1, 2, and 7 tick after a rewrite (see Ticks); box 4 (MLflow promotion and `models.model.promoted.v1`) is moot and merges into 7. Open: 3 (a floor), 5 (a prompt change reaches the gate), 6 (dataset by config), plus the Mac agreement run. Drop deps on 030, 032, 020. 4 open boxes: S (W3-05 to W3-08, W3-21) |
| 038 X-1a | 3/10 | later | tick, tick-blocked, drift, parked, deps | Nothing cloud exists: no `*.tf`, Argo CD, CloudNativePG, or Vault file. The first cloud is a P3 open decision and phases A–F run on kind and Compose. Boxes 7, 8, and 10 tick on kind after a rewrite. Box 9 is blocked (chart render only), 6 needs 025 H-10, 5 needs Vault. Boxes 1–4 stay: the Terraform work. ADR-005 overrides the Kyverno and cloud-auth bullets. Size L stays (W3-09 to W3-12, W3-17) |
| 039 X-2 | 0/6 | parked | parked | Every box is GPU Operator, vLLM on `simplifier-slm`, or MLflow weights. No GPU workload remains once the SLM is parked, so there is no half to keep. Unpark as `later` only if a self-hosted model returns. Depends on 036 and 038 (W3-16) |

## Ticks

One line per box this session ticks, ready to paste into the issue. Every one is conditional on the rewrite named in the line.

- `037 M-2`, box 1, rewritten to "A pull request starts the eval gate in CI with no manual step" (W3-06): `· Delivered in PoC-8: .github/workflows/poc08-evals.yml (on: pull_request with no paths filter; jobs evals, regression, redteam); pocs/poc-08-cross-cutting-build/tests/test_poc08_c1_ci.py::test_poc08_evals_runs_the_gate_and_the_redteam_set_on_every_supported_engine; run 40919685333 on 0457ae1 (pocs/poc-08-cross-cutting-build/README.md, exit criterion "Offline evals")`
- `037 M-2`, box 2, rewritten to "A change that drops facts fails the gate, and the merge is blocked with a report": `· Delivered in PoC-8: packages/eval-kit/tests/test_eval_kit_evalgate.py::test_eval_kit_evalgate_fails_the_drop_facts_logic_on_every_baseline_target and ::test_eval_kit_evalgate_fails_when_a_known_bad_answer_starts_to_pass; pocs/poc-08-cross-cutting-build/tests/test_poc08_c5_regression.py::test_the_gate_fails_drop_facts_and_passes_normal_on_every_baseline_target; pocs/poc-08-cross-cutting-build/notes/2026-10-29-eval-gate.md (drop-facts 0/50 against 0.780); evals/gate/baseline.json`
- `037 M-2`, box 7 merged with box 4, rewritten to "Eval results are attached to the CI run": `· Delivered in PoC-8: .github/workflows/poc08-evals.yml, upload-artifact steps poc08-eval-reports-<engine> and poc08-eval-reports-drop-facts (run 40919685333)`
- `038 X-1a`, box 7, rewritten to "A test pod with the gVisor RuntimeClass runs and reports the gVisor kernel (on kind; the cloud rerun is part of box 1)": `· Delivered in PoC-5: pocs/poc-05-sandboxed/tests/test_poc05_kind_remote_controls.py::test_h28_remote_runs_on_gvisor_next_to_a_runc_pod; pocs/poc-05-sandboxed/demo/2026-10-09-demo-sandboxed.md sections 2 and 6; pocs/poc-05-sandboxed/notes/2026-10-08-remote-suite.md`
- `038 X-1a`, box 8, rewritten to "Default-deny egress holds on kind: a remote-lane pod reaches no public address and no other pod": `· Delivered in PoC-5: pocs/poc-05-sandboxed/tests/test_poc05_kind_remote_controls.py::test_h19_remote_reaches_no_outside_address and ::test_h30_remote_reaches_no_other_pod; pocs/poc-05-sandboxed/tests/test_poc05_netpol_static.py::test_poc05_netpol_default_deny_in_every_namespace; pocs/poc-05-sandboxed/notes/2026-10-08-remote-suite.md`
- `038 X-1a`, box 10, rewritten to "Each service's virtual key and tool grants are provisioned from code with no manual steps (kind: `seed.sh`)": `· Delivered in PoC-5: deploy/kind/poc05/platform/seed.sh; pocs/poc-05-sandboxed/tests/test_poc05_seed_static.py::test_no_litellm_config_grants_an_mcp_server_to_every_key and ::test_seed_is_idempotent_and_rotates_on_request; pocs/poc-05-sandboxed/tests/test_poc05_kind_tool_gateway.py::test_a_key_the_gateway_did_not_issue_gets_no_tool; pocs/poc-05-sandboxed/demo/2026-10-09-demo-sandboxed.md`

Offline tests cited in this file were run on 2026-11-06:

```
$ UV_NO_SYNC=1 scripts/check_offline.sh <10 test ids from this file> -q -p no:cacheprovider
...........................                                              [100%]
27 passed in 24.73s
```

The ten ids: `test_eval_kit_evalgate.py::test_eval_kit_evalgate_fails_the_drop_facts_logic_on_every_baseline_target` and `::test_eval_kit_evalgate_fails_when_a_known_bad_answer_starts_to_pass`, `test_poc08_c5_regression.py::test_the_gate_fails_drop_facts_and_passes_normal_on_every_baseline_target` and `::test_drop_facts_through_the_cell_fails_the_gate_and_normal_passes`, `test_evaluators.py::test_judge_sends_a_fixed_rubric_at_temperature_zero` and `::test_judge_reports_usage_cost_and_route`, `test_poc05_seed_static.py::test_no_litellm_config_grants_an_mcp_server_to_every_key` and `::test_seed_is_idempotent_and_rotates_on_request`, `test_poc05_netpol_static.py::test_poc05_netpol_default_deny_in_every_namespace`, `test_poc06c_route_switch.py::test_only_the_route_changes_and_every_task_passes_both_times`. The two regression tests and the evalgate tests are parametrized, so 10 ids ran 27 cases. The kind tests (`test_h28`, `test_h19`, `test_h30`, `test_a_key_the_gateway_did_not_issue_gets_no_tool`) and `test_poc08_c1_ci.py::test_poc08_evals_runs_the_gate_and_the_redteam_set_on_every_supported_engine` were checked by `grep -n "def test_name"` and not run here; their evidence is the dated PoC-5 record and the PoC-8 run named next to them.

## Status bullets folded

- `028 S-3`, "Status after PoC-6": out of scope, a fact. The bake-off string matches (`packages/bakeoff/src/bakeoff/tasks.py`) stand in for `facts_kept` in the bake-off only; they are not the score this issue builds. The PoC-6a note says the same (`pocs/poc-06a-bake-off-sidecar-lane/notes/backlog-changes.md`).
- `036 S-7`, "Status after PoC-6": out of scope on the parked issue. PoC-6c uses llama.cpp on `local-small`; the SLM numbers wait on the Mac run (`pocs/poc-06c-pretrained-slm/notes/2026-10-09-mac-command-debt.md`).
- `036 S-7`, PoC-6c "to apply" list, three items (`pocs/poc-06c-pretrained-slm/notes/backlog-changes.md`): the model server is the scale limit; the queue needs a bound; every number is CPU only. All three go into the parked issue's `## What` as "Status after PoC-6c", so they survive an unpark (W3-15). The same file's items for 063 S-8 and 013 CH-2 belong to the W5 and W2a sessions.
- `037 M-2`, "Proposed in PoC-7": decided. DeepEval is the one offline tool (ADR-010 item 1; trial in `pocs/poc-07-cross-cutting-decisions/notes/2026-10-28-eval-tool-trial.md`). The Langfuse dataset upload (`eval_kit.langfuse_datasets`, tested against a stub) is called by no workflow or Makefile target: an open item for phase D, not a box here.
- `037 M-2`, "Status after PoC-8": folds into ticks 1, 2, and 7. "As a required check on `main`" is a repository setting: owner list (011 section 2), not a box. "A real judge's agreement with the labels" and "the label of g08" go to the rewritten 028 S-3 box 5. "No tolerance while the model is scripted" stays a `suggested:` marker (W3-08).
- `031 G-5`, "Open concern (review 2026-09-29)": the break-even must count the evaluator gate. Out of scope while parked; the product's cost question is the judge at rate 0.1, already priced in ADR-010 (about $5 per million requests).
- Inputs never applied: the PoC-7 draft names `037 M-2` and `031 G-5` (the encoder numbers plug into G-5; parked). The PoC-8 backlog-changes entry for `037 M-2` is the "Status after PoC-8" bullet above. The PoC-9 W2–W5 report names none of 027–039.

## Findings

### W3-01 [HIGH] [rule 13] docs/planning/issues/041-A-1-simplifier-core.md
Problem: 041 A-1 depends on 036 S-7 (parked), and 091 R-11 does too. 041 is P0 on the critical path to the first measured savings.
Why it matters: a live issue cannot wait on a parked one; the simplifier core as a workload needs only a route, and `big-default` and `local-small` answer today (`pocs/poc-06c-pretrained-slm/tests/test_poc06c_route_switch.py::test_only_the_route_changes_and_every_task_passes_both_times`, ran).
Fix: drop `S-7` from the `ROWS` dependencies of A-1 and R-11; the W4 and W7 sessions decide whether A-1 keeps `simplifier-slm` in its text or calls a named route from the route map (phase B). If A-1 keeps rewrite rules, they are the golden set's `banned:` and `contains:` fields, not 027 S-1.
Status: open

### W3-02 [HIGH] [rule 14] docs/planning/issues/028-S-3-simplifier-metrics.md
Problem: The product plan's P1 gap "the judge is off in every profile; no real judge numbers" (phase B: cost, latency, and agreement from the Mac run) has no issue that holds the agreement measurement. 074 E-2 is the evaluator-SLM agreement test and depends on 073 E-1 (parked).
Why it matters: phase B's done-when ("the judge's numbers are recorded and the defaults confirmed or changed by ADR-010's rule") has no box that records agreement with people on the 50-case set; `pocs/poc-07-cross-cutting-decisions/notes/2026-10-28-evaluator-gate.md` leaves it open.
Fix: rewritten 028 S-3 box 5 is that home ("the judge's `facts_kept` agrees with the 50 hand labels, agreement reported, from the Mac run"); name it in `phase-map.md` as phase B's agreement box and keep 074 E-2 for a future encoder.
Status: open

### W3-03 [HIGH] [rule 13] docs/planning/issues/030-S-4-hand-review-test-set.md
Problem: 068 D-3 (golden-set import) and 074 E-2 (agreement test) depend on 030 S-4, which is parked, and 074 E-2 also depends on 073 E-1.
Why it matters: both are golden-set and evaluator work the product may keep, but the 300-record held-out set they wait on will not be built.
Fix: in `ROWS`, replace `S-4` with `M-2` (the gate set, number 037) in 068 and 074; the W6 session confirms. Move 030's box 4 (reviewer, date, and reason per label) to 064 D-1 and 065 D-2 as the label metadata the golden set lacks today (the YAML headers name one author and a date, not one per case).
Status: open

### W3-04 [HIGH] [rule 13] docs/planning/issues/028-S-3-simplifier-metrics.md
Problem: Three of S-3's five metrics (token reduction, readability, embedding cosine) exist only to filter and rank SLM training pairs; none exists in code. `facts_kept` and banned words are what the gate and the judge score, and both depend on 027 S-1 (parked).
Why it matters: parking all of S-3 would park 042 A-2's `facts_kept` gate and phase B's agreement box; keeping it whole keeps three metrics no phase needs.
Fix: rewrite the title to "`facts_kept` and banned-word scoring for the eval gate and the judge"; move tokens, readability, and similarity to `## Out of scope` as "parked with the SLM track"; rewrite box 1 to "One command scores a golden case file", box 3 to "banned words use the case's `banned:` list", box 6 to "the judge route and rubric version are in the report" (`test_judge_reports_usage_cost_and_route` covers the route, not the version: tick-blocked); drop the `S-1` dependency in `ROWS`; phase B; resize S from the 4 live boxes (box 2 `facts_kept` fixture test, box 4 repeat-run test, box 5 agreement, box 6).
Status: open

### W3-05 [HIGH] [rule 13] docs/planning/issues/037-M-2-eval-gate-ci.md
Problem: 037 M-2 is written for "a new model version registered in MLflow", and its dependencies are 028 S-3, 030 S-4 (parked), 032 M-1 (parked), and 020 X-8 (the broker, for a `models.model.promoted.v1` event nothing defines in code or contracts).
Why it matters: the gate as built is a repo gate on a change (DeepEval, `evals/gate/baseline.json`, `poc08-evals`), and the issue cannot close while three of its dependencies are parked or `later`.
Fix: retitle "Eval gate in CI: a change ships only if it passes the golden set against the baseline"; rewrite `## Why`; box 4 (MLflow and the event) is moot, merge into 7; `ROWS` dependencies become `["028 S-3"]` (the floor in box 3 needs the score); phase B (the gate set and the judge's defaults); resize S from the 4 open boxes (3, 5, 6, the agreement run owned by 028).
Status: open

### W3-06 [MEDIUM] [rule 1] docs/planning/issues/037-M-2-eval-gate-ci.md
Problem: Boxes 1, 2, and 7 are met by a CI workflow and a regression test, but are worded for a model registry, so none can be ticked as written.
Why it matters: the gate is the PoC-8 offline-evals criterion, which its README leaves unticked only for the repository setting; the issue shows 0 of 7.
Fix: rewrite boxes 1, 2, and 7 as in Ticks and tick them with the PoC-8 evidence; the PoC-8 README's `poc08-evals` run 40919685333 is the CI record (the eval-gate note itself has no run id).
Status: open

### W3-07 [MEDIUM] [rule 1] docs/planning/issues/037-M-2-eval-gate-ci.md
Problem: Box 3 ("a candidate below `facts_kept` 0.95 fails, even with no current model") has no code: `evalgate.py` compares each target to its recorded pass rate (0.780 in `evals/gate/baseline.json`), and no 0.95 exists. A scripted model can never reach 0.95 on the 10 fail-labeled cases.
Why it matters: the box is unreachable as written and hides the real decision, an absolute floor for a hosted model.
Fix: rewrite to "a pass rate below the baseline floor fails, with or without a previous run" (the `must_pass` list already does most of this; tick-blocked by a test `test_a_run_below_the_floor_fails_with_no_previous_report`); the absolute floor for a hosted judge is the phase B decision and goes to the DEC-1 pack; keep `suggested: no tolerance on the pass rate while the model is scripted` as is.
Status: open

### W3-08 [MEDIUM] [rule 1] docs/planning/issues/037-M-2-eval-gate-ci.md
Problem: Box 5 (a prompt change runs the same gate) and box 6 (another dataset version needs a config change only) are open. The workflow runs on every pull request, so a prompt change triggers it, but the gate uses a scripted model, so a prompt edit cannot change the result; `evalgate` has no dataset option (`GATE_FILE` and `GATE_SIZE = 50` are fixed in `eval_kit/golden/__init__.py`; `GOLDEN_CASES` is read only by `evals/deepeval/test_golden.py`).
Why it matters: phase D points LangSmith datasets at the 20 golden cases; that needs the gate to take a dataset by config.
Fix: keep both open. Name the proving tests: `test_a_changed_prompt_changes_the_gate_input` and `test_evalgate_reads_the_dataset_named_in_the_baseline`.
Status: open

### W3-09 [MEDIUM] [rule 4] docs/planning/issues/038-X-1a-terraform-first-cloud.md
Problem: Five issues depend on 038 X-1a: 039 X-2, 055 CH-6, 116 X-1b, 117 X-3, and 121 X-4. 117 X-3 is phase E (SLOs and alerts); 055 CH-6 is the remote lane and trust rule.
Why it matters: X-1a is `later` (the first cloud is a P3 open decision), so phase E would wait on a cloud, and 117 X-3 is named in the product plan's phase E row.
Fix: drop `X-1a` from 117 X-3 (SLOs run on the Compose and kind stacks with Prometheus) and from 055 CH-6 (kind proves gVisor, the CNI, and admission); keep it for 116 X-1b and 121 X-4. The W5 and W1012 sessions confirm.
Status: open

### W3-10 [MEDIUM] [rule 14] docs/planning/issues/038-X-1a-terraform-first-cloud.md
Problem: 038 has no phase: the product plan puts the first cloud under "open decisions" (P3), and phases A–F run on kind and Compose.
Why it matters: without a phase it counts as work the plan needs.
Fix: phase `later`, size L stays (boxes 1–4, 5, 6, and the cloud rerun of 7 and 8 are open: Terraform for a real cluster is 4 to 5 days); no split, since one owner holds the module.
Status: open

### W3-11 [MEDIUM] [rule 1] docs/planning/issues/038-X-1a-terraform-first-cloud.md
Problem: Boxes 7, 8, and 10 have kind evidence from PoC-5 but are worded for a cloud; box 9 has a chart render only; boxes 5 and 6 have none. No box can be ticked as written.
Why it matters: three finished results show as 0 of 10, and 026 CH-4 and 055 CH-6 already rest on the same evidence.
Fix: rewrite and tick 7, 8, and 10 as in Ticks. Box 9 (two containers from the library chart) is `tick-blocked`: `pocs/poc-09-agent-mvp-template/tests/test_poc09_chart.py::test_sidecar_lane_renders_one_pod_with_the_chassis_and_the_workload` renders only; the proving test is a kind test that installs `deploy/helm/example-agent` and reads two ready containers (suggested: `test_the_chart_pod_runs_chassis_and_workload`). Box 6 (unsigned images) waits on 025 H-10; box 5 (Vault, External Secrets) has only the kind `seed.sh` stand-in and stays open.
Status: open

### W3-12 [MEDIUM] [rule 5] docs/planning/issues/038-X-1a-terraform-first-cloud.md
Problem: Three `## What` bullets are overridden or narrowed: Kyverno among the add-ons (ADR-005 item 4 chose a ValidatingAdmissionPolicy and "no Kyverno"; Kyverno stays a candidate only for H-10's signature check), the `remote` lane's cloud auth adapter with Workload Identity or IRSA (ADR-005 item 1 picks one bearer token per remote; ADR-006 puts managed runtimes out of scope), and the `terraform apply` box for add-ons that ADR-007 and ADR-008 do not use.
Why it matters: an engineer would build Kyverno and an auth adapter the product does not use.
Fix: add "Decided in ADR-005 (items 1, 4) and ADR-006" and replace Kyverno with "the ValidatingAdmissionPolicy and its parameter file (`deploy/kind/poc05/admission/`)"; drop the IRSA bullet; keep Argo CD, Vault, and CloudNativePG, which no ADR touches.
Status: open

### W3-13 [MEDIUM] [rule 13] docs/specs/metrics.md
Problem: The counter-metrics table sets the target for "Cost per good task" to "the G-5 break-even" (line 32), and G-5 is parked.
Why it matters: a live phase E metric then waits on a parked cost model.
Fix: the product plan's PR replaces it with "set from the first week of real numbers (phase E)" and the judge's $5 per million; this is a spec edit, not a planning edit, so it goes to `proposals.md`.
Status: proposal

### W3-14 [MEDIUM] [rule 13] docs/planning/issues/123-C-4-documentation-pack.md
Problem: 123 C-4 (model card per agent version, W11, `later`) depends on 032 M-1 (parked).
Why it matters: a `later` issue still may not depend on a parked one.
Fix: drop `M-1` in `ROWS`; the model card's source becomes the manifest and the response `Versions` (chassis, config, prompt, model_route; `packages/chassis/src/chassis/core/envelope.py`); the W1012 session rewrites the box.
Status: open

### W3-15 [MEDIUM] [rule 10] docs/planning/issues/036-S-7-serve-with-vllm.md
Problem: The PoC-6c "to apply" list still has no "Status after PoC-6c" bullet: the model server, not the agent pods, is the scale limit (throughput stops at 2 pairs, about 2.3 to 2.4 ok requests per second on 4 slots); a request that waits for a slot needs a bound (13 of 124 failed at p95 30000 ms); all numbers are CPU on a shared 4-CPU runner.
Why it matters: these are the sizing facts for the day the SLM track unparks, and `036 S-7` is the only issue that holds them.
Fix: add one "Status after PoC-6c" bullet with the three facts and the records `pocs/poc-06c-pretrained-slm/notes/2026-10-28-scale-run.md` and `2026-10-28-slm-run.md`; keep the issue parked. Box 1 in `## Acceptance criteria` names `simplifier-slm`; do not edit it while parked.
Status: open

### W3-16 [MEDIUM] [rule 13] docs/planning/issues/039-X-2-gpu-setup.md
Problem: Every box of 039 X-2 is GPU Operator, vLLM on `simplifier-slm`, or MLflow weights. The brief allowed `later`; there is no non-SLM half to keep.
Why it matters: a `later` label would keep a GPU cluster task alive with no workload that needs a GPU.
Fix: `parked`; unpark as `later` only if a self-hosted model returns (for example a judge that must run in the cluster). It stays on 038 and 036 in `ROWS`.
Status: open

### W3-17 [MEDIUM] [rule 13] docs/planning/issues/038-X-1a-terraform-first-cloud.md
Problem: Four SLM remnants sit in 038: the `## Why` ("the simplifier needs a real environment to show savings"), the MLflow artifacts and datasets buckets, the GPU node pool with a minimum of zero, and the 039 X-2 block.
Why it matters: they make the first-cloud work look tied to the SLM track.
Fix: rewrite `## Why` to the cluster contract; move the GPU node pool and the MLflow bucket to `## Out of scope` as "parked with the SLM track".
Status: open

### W3-18 [MEDIUM] [rule 13] docs/planning/issues/034-C-8-training-compute-log.md
Problem: C-8 is parked because nothing is fine-tuned, but the GPAI duty (one third of the base model's training compute) starts with the first fine-tune.
Why it matters: unparking 033 S-5 without 034 would run a fine-tune with no compute record.
Fix: add to 033's and 035's parked notes: "unpark 034 C-8 first"; 034 stays parked and keeps its dependency on 033.
Status: open

### W3-19 [LOW] [rule 6] docs/planning/issues/031-G-5-cost-model-break-even.md
Problem: Parking the SLM track leaves the `suggested:` markers in 027–036 (sample sizes, tolerances, a hyperparameter sweep, a pass rule) unsettled and unowned.
Why it matters: the DEC-1 pack should not carry them; none blocks W1 to W4 for a live issue.
Fix: leave them in the parked bodies; list none for the DEC-1 pack. Only 037's tolerance on a hosted model needs an owner (W3-07).
Status: deferred (the markers sit in parked issues)

### W3-20 [LOW] [rule 4] docs/planning/issues/047-M-3-shadow-mode.md
Problem: 047 M-3 and 049 M-5 depend on 037 M-2. M-3 shadows a new model against the current one; M-5 evaluates quantized SLM variants.
Why it matters: 049 M-5 is SLM-only (the rubric lists it as likely parked), and 047 M-3 may be, once no new model is trained; both depend on a live issue, which is fine, but 037 no longer does the registry work they assume.
Fix: the W4 session decides 047 and 049; 037's rewritten text says "no registry" so they can see why.
Status: open

### W3-21 [LOW] [rule 10] docs/planning/issues/037-M-2-eval-gate-ci.md
Problem: The bullet "Proposed in PoC-7 ... the pick is added when PoC-7 closes" is stale: ADR-010 item 1 chose DeepEval, and `evals/promptfoo` is parked in the product plan's "What is reused".
Why it matters: a reader thinks the tool is still a trial.
Fix: replace the bullet with "Decided in ADR-010 item 1: DeepEval"; keep the Langfuse dataset sentence as a phase D hand-off.
Status: open
