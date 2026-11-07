# Findings W7: W7 Registry and governance gate

Session: W7. Issues: 076–093. Waves: W7.
Rubric: [rubric.md](rubric.md).

## Summary

The PoC track built no registry, no controller, no watcher, no importer, no scanner for tool descriptions, and no search. It built the parts a registry would read: `GET /manifest` (manifest v0), `spec.trust` with its two checks (chassis config and the PoC-5 admission policy), the `/ready` probe of the workload, `spec.governance` with two fields, keyless cosign signing in the template CI, and a Snyk Agent Scan job. One box ticks, after a rewrite (086 R-10, the `/ready` half); nothing retires and nothing is parked. All 18 issues are phase `later`: the product plan names no registry phase, and phases A to F run on kind and Compose with no agent lookup. The top risk is a phase A item, 122 X-5, that depends on 091 R-11 (W7-01). Two other risks: 077 C-1 assumes governance fields in the config schema that the code does not have (W7-02), and 080 R-7 assumes an organization key where the template signs keyless (W7-04).

## Issues

`Boxes` is met over total after the ticks, counting 086 after its box 3 is split in two. Phase is `later` on every row: no product phase (A to F) needs a registry. The one-line reason is in the last column.

| Issue | Boxes | Phase | Verdicts | Evidence or reason |
| ----- | ----- | ----- | -------- | ------------------ |
| 076 R-1 | 0/8 | later | keep, deps, cite | No registry store, model, API, or `description_hash` anywhere. Box 5 has a part: `GET /manifest` exists (`packages/chassis/tests/test_manifest.py::test_manifest_matches_the_config_and_the_openapi_spec`) but carries `versions.chassis`, `lane`, `agent.trust` and no digests, endpoint, or `chassis_version` key (W7-08). Needs the `agentctl` `data` kind, not built (W7-09). Size L stays (8 open boxes, one service plus a store). Later: nothing in phases A to F looks up an agent |
| 077 C-1 | 0/9 | later | keep, resize M, deps, drift, conflict | Chassis side holds two governance fields only (W7-02). Boxes 4 and 6 are already enforced at config load and at admission, so the registry adds a check only for manual entries (W7-07). Box 7 has no admission rule behind it (W7-06). 9 open boxes plus 7 missing schema fields: M (W7-10). Later: needs the registry. The schema-fields half could land in phase F with the `governance` block in `agentctl` (059 H-15) |
| 078 R-16 | 0/7 | later | keep | Nothing lists agents by risk class or exports an inventory. The reusable piece is the config `version` (content hash) in `/manifest` `versions.config`. Size S stays. Later: depends on 077 |
| 079 C-2 | 0/6 | later | keep, deps | No checklist, no Article 5 file, no `prohibited_check_failed` value (`RiskClass` is `minimal`, `limited`, `high`). Box 5 needs 078 R-16; add the dependency (W7-18). Legal sign-off is an owner-list item. Later: depends on 077 |
| 080 R-7 | 0/9 | later | keep, deps, drift, tick-blocked | No trust levels or approval queue. Box 8 has a part: admission rule 4 and `trustedRepositories` (`pocs/poc-05-sandboxed/tests/test_poc05_admission_static.py::test_params_live_in_the_platform_namespace_and_list_our_repositories`) read the image repository, not a signature, and the list is hand-kept: `tick-blocked`. Signing is keyless in the template, not "the organization's key" (W7-04). ADR-005 and the "two meanings of trust" note agree (W7-14). Add dep 025 H-10 (W7-05). Size M stays. Later |
| 081 R-8 | 0/6 | later | keep, drift | No scanner, no poisoned fixture, no hidden-Unicode rule. `guardrail-server` has 7 English injection patterns for runtime text; the 18 red-team cases cover input, tool output, exfil, and secrets, not tool descriptions. The Snyk job exists (`.github/workflows/poc07-agent-scan.yml`) but never ran a real scan (`pocs/poc-07-cross-cutting-decisions/notes/2026-10-28-agent-scan.md`). Stale "Proposed in PoC-7" bullet (W7-12). Size M stays. Later |
| 082 R-9 | 0/6 | later | keep | No hash over tool descriptions. The reusable pattern is `spec_sha256` in `packages/chassis/src/chassis/server/manifest.py` (canonical JSON, then sha256; `test_the_sha256_follows_the_spec`). Snyk's pinning is not asserted in the repo. Size S stays. Later |
| 083 R-2 | 0/9 | later | keep, cite | No kopf code, no `platform/register` label. Pods carry `agents.platform/role`, `lane`, `trust` (`deploy/helm/agent-lib/templates/_deployment.tpl`), the hooks a controller could select on. Nothing in a cluster reads `/manifest` from a Service. The template CI has no registration step to remove (W7-15). Box 6 needs the minimum-version rule, not built (W7-06). Size M stays. Later |
| 084 R-3 | 0/7 | later | keep | No Docker events watcher; the Compose files carry no `labels:`. The template Compose has `fake` and `local` profiles and a `debug/` folder (016 H-9 side). Size S stays. Later: Compose runs need no registry |
| 085 R-6 | 0/7 | later | keep | Nothing: no registry API, YAML import, or UI. Size M stays (the UI is the largest part; not a second owner worth a split). Later |
| 086 R-10 | 1/9 | later | tick, resize M, cite | `/ready` already probes the workload every 2 s and goes 503 `workload_unreachable` after 3 failures, `/health` stays 200: box 3 splits into a ticked half and an open half (see Ticks). Metrics series `p95_latency_ms` and `error_rate` do not exist (W7-11). 8 open boxes after the tick, three subsystems: M (W7-10). Later |
| 087 R-4 | 0/6 | later | keep, cite | The one `tools/list` client is the chassis gateway (`chassis/adapters/mcp/gateway.py`, `refresh()`): no cursor, no `listChanged`. `test_a_failed_refresh_keeps_the_last_list` is a failure case, not removal. `mcp` 2.2.0 and `fastmcp` 4.0.10 are locked. The Vault reference has no store behind it (W7-20). Size S stays. Later |
| 088 R-5 | 0/6 | later | keep, cite | The operation-to-tool mapper is `FastMCP.from_openapi` in `chassis/adapters/mcp/agent.py`, limited to `POST /v1/run` (one tool). No OpenAPI validator in `uv.lock` (box 6). 051 H-13 is not closed. Size M stays. Later |
| 089 R-15 | 0/6 | later | keep, tick-blocked | No AsyncAPI code (052 H-21 not built). Box 4 is partial: payload schemas exist for two of four event types in code (`packages/chassis/schemas/task-result.v1.json`, `feedback.v0.json`); the D.2 name pattern is checked nowhere (W7-21). Size S stays. Later |
| 090 R-13 | 0/7 | later | keep | No query set. The pattern to copy is `eval_kit.golden`: one source file, generated consumers, a sync test (`test_eval_kit_golden_generated_files_are_in_sync`), a baseline file. It has no held-out split. Size M stays. Later |
| 091 R-11 | 0/7 | later | keep, deps, parked-dep, drift | No pgvector, psycopg, embedding, or rerank code, and no embedding route in LiteLLM config (`docs/specs/models.md` lists `big-default`, `local-small`, `fake-chat`). Depends on 036 S-7 (parked): drop it (W7-03). Size L stays. Later |
| 092 R-12 | 0/7 | later | keep, deps, cite | No `fallback_of` or `fallbacks` anywhere. `ModelSpec.fallback_route` is the model fallback of the evaluator gate, not this. Box 3 needs live metrics from 086 R-10: add the dependency (W7-17). Example names the SLM story (W7-22). Size M stays. Later |
| 093 R-14 | 0/7 | later | keep, deps | FastMCP 4.0.10 over streamable HTTP is proven in `chassis`, `fake-mcp-server`, `code-runner`. The OpenAPI-to-MCP mechanism is proven for one operation (`packages/chassis/tests/test_mcp_interface.py::test_the_tool_spec_is_the_run_operation_of_the_app_spec`). Box 6 needs the approve scope from 080 R-7: add the dependency (W7-17). Size S stays. Later |

Sizes: 077 S to M, 086 S to M. Wave W7 goes from 36.5 to 39.5 days. No split: the largest, 076 R-1 and 091 R-11, each have one owner and one store.

## Ticks

One line per box this session ticks, ready to paste into the issue. It is conditional on the rewrite named in the line.

- `086 R-10`, box 3, split. The ticked half is rewritten to "A hung workload behind a live chassis fails `/ready` after 3 failed probes, and `/health` stays 200" (W7-10); the other half stays open as "…and the entry becomes `inactive` after 3 failed checks": `· Delivered in PoC-4: packages/chassis/tests/test_readiness.py::test_ready_goes_false_after_failed_probes_and_health_stays_ok and ::test_the_monitor_turns_unhealthy_after_n_failures_and_back_after_one_success; pocs/poc-04-stateless-scalable/tests/test_liveness.py::test_ready_goes_false_when_the_workload_hangs_and_health_stays_ok; pocs/poc-04-stateless-scalable/notes/2026-10-01-drills.md (Compose pause drill: 503 after 9.7 s and 8.2 s, 200 again after 2.0 s and 1.4 s)`. The `suggested:` marker on "`/ready` checks that the workload answers through the connector" in `## What` is dropped: `ReadinessMonitor.probe()` in `packages/chassis/src/chassis/server/readiness.py` does it.

Offline tests cited in this file were run on 2026-11-06:

```
$ UV_NO_SYNC=1 scripts/check_offline.sh <10 test ids from this file> -q -p no:cacheprovider
74 passed in 4.80s
```

The ten ids: the three 086 tests above; `test_poc05_trust_config.py::test_untrusted_outside_the_remote_lane_is_refused`; `test_marking.py::test_marking_false_is_refused_for_limited_and_high`; `test_poc05_admission_static.py::test_the_rule_model_agrees_with_each_fixture` and `::test_params_live_in_the_platform_namespace_and_list_our_repositories`; `test_manifest.py::test_manifest_agent_trust_shows_untrusted_with_the_remote_lane`; `test_poc09_template_ops.py::test_github_signing_is_keyless_and_default_branch_only`; `test_mcp_interface.py::test_the_tool_spec_is_the_run_operation_of_the_app_spec`. Several are parametrized, so 10 ids ran 74 cases. Every other id in this file was checked with `grep -rn "def test_name"` and not run. The kind admission test (`test_poc05_kind_admission.py::test_rejected_fixture_is_refused_with_its_rule_message`) needs the `poc05` cluster; its record is `pocs/poc-05-sandboxed/notes/2026-10-08-remote-suite.md` and ADR-005 (51 cases).

## Status bullets folded

None of the 18 issues has a "Status after PoC-N" bullet (checked with `grep` over 076–093). Inputs never applied, read for this wave:

- `081 R-8`, "Proposed in PoC-7": decided. ADR-008 was accepted on 2026-10-30. The Snyk job and its 13 offline checks (`pocs/poc-07-cross-cutting-decisions/tests/test_poc07_agent_scan_static.py`) are built. The first real scan waits on a `SNYK_TOKEN` repo secret: owner list (011 section 2), not a box. The PoC-7 draft's open item ("the `scan` run's egress is not limited to `api.snyk.io`") goes to `## Out of scope`. Replace the bullet with "Decided in ADR-008" (W7-12).
- `077 C-1`, PoC-7 draft line 55 (`spec.governance`, the load-time refusal): a fact for the box about `limited` with marking off, which stays open as a registry-side check (W7-07). The draft puts it under 023 C-5; W2b owns that.
- `086 R-10`: PoC-4 drills (`pocs/poc-04-stateless-scalable/notes/2026-10-01-drills.md`) are the evidence for the ticked half. `backlog-changes.md` of PoC-4 names no W7 issue.
- `080 R-7` and `025 H-10`: the PoC-9 template ops note (`pocs/poc-09-agent-mvp-template/notes/2026-10-29-template-ops.md`) records that nothing verifies a signature at admission or deploy; only the CI self-check does. Open item for 080 box 1 and 2.
- 011 section 5 and the PoC-6c list: none names 076–093. The PoC-9 W2–W5 report names none either.

## Findings

### W7-01 [HIGH] [rule 4] docs/planning/issues/122-X-5-load-tests.md
Problem: 122 X-5 depends on 091 R-11 (hybrid search) and 104 O-3. Phase A of the product plan names "throughput at 4 pairs on Linux (122 X-5)" as one of its items.
Why it matters: a phase A item then waits on the registry's search, which is `later` and about 7 days from a registry that does not exist; phase A is the first thing the product plan builds.
Fix: drop `R-11` and `O-3` from 122 X-5 in `ROWS`; the 4-pair throughput box stands alone. Move the registry load box to a new `122b` or into 091 R-11, as a `later` box. The W1012 session confirms the box split.
Status: open

### W7-02 [MEDIUM] [rule 4] docs/planning/issues/077-C-1-governance-enforcement.md
Problem: `## Why` says "the config loader (010 H-12) already puts the governance fields in the config JSON Schema". The code has two: `GovernanceSpec` in `packages/chassis/src/chassis/server/config.py:383` holds `risk_class` (`minimal`, `limited`, `high`) and `ai_generated_marking`. `intended_purpose`, `eu_ai_act_role`, `impact_assessment_ref`, `owner`, `human_oversight`, `data_sources`, `log_retention_days` do not exist, and `prohibited_check_failed` is not an allowed `risk_class`. A missing block is valid (`Spec.governance` defaults to `GovernanceSpec()`).
Why it matters: "no governance block, no activation" has nothing to check yet, and 078 R-16 and 079 C-2 list the same fields.
Fix: rewrite the `## Why` sentence to say the loader holds two fields; add one box to 077 or to 010 H-12 (W2a-21 already holds the `risk_class` and `vault://` boxes): "the chassis config schema carries the nine E.4 governance fields, required when `risk_class` is `limited` or `high`, with allowed values". This is the part that could land in phase F, when `agentctl` renders a `governance` block (059 H-15).
Status: open

### W7-03 [MEDIUM] [rule 13] docs/planning/issues/091-R-11-hybrid-search.md
Problem: 091 R-11 depends on 036 S-7 (parked) and serves the embedding and reranker models "on vLLM" (the vLLM adapter in 012 H-3 is parked). It also depends on 003 G-1b (named model routes), which the route map in phase B delivers.
Why it matters: W3-01 left the 091 side to this session; a `later` issue may not wait on a parked one. Nothing in the repo embeds or reranks: `uv.lock` has no `pgvector`, `psycopg`, or `sentence-transformers`, and no LiteLLM route for embeddings exists.
Fix: drop `S-7` in `ROWS`; keep `G-1b`. Rewrite the vLLM bullet: embedding and reranker models are called through named routes (`registry-embed`, `registry-rerank`) on whatever server the route map names, hosted or self-hosted; the Appendix I comparison (box 3) decides. Size L stays.
Status: open

### W7-04 [MEDIUM] [rule 6] docs/planning/issues/080-R-7-trust-levels.md
Problem: Boxes 1, 2, and 9 test "a valid cosign signature from the organization's key". The template signs keyless: `.github/workflows/ci.yml.jinja` runs `make sign` with `id-token: write`, then `cosign verify --certificate-identity <this workflow> --certificate-oidc-issuer https://token.actions.githubusercontent.com` (`pocs/poc-09-agent-mvp-template/tests/test_poc09_template_ops.py::test_github_signing_is_keyless_and_default_branch_only`). There is no organization key. 083 R-2 box 5 inherits the same wording.
Why it matters: the auto-activation path is the point of the issue, and its first two boxes cannot be written as tests until the verifier is chosen.
Fix: rewrite the boxes to "signed by the platform's CI workflow identity (certificate identity and issuer from a registry setting)"; drop "key" in 080 and 083; add the choice (keyless identity or a key) to the DEC-1 pack as a decision that no phase A to F blocks. The template's own `suggested:` marker on keyless signing stays until 025 H-10 closes.
Status: open

### W7-05 [MEDIUM] [rule 5] docs/planning/issues/080-R-7-trust-levels.md
Problem: Three things about ADR-005 are not in 080 or 077. First, admission rules 3 and 4 already enforce the registry prefix and a platform-owned `trustedRepositories` list (`deploy/kind/poc05/admission/params.yaml`, `policy.yaml`), which 077's source-test rule and 080 box 8 restate. Second, ADR-005 "Revisit" says decisions 4 and 5 reopen when image signatures land and "the trust list then comes from the signature". Third, 080 `## What` and its `ROWS` row mention image signing from 025 H-10 but depend only on 076 R-1.
Why it matters: an engineer would build a second source test beside the admission one, and miss that landing 080 triggers an ADR-005 revisit.
Fix: add "Decided in ADR-005 items 4 and 5: admission already refuses an image outside the registry prefix and a `trusted` image outside the platform list; this check reads the same list, or the signature once it lands, and landing it reopens ADR-005 decisions 4 and 5" to 080 and 077; add `025 H-10` to the dependencies of 080 in `ROWS` (it is a lower number).
Status: open

### W7-06 [MEDIUM] [rule 11] docs/specs/security.md
Problem: `docs/specs/security.md` lists "Admission: `untrusted` must use `remote`; a minimum chassis version" as a fixed control in `deploy/kind/poc05/admission/`. The policy has rules 0 to 8 and none checks a chassis version (ADR-005 and `pocs/poc-05-sandboxed/notes/2026-10-02-threat-model.md` defer it to 056 CH-7). 077 C-1 box 7 and 083 R-2 box 6 depend on that rule.
Why it matters: a reader of the spec believes the version floor is enforced today.
Fix: change the spec row to "a minimum chassis version (056 CH-7, not built)". It is a spec edit, so it goes to `proposals.md`.
Status: proposal

### W7-07 [MEDIUM] [rule 8] docs/planning/issues/077-C-1-governance-enforcement.md
Problem: Two boxes restate refusals the chassis already makes. "A `limited` agent with `ai_generated_marking: false` is not activated" is refused at config load (`packages/chassis/tests/test_marking.py::test_marking_false_is_refused_for_limited_and_high`). "An `untrusted` agent in the `sidecar` lane is not activated" is refused at config load (`pocs/poc-05-sandboxed/tests/test_poc05_trust_config.py::test_untrusted_outside_the_remote_lane_is_refused`) and at admission (rule 2, `rule2-untrusted-sidecar`). A chassis that fails to start never serves `/manifest`, so an auto-registered entry can never reach the registry in that state.
Why it matters: neither box can be ticked (the activation step does not exist), and as written they test a path no auto-registered agent takes.
Fix: rewrite both to the case the registry alone guards: "a manually registered agent entry that declares ... is not activated", and add "the registry validates the entry's config with the chassis config model, so the loader's refusals hold". Keep both open (tick-blocked).
Status: open

### W7-08 [MEDIUM] [rule 4] docs/planning/issues/076-R-1-registry-data-model-api.md
Problem: Box 5 says the echo agent's `/manifest` maps to a valid entry "with `chassis_version`, `engine_connector`, and `runtime_trust` set, and the chassis's public address as `endpoint`". Manifest v0 (`packages/chassis/src/chassis/core/manifest.py`) has `versions.chassis`, `lane`, and `agent.trust`; its docstring says "Image digests, scopes, events, governance, class, kind, and task arrive later (051 H-13)". There is no `endpoint` and no image digest, and 083 R-2 passes both digests.
Why it matters: the mapper cannot be "with no hand edits" until the manifest has them, and 051 H-13 is not closed.
Fix: write the mapping in 076 (`versions.chassis` to `chassis_version`, `lane` to `engine_connector`, `agent.trust` to `runtime_trust`); add an open box to 051 H-13 for the two image digests, class, kind, governance, and the public address (the W5 session owns 051).
Status: open

### W7-09 [MEDIUM] [rule 4] docs/planning/issues/076-R-1-registry-data-model-api.md
Problem: 076 builds "a registry service built on the chassis, from the service template (025 H-10)", class `data`, with the chassis holding the store credential and exposing the store as MCP tools (ADR-001 follow-up (a), still unsigned-off). `agentctl` renders no `data` kind (011 "Moved to the backlog": `--trust`, the `orchestrator` and `data` kinds, `spec.modules`, the `governance` block, and a chart in 059 H-15).
Why it matters: the first box of the wave needs a template kind that does not exist, and follow-up (a) has no owner but this issue.
Fix: add `059 H-15` to the dependencies of 076 in `ROWS`; name (a) in 076's `## What` as an open decision; list it in the DEC-1 pack as "needed before W7, not before phases A to F".
Status: open

### W7-10 [MEDIUM] [rule 3] docs/planning/issues/086-R-10-health-checks.md
Problem: Two S issues carry 8 or more open boxes that each need a test. 086 R-10 has 8 boxes over three subsystems (a probe loop, a Prometheus refresh, a database role with no `DELETE`). 077 C-1 has 9 boxes plus the seven missing schema fields (W7-02).
Why it matters: S is up to 1 day; both are two to three.
Fix: resize 077 C-1 and 086 R-10 from S to M in `ROWS`. W7 goes from 36.5 to 39.5 days. For 086, split box 3 as in Ticks and tick the first half. Box 4 (scale to zero) is testable with `kubectl scale --replicas=0`; the chart has an HPA with `minReplicas` of at least 1 and no KEDA, so the box needs no higher-numbered issue (104 O-3).
Status: open

### W7-11 [MEDIUM] [rule 1] docs/planning/issues/086-R-10-health-checks.md
Problem: Box 8 ("live metrics on an entry match Prometheus") and the `## What` name `p95_latency_ms`, `error_rate`, and cost as series. The chassis exports `chassis_run_duration_seconds` (a histogram), `chassis_requests_total`, `chassis_run_errors_total`, and model token and cost counters (`packages/chassis/src/chassis/server/observability.py`). p95 exists only as the dashboard expression `histogram_quantile(0.95, …)` in `deploy/dashboards/*.json`.
Why it matters: a test cannot compare a series that does not exist; the refresh must define the queries.
Fix: rewrite box 8 to "the refreshed `p95_latency_ms`, `error_rate`, and cost on an entry equal the result of the named PromQL queries over the chassis series, within one interval", and list the queries in `## What`, reusing the dashboard expressions.
Status: open

### W7-12 [MEDIUM] [rule 10] docs/planning/issues/081-R-8-description-scanning.md
Problem: The "Proposed in PoC-7 … the user accepts it at the PoC-7 pull request" bullet is stale. ADR-008 was accepted on 2026-10-30. The PoC-7 draft (`pocs/poc-07-cross-cutting-decisions/notes/backlog-changes.md`) names 081 R-8 and was never applied. Also, the Snyk job ran only the offline `inspect` step: the PoC-7 note says no Docker daemon was available, there was no `SNYK_TOKEN`, and "no scan has reached the analysis API".
Why it matters: a reader thinks a real scan result exists.
Fix: replace the bullet with "Decided in ADR-008. Built in PoC-7: `poc07-agent-scan` and `deploy/agent-scan/`, with 13 offline checks; no real scan has run (needs `SNYK_TOKEN`). It reports on our own servers and does not replace this scan." The Snyk egress item goes to `## Out of scope`.
Status: open

### W7-13 [MEDIUM] [rule 14] docs/planning/issues/076-R-1-registry-data-model-api.md
Problem: The product plan has no registry phase, and its "Gaps the PoC track did not cover" table holds no registry gap. All 18 W7 issues (39.5 days after W7-10) have no phase A to F. Three live phase items sit near them: 123 C-4 depends on 078, 099 O-1 on 092, and 094 P-1 on 093, all outside the plan.
Why it matters: `phase-map.md` shows zero days from W7; the wave's `later` label has to be a decision, not a gap.
Fix: record W7 as `later` as a whole in `phase-map.md`; one exception to weigh: the governance fields in the chassis schema (W7-02) and the `governance` block in `agentctl` (059 H-15) are phase F work, because the generated repo should carry a governance block before any registry reads it.
Status: open

### W7-14 [LOW] [rule 5] docs/planning/issues/076-R-1-registry-data-model-api.md
Problem: Check of the "two meanings of trust" note (`issues/000-plan.md`, "ADR-001 follow-ups") against 080 R-7 and ADR-005: no conflict. ADR-005 decision 5 uses only a source signal (the platform list of CI-built repositories) and is one-way (`untrusted` is always believed), which fits "`external` and `manual` are `untrusted`, `internal-signed` is evidence for the source test only". There is a third surface the note does not name: `ManifestAgent.trust` in `/manifest` is `spec.trust`, documented as "shown to callers; not a security control by itself". The registry entry also has a `trust` field (a level) next to `runtime_trust`, and a `trust` list filter.
Why it matters: an API with `trust` meaning a level while the manifest it is built from has `agent.trust` meaning `trusted` or `untrusted` invites a wrong mapping.
Fix: name the registry field `trust_level` in 076, 080, 085, 091, and 093, keep `runtime_trust` for `spec.trust`, and add the manifest's `agent.trust` to the note in `000-plan.md`.
Status: open

### W7-15 [LOW] [rule 10] docs/planning/issues/083-R-2-kubernetes-controller.md
Problem: Box 9 says "the template CI no longer has the registration stub". There is no registration step in `packages/agentctl/src/agentctl/template/project/.github/workflows/ci.yml.jinja` or the GitLab file; the "registry" lines are the GHCR image registry. The `ROWS` note of 025 H-10 ("the registration step is a stub") is stale.
Why it matters: the box is moot as written; there is nothing to remove.
Fix: rewrite box 9 to "a new agent from the template carries the registration label and registers through the controller only"; drop the "Removes the registration stub" bullet; reword the `ROWS` note of 025 H-10 to "registration is by label, through the controller (R-2)".
Status: open

### W7-16 [LOW] [rule 1] docs/planning/issues/088-R-5-openapi-import.md
Problem: Box 6 ("an invalid spec is rejected with the validator's error") needs an OpenAPI validator. `uv.lock` has none (`openapi-spec-validator`, `openapi-core`); it has `openapi-pydantic` 0.5.1, `jsonschema`, and `schemathesis`. 051 H-13's own criterion to validate the spec in CI is also unmet: `pocs/poc-03-one-interface-every-client/tests/test_openapi_props.py::test_the_spec_is_openapi_3_1_with_every_operation_named_and_labeled` checks only the version prefix and operation names.
Why it matters: both issues pick a validator, and 088 would pick it again.
Fix: name the validator in 051 H-13 (the W5 session) and say in 088 that it reuses it; add the OpenAPI 3.0 note ("suggested: also 3.0") to the validator choice.
Status: open

### W7-17 [LOW] [rule 4] docs/planning/issues/092-R-12-main-pick-fallbacks.md
Problem: Two boxes use a lower-numbered issue that is not a dependency. 092 R-12 box 3 ("cost and speed differences match the live metrics") needs 086 R-10, which `## What` names. 093 R-14 box 6 ("`registry_approve` without the approve scope is refused") needs 080 R-7, which `## What` names.
Why it matters: `ROWS` would let 092 and 093 start before the pieces they test exist.
Fix: add `R-10` to the dependencies of 092 R-12 and `R-7` to those of 093 R-14 in `ROWS` (both are lower numbers).
Status: open

### W7-18 [LOW] [rule 4] docs/planning/issues/079-C-2-prohibited-practice-checklist.md
Problem: 079 C-2 box 5 ("the answers … show on the entry and in the inventory (078 R-16)") needs 078 R-16, but 079 depends only on 077 C-1.
Why it matters: 078 has a lower number, but the box would be written before the inventory exists.
Fix: add `R-16` to the dependencies of 079 in `ROWS`.
Status: open

### W7-19 [LOW] [rule 14] docs/planning/issues/081-R-8-description-scanning.md
Problem: The product plan's phase E maps the OWASP agentic list (ASI01–ASI10) to the controls, and its P2 gap says that map is missing. Tool-description poisoning is one of its rows. The 18 red-team cases (`packages/contract-suites/src/chassis_contracts/redteam/cases.yaml`) have none on a tool description, and `guardrail-server` runs only on runtime text.
Why it matters: phase E would map the threat to a control that does not exist yet.
Fix: no new issue. In the phase E mapping, mark tool-description poisoning as covered by Snyk Agent Scan in CI (`deploy/agent-scan/`, with the W7-12 caveat) and by 081 R-8 (`later`); the 30 poisoned fixtures of 081 can seed a red-team case later.
Status: open

### W7-20 [LOW] [rule 6] docs/planning/issues/087-R-4-mcp-import.md
Problem: 087 R-4 and 088 R-5 store a "Vault reference" for credentials. No Vault exists: 038 X-1a box 5 (Vault, External Secrets) is open and the kind stand-in is `deploy/kind/poc05/platform/seed.sh`.
Why it matters: box 6 ("only the Vault reference") needs a reference format that does not exist.
Fix: reword to "a secret reference (a name resolved by the chassis's secret mechanism)" and drop the Vault name until X-1a closes.
Status: open

### W7-21 [LOW] [rule 1] docs/planning/issues/089-R-15-event-schemas-registered.md
Problem: Box 4 ("every event type in D.3 has a registered schema") is partly met by file only. The code has four types: `agents.task.completed.v1`, `agents.task.failed.v1`, `agents.task.requested.v1`, and `agents.feedback.received.v1`; schema files exist for the first two (`task-result.v1.json`) and the last (`feedback.v0.json`). The D.2 name pattern is checked nowhere in code. `registry.entry.changed.v1` is in the epic's D.3 (line 906) and has no schema.
Why it matters: the box mixes "a schema file exists" with "registered in the registry".
Fix: keep open; add one sentence to `## What`: the first schema set comes from `packages/chassis/schemas/`, and the pattern check lives in the registry, not the chassis. Tick-blocked.
Status: open

### W7-22 [LOW] [rule 13] docs/planning/issues/092-R-12-main-pick-fallbacks.md
Problem: Box 1 uses `simplifier` and `simplifier-big-model`, the epic's F.3 example from the SLM story (the SLM is parked).
Why it matters: the test fixture names a parked agent pair.
Fix: say the example is a fixture pair, and name two generic entries (a main and a fallback) in the fixture; the F.3 names stay in the epic.
Status: open
