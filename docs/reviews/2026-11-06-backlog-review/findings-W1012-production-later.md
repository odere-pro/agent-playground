# Findings W1012: W10 Production readiness, W11 Compliance evidence, W12 Later

Session: W1012. Issues: 116–127. Waves: W10, W11, W12.
Rubric: [rubric.md](rubric.md).

## Summary

The PoC track built the inputs of these issues and none of the issues. It built the metrics, the per-agent dashboard generator, a Compose Prometheus and Grafana stack, the sidecar measurement on Docker Desktop, and the AI-marking contract. It built no alert rule, no `slo` block, no cloud module, no backup, no runbook beyond the PoC-5 set, no documentation pack, and no control map. So nothing retires and no box ticks; one box (122 box 7) is `tick-blocked`. Three issues are live: 122 X-5 in phase A (after a split), and 117 X-3 and 120 X-7 in phase E (the latter after a split). Eight are `later` (116, 118, 119, 121, 123, 124, 125, 126) and 127 L-3 is `parked`. The top risk is phase E: the product plan's done-when ("one dashboard with one week of real numbers") has no issue that holds the container dashboard or the weekly record, and 117 waits on a cloud (038) and release rings (056) it does not need.

## Issues

`Boxes` is met over total after the review's ticks. A split row names the live half; the other half is a new issue, number assigned by the coordinator.

| Issue | Boxes | Phase | Verdicts | Evidence or reason |
| ----- | ----- | ----- | -------- | ------------------ |
| 116 X-1b | 0/8 | later | drift, moot, parked, deps | No `*.tf` file exists. The product plan names only an open first cloud (P3); no phase needs a second. Box 6 (a managed-runtime auth adapter) is moot: ADR-005 line 78 says `sigv4` and `google` are "not built", ADR-006 puts managed runtimes out of scope. Kyverno is overridden by ADR-005 item 4. GPU node pools and the GPU Operator are the parked half of 039 X-2. Size L stays (W1012-07, -08) |
| 117 X-3 | 0/7 | E | resize M, deps, tick-blocked, split | Named in the product plan's phase E row. Built inputs only: `packages/chassis/tests/test_metrics.py::test_gated_runs_and_fallbacks_are_both_counted`, `packages/chassis/tests/test_dashboards.py::test_every_panel_names_a_metric_the_chassis_exports`. No rule file, no Alertmanager, no `slo` schema key, no chassis-version label. Drop deps on 038 and 056; move the ring and cloud bullets out; add the container-dashboard and one-week boxes (W1012-02, -03) |
| 118 C-7 | 0/5 | later | keep, deps | `events.v0.json` has no `incident` type; no audit agent (072) exists. Needs 117 first. Adding the event type is a contract change (W1012-17) |
| 119 X-6 | 0/4 | later | keep, resize M | `docs/guides/poc-05-runbooks.md` (11 sections) covers a leaked remote token, an admission refusal, and a stuck claim; none of the listed failures has a five-part runbook (no rollback or escalation parts). Phase E does not list runbooks. Prune the GPU and Temporal items (W1012-18) |
| 120 X-7 | 0/6 | E | split, deps, parked, resize S | Per-agent "Model cost" panel and `chassis_model_cost_microusd_total` exist (`packages/chassis/tests/test_metrics.py::test_model_tokens_and_cost_come_from_the_proxy_usage`). Split into X-7a (cost per agent and per good task, sidecar line; phase E, depends on 004 only) and X-7b (per-pool hosting cost; later, needs 104 O-3, which the product plan marks `later`). Rates come from 031 G-5 and the baseline from 005 G-3, both parked (W1012-06) |
| 121 X-4 | 0/6 | later | deps, moot, drift | Nothing delivered: no CloudNativePG, backup, restore, object lock, or lakeFS in `deploy/`, `packages/`, or `pocs/`. MinIO bucket versioning is on (`deploy/compose/docker-compose.scale.yaml:212`, `deploy/kind/poc05/platform/minio.yaml:105`) and is not a backup. Box 6 ("both clouds") needs 116 and lacks the dependency; the golden-set half needs lakeFS (069), but the golden sets live in git (W1012-11) |
| 122 X-5 | 0/8 | A | split, resize M, deps, tick-blocked | Split into X-5a (phase A: the PoC-4 criterion 4 rerun at 4 pairs on Linux, sidecar p95, chart requests) and X-5b (later: pool, registry, and event tests). No KEDA, no hybrid search, no registry load test exists. Box 7 is `tick-blocked` (W1012-01, -05, -12) |
| 123 C-4 | 0/6 | later | deps, parked, drift, cite | No generator exists. Depends on 032 M-1 (parked). Technical documentation and instructions for use need no MLflow; the model card does as written. `/manifest` already serves the OpenAPI sha256. AsyncAPI is not built (052). `GovernanceSpec` has only `risk_class` and `ai_generated_marking` (W1012-09, -10) |
| 124 C-9 | 0/5 | later | cite, keep | Nothing built. Art. 50(2) evidence now exists and can be listed as "covered by the platform" (W1012-10). Depends on 072 and 123, both `later`. The OWASP agentic list (phase E) is not this issue's table and has no home (W1012-04) |
| 125 L-1 | 0/5 | later | resize M, cite, keep | Plan only. `context_ref` exists in the envelope (`packages/chassis/src/chassis/core/envelope.py:61,77`; `docs/specs/contract.md`: "accepted and passed through; unused"). Layer 1 assumes Temporal; the product has no Temporal and phase C owes a checkpointer ADR first (W1012-15) |
| 126 L-2 | 0/5 | later | deps, parked, cite, resize M | Plan only. Depends on 075 E-3 (the encoder judge, parked with `EncoderEvaluator`); the keep rule names a `facts_kept` floor of 0.95 that no code has (W1012-14) |
| 127 L-3 | 0/5 | parked | parked | Named by the product plan as parked (the router SLM). A trained classifier on logged runs. Nothing live depends on it (W1012-16) |

## Ticks

None. No box in 116–127 has plain evidence under rule 1.

Boxes that are partly met, each `tick-blocked`, with the test that would prove it:

- `122 X-5`, box 7 (sidecar p95 CPU and memory, hop p50 and p95, next to ADR-001): the note `pocs/poc-04-stateless-scalable/notes/2026-10-01-load-results.md` gives CPU and memory as a mean and a max, and the hop at p50 1.0 ms and p95 0.0 ms below the method's resolution (`pocs/poc-04-stateless-scalable/tests/test_load_results.py::test_the_sidecar_hop_is_recorded` checks that the numbers are recorded, not that they are p95 or isolated). The proving test would assert a recorded p95 for CPU and memory from a Linux run (suggested: `test_the_sidecar_p95_cpu_and_memory_are_recorded`) and a chassis-side hop timer.
- `117 X-3`, box 4 (fallback above 10% fires an alert): the counter and the panel exist; no rule. Proving test: a Compose drill (suggested: `test_a_fallback_rate_above_ten_percent_fires_the_alert`).
- `117 X-3`, box 6 (dashboard shows SLOs and error budget): `test_every_panel_names_a_metric_the_chassis_exports` proves the seven panels, none is an SLO or budget panel. Proving test: the same generator test over an SLO panel set.
- `120 X-7`, box 1 (cost per agent): the per-hour panel exists; no day, week, or month windows and no per-pool cost. After the split, X-7a box 1 can tick once the windows are added.

Offline tests cited in this file were run on 2026-11-06:

```
$ UV_NO_SYNC=1 scripts/check_offline.sh <10 test ids> -q -p no:cacheprovider
21 passed, 4 xfailed in 5.32s
```

The ten ids: `test_marking.py::test_limited_marks_every_interface_streaming_and_complete` and `::test_marking_false_is_refused_for_limited_and_high`, `test_marking_body.py::test_the_openai_body_carries_the_flags_complete_and_on_every_chunk` and `::test_result_events_carry_the_aigenerated_extension`, `test_metrics.py::test_gated_runs_and_fallbacks_are_both_counted` and `::test_model_tokens_and_cost_come_from_the_proxy_usage`, `test_dashboards.py::test_every_panel_names_a_metric_the_chassis_exports` and `::test_the_generated_files_are_up_to_date`, `test_load_results.py::test_the_sidecar_hop_is_recorded` and `::test_throughput_grows_with_pairs`. The parametrized ones ran 25 cases; the 4 xfails are the strict PoC-4 exceptions (`echo-python-1to2`, `echo-python-2to4`, `echo-langgraph-2to4`, `echo-typescript-2to4`). The other test names in this file were checked with `grep -n "def test_name"` and not run.

## Status bullets folded

- `122 X-5`, "Status after PoC-4": facts into the open boxes of X-5a. The CPU figures (0.105 to 0.129 vCPU at 10 RPS, at or above the top of 0.05–0.1; memory 150 to 182 MiB, inside 128–256) become the "before" numbers in the sidecar box. The Revisit-rule input ("at or above the top of the range") goes to the owner list (011 section 3, PoC-4 items), not a box. "This issue still sets the chart's requests from a cluster p95 and isolates the hop" stays as open boxes. The throughput rerun becomes X-5a box 1.
- `122 X-5`, "Status after PoC-9": out of scope, a fact. `LoadSmoke` (`packages/agent-testkit/src/agent_testkit/suites.py::LoadSmoke.test_concurrent_runs_all_complete`, 24 calls) is a smoke test and does not close any box; move it to `## Out of scope` as "not a substitute".
- Inputs never applied: the PoC-7 draft (`pocs/poc-07-cross-cutting-decisions/notes/backlog-changes.md`) names none of 116–127. The PoC-6c "to apply" list names none. The PoC-9 W2–W5 report names none. The PoC-4 backlog-changes file names `122 X-5` twice (criteria 4 and 11); both are the bullets above. PoC-8's file names none of these issues; its open questions Q1 (`/metrics` open by default) and Q4 (the feedback panel) touch 117's dashboard and go to DEC-1.
- 011 section 4, "The rare 502 under load (6a follow-up)", about 1 in 4,600 calls at 4 pairs: no issue owns it. It belongs in X-5a as an optional box or `## Out of scope` (W1012-19).

## Findings

### W1012-01 [HIGH] [rule 9] docs/planning/issues/122-X-5-load-tests.md
Problem: 122 mixes the phase A job (rerun PoC-4 criterion 4 on Linux) with W10 work that needs KEDA pools (104 O-3) and hybrid search (091 R-11), neither of which exists. The product plan's phase A row names "throughput at 4 pairs on Linux (122 X-5)", and its done-when is "every PoC-1 to PoC-5 flag has plain evidence".
Why it matters: phase A would wait on 091 and 104, which are W7 and W9 work; the PoC-4 flag stays open until the whole of 122 runs.
Fix: split. X-5a keeps the number 122, phase A, size M, no dependencies. Its open boxes are exactly the done-when: (1) "Throughput grows with replicas for every engine on a Linux host with pinned CPUs, numbers recorded; the four strict xfails in `pocs/poc-04-stateless-scalable/tests/test_load_results.py::test_throughput_grows_with_pairs` (`RECORDED_EXCEPTIONS`) pass and the list is emptied; PoC-4 criterion 4 is ticked in its README" (the PoC-4 criterion reads "Throughput grows with replicas for every engine (numbers recorded)"); (2) "The report gives the sidecar's p95 CPU and memory and the local hop at p50 and p95, isolated by a chassis-side timer, next to ADR-001's 0.05–0.1 vCPU, 128–256 MiB, and 1–3 ms"; (3) "The library chart's resource requests (`deploy/helm/agent-lib`) are set from that p95, and the report says whether the ADR-001 Revisit trigger is met (suggested: more than twice the upper estimate)". X-5b (new issue, later, size M) takes the pool, registry, event-burst, and top-3 boxes, with dependencies 091, 104, and 117.
Status: open

### W1012-02 [HIGH] [rule 14] docs/planning/issues/117-X-3-slos-alerts.md
Problem: The product plan's phase E done-when is "the north star, inputs, and counters are on one dashboard with one week of real numbers". 117's six boxes are about alerts on staging and on-call; none says the SLO targets and the remaining budget show on the container dashboard, and none asks for a week of numbers. `docs/specs/metrics.md` gives 117 the targets "the X-3 error-rate SLO" (task success) and "below 10% (117 X-3)" (gate fallback and fail-open), and says "SLOs and burn-rate alerts on availability, p95, error rate, and the gate fallback rate are 117 X-3".
Why it matters: closing 117 as written would not close phase E.
Fix: rewrite the open boxes to run on Compose and kind: (1) "A newly deployed agent gets SLO rules (availability, p95, error rate, fallback rate) generated from an `slo` block with chart defaults, no manual step"; (2) "A fault test on the Compose stack (`deploy/compose/poc08`, a fake agent returning errors) fires the burn-rate error alert and it reaches the Alertmanager receiver"; (3) the p95 latency fault test; (4) "a gate fallback rate above 10% fires an alert" (the counter and panel exist); (5) "an alert carries the chassis version from a `chassis_build_info` metric" (no such label exists today); (6) "each agent dashboard and the container dashboard show each SLO target and the remaining error budget"; (7) new: "one week of real SLO numbers is recorded in a weekly note (`record-measurement`) next to the north star". Size M stays: five bullets leave (see W1012-03) and two boxes join. Phase E.
Status: open

### W1012-03 [HIGH] [rule 4] docs/planning/issues/117-X-3-slos-alerts.md
Problem: 117 depends on 038 X-1a (the first cloud, `later`) and 056 CH-7 (release rings). Only the "same rules in the cloud" bullet needs the first; only three bullets need the second (SLOs per ring, the minimum-version alert, and the canary in box 5). W3-09 already proposes dropping 038 here.
Why it matters: a phase E issue cannot wait on a `later` cloud; 056 has no code (no ring or minimum-version code outside PoC-5 notes), so rings would hold phase E too.
Fix: in `ROWS` set 117's dependencies to `["014 H-7"]` (the metrics and dashboards it builds on). Move the per-ring, minimum-version, and `remote`-endpoint alert bullets into 056 CH-7 (the W5 session decides), and the broker-lag, dead-letter, and Temporal-run alerts into the issues that add those components (019 H-17, 100 O-2). Change "staging" to "the Compose stack" in boxes 1–6. Confirms W3-09.
Status: open

### W1012-04 [HIGH] [rule 14] docs/plans/2026-11-06-agent-container-product.md
Problem: Phase E has four named items with no issue: the container dashboard (`deploy/dashboards/container.json`, three rows), the weekly record, the guard-stage span, and the OWASP agentic list (ASI01–ASI10) mapped to the controls. Sampling in the Collector has none either. 117 holds only SLOs and alerts. The `docs/specs/security.md` file has no OWASP table.
Why it matters: the P2 gap rows "no container dashboard, no weekly record" and "OWASP list not mapped" have no home, so phase E has nothing to close them.
Fix: add one new issue in phase E (suggested: "X-9 Container dashboard and weekly record", size S, depends on 117 and 120 X-7a, generated by `python -m chassis.dashboards`, with the weekly note as its last box). Give the OWASP mapping to a second new issue (suggested size S) or to a section in `docs/specs/security.md`; 124 C-9's AI Act table is the sibling, but 124 is `later` and the OWASP map is phase E. The guard-stage span and sampling belong to the observability work outside W10–W12; I list them so the coordinator does not lose them.
Status: open

### W1012-05 [HIGH] [rule 4] docs/planning/issues/122-X-5-load-tests.md
Problem: Boxes 2 and 3 say "meets its p95 latency SLO" and the body says "Pass or fail against the X-3 SLOs", but `depends_on` is `["091 R-11", "104 O-3"]`. 117 is lower-numbered, so the dependency is allowed.
Why it matters: the criteria depend on an issue the `ROWS` do not list; a reader would start X-5b before SLOs exist.
Fix: X-5b gets `["091 R-11", "104 O-3", "117 X-3"]`. X-5a needs none.
Status: open

### W1012-06 [MEDIUM] [rule 9] docs/planning/issues/120-X-7-cost-dashboard.md
Problem: 120 depends on 004 G-2 and 104 O-3, and its body takes hosting rates from 031 G-5 (parked) and compares with 005 G-3 (parked, per W3). Its per-pool boxes (3, 5, and the pool half of 1) need KEDA pools that do not exist and that the product plan marks `later` ("HPA on CPU only", 104 O-3).
Why it matters: cost per good task (`docs/specs/metrics.md`, phase E) needs only the per-agent cost the chassis already counts, but the issue as written waits on pools.
Fix: split. X-7a (keeps 120, phase E, size S, depends on 004 G-2 only): cost per agent and per good task for day, week, and month on the container dashboard; API cost matches the model proxy's counts; the sidecar's cost line from X-5a's p95 and the configured rates; rates as config, not from G-5. Box 4 (the baseline) becomes "next to the first week's number" until 005 G-3 returns. X-7b (new, later, size S, depends on 104): per-pool hosting cost, scale-to-zero, replica-hours. Add 122 X-5a as a dependency of X-7a for the sidecar line.
Status: open

### W1012-07 [MEDIUM] [rule 5] docs/planning/issues/116-X-1b-terraform-second-cloud.md
Problem: Three `## What` bullets are overridden: Kyverno among the add-ons (ADR-005 item 4: a ValidatingAdmissionPolicy, "no Kyverno"; Kyverno is reopened only for signatures or mutation), the `remote` lane's cloud auth adapter and IRSA or Workload Identity (ADR-005 line 78 "`sigv4`, `google` are not built"; ADR-006 line 91 "no cloud runtime and no cloud auth adapter"), and the claim "055 CH-6 built the first cloud's adapter" (055 built the bearer-token connector, the admission policy, and gVisor on kind).
Why it matters: an engineer would build an auth adapter and a Kyverno install the product does not use, and box 6 can never be met.
Fix: add "Decided in ADR-005 (items 1, 4) and ADR-006"; replace Kyverno with the ValidatingAdmissionPolicy from `deploy/kind/poc05/admission/`; delete the managed-runtime auth bullet and box 6 (moot); keep the gVisor and CNI bullets. Same fix as W3-12 for 038.
Status: open

### W1012-08 [MEDIUM] [rule 13] docs/planning/issues/116-X-1b-terraform-second-cloud.md
Problem: 116 lists GPU node pools on spot nodes, a model-weights bucket, and the NVIDIA GPU Operator. These are the GPU half of 039 X-2, which W3 parks.
Why it matters: the module would carry GPU resources for a workload that is parked.
Fix: remove the GPU pools, the weights bucket, and the GPU Operator from `## What`, and keep them as "Out of scope: parked with the SLM track". Phase `later`; size L stays (cluster, buckets, networking, IAM, add-ons, environments, and the contract check are still 4 to 5 days).
Status: open

### W1012-09 [MEDIUM] [rule 13] docs/planning/issues/123-C-4-documentation-pack.md
Problem: 123 depends on 032 M-1 (MLflow, parked) and its model card takes base model, data version, golden-set scores, and training compute (034 C-8, parked) from MLflow. Nothing is trained. W3-14 already proposes sourcing the card from the manifest and `Versions`.
Why it matters: a `later` issue still shows a parked dependency, and the pack could be built today for the sections that need no MLflow.
Fix: drop `032 M-1` from `ROWS`; rewrite the model card to "the route map entry (`spec.models`), the provider, the golden-set scores from `evals/gate/baseline.json`, and the chassis version from `Versions`" (suggested); drop "training compute" and the ML-BOM item; keep 051 H-13 (the manifest serves the OpenAPI sha256: `packages/chassis/tests/test_manifest.py::test_manifest_matches_the_config_and_the_openapi_spec`). Keep 052 H-21: no AsyncAPI file exists. Box 3 ("no `governance` block fails the build") needs more than `GovernanceSpec` has: today it holds `risk_class` and `ai_generated_marking` only (`packages/chassis/src/chassis/server/config.py:383`), so the intended purpose, owner, and impact-assessment fields in the box have no source until 078 R-16 or the config schema adds them. Phase `later`; size M stays.
Status: open

### W1012-10 [MEDIUM] [rule 1] docs/planning/issues/124-C-9-control-mapping-report.md
Problem: The AI Act Art. 50(2) note in `000-plan.md` ("Why the order differs", item 5) says marking "applies to systems already on the market from 2 December 2026". PoC-8 built most of what it asks, but the note claims more than the code shows: marking covers the four public run routes (`RUN_PATHS = ("/v1/run", "/v1/chat/completions", "/v1/messages", "/v1/mcp")` in `packages/chassis/src/chassis/server/marking.py:47`), on complete, streamed, and refused responses; a body field `ai_marking` on the OpenAI and Anthropic formats only; and the `aigenerated` CloudEvent extension. No A2A route is in `RUN_PATHS` (the public A2A endpoint is an open decision, 061 H-5), and the native envelope and `/v1/mcp` carry headers only.
Why it matters: 124 must list Art. 50(2) as "covered by the platform" with evidence that opens, and 123's instructions for use must state the marking and the disclosure flag; the evidence exists and is not cited. The date is four weeks away; I did not check the legal text, and PoC-8 leaves "legal on disclosure for `minimal` with marking on" open (`pocs/poc-08-cross-cutting-build/notes/backlog-changes.md` line 57).
Fix: cite for 124 (Art. 50(2) row): contract anchor `docs/specs/contract.md`, "AI-generated marking (`server/marking.py`, `adapters/ai_marking`)" in section 8 (also `docs/contracts/contract-v7.md`, "E. AI-generated marking"), and the tests `packages/chassis/tests/test_marking.py::test_limited_marks_every_interface_streaming_and_complete`, `::test_marking_false_is_refused_for_limited_and_high`, `packages/chassis/tests/test_marking_body.py::test_the_openai_body_carries_the_flags_complete_and_on_every_chunk`, `::test_result_events_carry_the_aigenerated_extension` (ran, passed). Mark A2A and the native-envelope body as the listed gap. 023 C-5's own open boxes (the client guide, the contract-suite case, the overwrite shown for headers only) belong to the W4 session; the 2 December date makes them time-bound, so I raise it for the coordinator. 124 stays `later`; the marking itself is 023's work, not this report's.
Status: open

### W1012-11 [MEDIUM] [rule 4] docs/planning/issues/121-X-4-backups-restore.md
Problem: Box 6 says "the restore test passes on staging on both clouds" and the Why says "both clouds (116 X-1b)", but `depends_on` is `["038 X-1a", "069 D-7"]`. 069 D-7 is lakeFS; golden sets in the product are YAML in `packages/eval-kit` under git, so the lakeFS half is moot.
Why it matters: a missing dependency, a dependency on lakeFS the product does not run, and a box that cannot be met until 116.
Fix: reword box 6 to "on the first cloud" and drop "both clouds" from the Why, or add `116 X-1b`; drop `069 D-7` and the golden-set and lakeFS bullets (the golden set restores from git). Keep 038. Phase `later`. Size M stays: Postgres PITR, bucket copy, schedule, and the restore test are about 2 to 3 days.
Status: open

### W1012-12 [MEDIUM] [rule 1] docs/planning/issues/122-X-5-load-tests.md
Problem: Box 7 looks met by the PoC-4 numbers but is not: CPU and memory are recorded as mean and max, the hop is below the method's 1 ms resolution and "not isolated", and the run was on Docker Desktop. `pocs/poc-06a-bake-off-sidecar-lane/notes/2026-10-28-sidecar-load-in-ci.md` is a Linux data point on a 4-CPU runner, but it covers two engines and 4 pairs do not fit 4 CPUs (the same starvation).
Why it matters: ticking box 7 would hide that the flagged criterion is open.
Fix: leave it open as `tick-blocked` (see Ticks). Name the Linux host as an owner-list item (011 section 3): the rerun needs a host with enough cores for 8 chassis and workload containers at 1 vCPU each plus Traefik, Valkey, MinIO, and the model server; `make load-test` and `python -m load_tools.run_matrix --engine X` run it.
Status: open

### W1012-13 [MEDIUM] [rule 13] Live issues that depend on a parked issue
Problem: The list for 116–127: (1) 123 C-4 → 032 M-1 (parked); 123 is `later`. (2) 126 L-2 → 075 E-3 (the encoder judge; `EncoderEvaluator` is parked in the product plan); 126 is `later`. (3) 120 X-7, phase E, names 031 G-5 and 005 G-3 (both parked) in its body, not in `ROWS`. (4) 116 X-1b carries the GPU half of 039. No issue in 116–127 depends on 127 L-3, and no other issue depends on any of 116–127 except 118 and 119 on 117, and 124 on 123.
Why it matters: 120 is live in phase E, so its two body references to parked work need removing.
Fix: 123: drop 032 (W1012-09). 126: drop 075 (W1012-14). 120: X-7a takes rates from config (W1012-06). 116: remove the GPU items (W1012-08). 117 and 122a end with no parked dependency.
Status: open

### W1012-14 [MEDIUM] [rule 13] docs/planning/issues/126-L-2-system-prompt-improver.md
Problem: 126 depends on 070 D-4 and 075 E-3. Its tagger is "an SLM", and its keep rule says "`facts_kept` never below 0.95 through the eval gate (037 M-2)". No `0.95` exists in `packages/eval-kit`, `packages/bakeoff`, or `evals/`; the gate compares each target to its recorded pass rate (0.780 in `evals/gate/baseline.json`; `packages/eval-kit/src/eval_kit/evalgate.py:183`); `facts_kept` is not an `eval-kit` metric. `prompts.system_ref` appears in no code or spec.
Why it matters: the keep rule rests on a floor that does not exist (W3-07 shows the same for 037 box 3) and on a parked SLM.
Fix: tagger = the cheap judge route (`decision-small`, behind `EvaluatorPort`) or a reviewer edit, not an SLM; keep rule = "beats the current prompt on the golden set and never falls below the baseline floor, through the eval gate as W3 rewrites it", with the absolute floor left to the DEC-1 pack; replace `prompts.system_ref` with "a prompt file in the config store, pinned per run" as the design note's choice; drop `075 E-3`. Phase `later`.
Status: open

### W1012-15 [MEDIUM] [rule 14] docs/planning/issues/125-L-1-shared-memory-at-scale.md
Problem: 125's layer 1 is "execution state in Temporal". The product has no Temporal (the orchestrator is "after the MVP"), and phase C owes two ADRs, the checkpointer and interrupts, that decide where a LangGraph agent's state lives. No such ADR exists yet (grep of `docs/planning/adr/` for "checkpointer" finds none). `StatePort` holds only idempotency, feedback, and rate-limit keys.
Why it matters: the four-layer design would be written before the first state decision it must contain, and the session layer (Valkey via `StatePort`) overlaps the checkpointer's backend.
Fix: add to `## References` and `## What`: "starts from the phase C checkpointer ADR; layer 1 is that ADR's choice until the orchestrator exists". No dependency edge (ADRs are not issues). `context_ref` already exists (`packages/chassis/src/chassis/core/envelope.py:61,77`), so box 3 can only be met by the design note. Phase `later`, size M (see W1012-17).
Status: open

### W1012-16 [MEDIUM] [rule 13] docs/planning/issues/127-L-3-router-slm.md
Problem: 127 designs a classifier trained on logged runs. The product plan parks it by name ("the router SLM", "127 L-3").
Why it matters: it must not sit in the live count; it also depends on 040 D-0 (the recorder, W4) and 108 O-12 (planner modes), neither built.
Fix: verdict `parked`, phase `parked`, size L unchanged; add one line "Parked 2026-11-06 with the SLM track (product plan)". Nothing depends on it, so no dependency edit. Unpark as `later` if a trained router returns.
Status: open

### W1012-17 [LOW] [rule 3] docs/planning/issues/125-L-1-shared-memory-at-scale.md
Problem: 125 and 126 are size L (4–5 days) but are plans: a design note, a review, entry criteria, and a split into sized issues, with "Building any of it" out of scope. The open boxes are five for each, and each box is a paragraph in one note.
Why it matters: W12 counts 15 days for work that produces two documents.
Fix: resize both to M (2 to 3 days). Suggested, not measured; the W9 and W6 sessions' results can move it. 127 stays L (parked, so it counts for nothing).
Status: open

### W1012-18 [LOW] [rule 10] docs/planning/issues/119-X-6-runbooks.md
Problem: 119's failure list mixes the product's failures with ones that need parked or later parts: model server down or GPU node lost, evaluator drift, stuck Temporal runs, registry degraded, Postgres failover on CloudNativePG. The listed chassis failures (bad release, minimum-version refusal, compromised workload, `remote` auth failure) are not in `docs/guides/poc-05-runbooks.md`, which has leaked-token rotation, admission refusal, and stuck claims, without rollback and escalation parts.
Why it matters: the runbook set should match the product that exists.
Fix: keep 119 `later`; rewrite the list as "the failures of components that exist" first (router outage and fallback rate, a bad chassis release, a compromised workload, a `remote` auth failure, bad config), and cite `poc-05-runbooks.md` as the base, adding rollback and escalation. Have 117's alerts link `runbook_url` to it as it stands.
Status: open

### W1012-19 [LOW] [rule 10] docs/planning/poc/011-what-is-left.md
Problem: Section 4 lists "the rare 502 under load" (about 1 in 4,600 calls at 4 pairs, chassis or Traefik unknown). No issue owns it.
Why it matters: it shows only at 4 pairs, which is exactly X-5a's run.
Fix: add one X-5a box: "the 502 rate at 4 pairs is recorded and the source (chassis or Traefik) is named".
Status: open

### W1012-20 [LOW] [rule 6] docs/planning/issues/121-X-4-backups-restore.md
Problem: Open `suggested:` values in 116–127 that no PoC settled: RPO 24 hours and RTO 4 hours, a quarterly restore test (121); 5,000 registry entries, "twice the current peak", "far above means more than twice the upper estimate" (122); `slo` block and burn-rate alerts (117); the keep floor (126). The PoC data settles one: the Revisit rule input is "at or above the top of 0.05–0.1 vCPU, not above twice" (PoC-4 note).
Why it matters: none blocks phases A to D, so they stay out of the W1 to W4 decision pack; the 122 "twice" rule becomes a measured check in X-5a.
Fix: keep the markers on 117, 121, and 122b. In X-5a, state the 2× rule as the PoC-4 note's reading, cited, and keep `suggested:` until the owner accepts it.
Status: open

### W1012-21 [LOW] [rule 5] docs/planning/issues/118-C-7-incident-flow.md
Problem: Box 2 adds an `incident` event type to the chassis JSON event schema. `packages/chassis/schemas/events.v0.json` defines Delta, End, Error, Metrics, Start, and ToolCall; the event schema is versioned and the previous major stays accepted (CLAUDE.md hard rules).
Why it matters: the change is a contract change, not a workload feature.
Fix: add "needs a `chassis-architect` pass and a schema version note" to the box; keep 118 `later`; its dependencies (072 C-3 and 117) stay.
Status: open

### W1012-22 [LOW] [rule 7] docs/planning/issues/116-X-1b-terraform-second-cloud.md
Problem: The epic refs on 116, 121, and 122 (`G.4`, `H`, `E.6`) still describe a two-cloud, GPU, MLflow shape. The product plan runs on kind and Compose.
Why it matters: wording only.
Fix: when W1012-07, -08, and -11 land, keep the epic story links and add "the product plan needs no cloud before phase F" to the Why of 116 and 121.
Status: open
