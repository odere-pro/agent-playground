---
title: "CH-6: Remote lane and the trust rule: sandboxed pod or managed runtime over A2A, cloud auth adapter, admission check"
labels: ["story", "priority:P1", "phase:0-harness", "area:harness", "size:S", "layer:chassis"]
milestone: "W5 Chassis completion"
index: 55
epic_id: CH-6
depends_on: ["009 CH-1", "026 CH-4", "010 H-12", "024 CH-3"]
blocks: ["056 CH-7", "059 H-15", "077 C-1", "116 X-1b"]
epic_refs: [G.3, G.4, H]
---

## Why

[ADR-001](../adr/001-chassis-delivery-model.md) items 4, 5, and 8: untrusted workloads, third-party images, managed runtimes, and other teams' services use the `remote` lane. The sandbox runtime and the network policy are set per pod, not per container. So to sandbox the workload but not the chassis, they must run in different pods. The trust rule decides what must go to `remote`, and an admission check enforces it. Hard requirement 2 says a lane that CI does not test on every commit is not supported, so this issue also adds that CI job.

## What

- The `remote` connector: the same A2A client as 009 CH-1, with its URL from `spec.engine.url` and an auth adapter from `spec.engine.auth`. It never follows the agent card's URL ([ADR-005](../adr/005-remote-lane-auth-and-trust-admission.md), decision 3).
- A sandboxed workload pod:
  - its own pod, on gVisor through agent-sandbox (Decided in ADR-005);
  - no secrets mounted, except its own token;
  - default-deny egress: it reaches only the chassis's model and tool proxies, with no DNS;
  - only the chassis pod may call it.

  The chassis pod stays outside the sandbox.
- The proxies' `remote`-lane listener: the chassis's model and tool proxies (013 CH-2, 054 H-16) also listen on the pod IP for this lane, and each remote authenticates. Decided in ADR-005, decisions 1 and 2: one random bearer token per remote, used both ways, and a listener that requires a run (401 `remote_unauthenticated`, 403 `run_required`). Since PR #85 the listener also needs the run secret, sent in the header `x-chassis-run-secret` next to one `traceparent` ([contract v8](../../contracts/contract-v8.md#d-a-per-run-secret-on-the-remote-listener-r2-02), part D). mTLS is only ADR-005's Revisit trigger. Only the remote pod's label may reach the listener.
- Plain-A2A mode: `spec.engine.protocol: a2a` reads a third-party agent's own A2A stream (opt-in, `remote` only, fixed error text, strict usage; [contract v5](../../contracts/contract-v5.md#b-plain-a2a-mode-in-the-remote-connector), part B). Four remotes run on gVisor: smolagents, the Claude Agent SDK, the TypeScript agent, and `kagent-adk` ([engines on kind](../../../pocs/poc-06b-bake-off-remote-lane/notes/2026-10-09-lanes-b-kind.md)).
- `spec.trust: trusted | untrusted`. A workload is trusted only if it passes both tests:
  - **Source:** the owning team wrote and reviewed it, and its image is built in our registry.
  - **Behavior:** it does not run code, shell commands, or file writes itself.
- The admission check. Decided in ADR-005, decision 4: the `agent-trust-rule` ValidatingAdmissionPolicy, no Kyverno. It stops a workload marked `untrusted`, or an image not from our registry, from deploying in the `sidecar` lane. Rules for `SandboxTemplate` (T1 to T5) and `SandboxClaim` (C1 to C5) join it. Since PR #79 the library chart renders the same policy (`agent-lib.admission`), and `deploy/helm/agent-admission` installs it once per cluster. PR #91 adds four rules:
  - 7e: a chassis-shape pod sets `automountServiceAccountToken: false`, and only the chassis mounts a projected token;
  - 7f: a `sidecar`-lane pod does not share its process namespace, and every other container sets its own uid;
  - 7g: an ephemeral container may not target the chassis;
  - S1: a Service is `ClusterIP`, with no `externalIPs`, `loadBalancerIP`, or `externalName`.

  Claim rule C3 is now per namespace (fixture `ruleC3b-claim-pool-namespace`). The offline tests are `pocs/poc-05-sandboxed/tests/test_poc05_admission_static.py::test_every_rule_has_a_rejected_fixture_and_an_admitted_twin`, `::test_the_rule_model_refuses_a_debug_container_on_the_chassis`, `::test_the_rule_model_needs_an_explicit_uid_for_every_sidecar_container`, `::test_c3_admits_a_pool_only_in_its_own_namespace`, and `pocs/poc-05-sandboxed/tests/test_poc05_service_rule_static.py::test_poc05_every_shipped_service_passes_rule_s1`. The kind tests have not run in CI yet.
- The submitter has no pod create in the agent and tool namespaces, because pod create reads Secrets. The deployer stays inside the credential boundary, held by admission rules 6a to 6c (ADR-005, Consequences). Since PR #91 the deployer has no `networkpolicies` verbs. The cluster admin applies each policy file by path (`pocs/poc-05-sandboxed/tests/test_poc05_admission_rbac_static.py::test_deployer_cannot_write_network_policies`).
- One SandboxClaim per run (PR #91). This closes the limit in contract v8 D.2: a remote pod that serves several runs holds every run's secret. Each engine is a SandboxTemplate and a SandboxWarmPool behind the front door `remote-run-dispatch` (`packages/code-runner/tests/test_code_runner_a2a_dispatch.py::test_code_runner_a2a_dispatch_two_runs_make_two_claims_on_two_pods`, `pocs/poc-06b-bake-off-remote-lane/tests/test_poc06b_run_isolation_static.py::test_each_engine_is_a_template_and_a_warm_pool_set_like_code_runner`). The kind proof, `test_poc06b_kind_run_isolation.py`, waits on its first CI run.
- Generated code run through the code-execution tool behind `ToolPort` (054 H-16) does not make an agent untrusted.
- A list, per kind of remote, of what the chassis cannot see or control: the remote's own internal calls ([the blind-spots note](../../../pocs/poc-05-sandboxed/notes/2026-10-02-blind-spots.md); for `kagent-adk`, its tool calls, its state, and whether it checks the bearer, which it does not).
- A CI job that runs a fake workload through the `remote` lane on every commit: `.github/workflows/remote-lane.yml`.

## Reuse

- **Use:** the a2a-sdk client from 009 CH-1; `kubernetes-sigs/agent-sandbox` and a gVisor RuntimeClass for sandboxed pods. Decided in ADR-005: a bearer token for the remote auth, and the `agent-trust-rule` ValidatingAdmissionPolicy for the admission check (decision 4). Managed runtimes are out of scope (ADR-006).
- **Build:** the `remote` connector, the admission policy, and the CI job with a fake remote workload.
- **Watch:** agent-sandbox reached v1.0 in 2026, so pin it. On EKS, gVisor or Kata must be installed on the nodes.
- Details: [reuse analysis](../poc/010-reuse-analysis.md)

## Out of scope

- Decided in [ADR-006](../adr/006-agent-engines-default-supported-lanes.md): managed runtimes (AgentCore, Vertex) are out of scope since 2026-10-09. The first cloud, 038 X-1a, reopens it. This takes with it the managed-runtime auth adapter (SigV4, OAuth 2.0, or Google auth) and its CI job, the managed-runtime rows of the blind-spots list, and the one relaxation of ADR-001 hard requirement 1 (a managed runtime that holds its own scoped key).
- The second cloud's auth adapter (116 X-1b).
- Accepted in ADR-005, Consequences, not fixed here: the dispatcher's Kubernetes token and its one `ipBlock`, to the API server on 6443 (a B4 exception); a stolen dispatcher token can delete other callers' claims (a denial of service); runsc ignores the pod's `/dev/shm` size cap, which the pod's memory limit bounds.
- An OpenAI-compatible connector for workloads that cannot speak A2A. It is added only when such a workload exists.
- The minimum-version rule. It is 026 CH-4's, and since PR #79 it is rule 9 of the same policy set.
- The registry's trust levels (080 R-7). The backlog plan's gaps list says how they map to `spec.trust`.

## Acceptance criteria

- [x] A fake workload runs through the `remote` lane in CI on every commit, and passes the same contract suite as the `sidecar` lane. · Delivered in PoC-5: `pocs/poc-05-sandboxed/tests/test_poc05_remote_lane_contract.py::TestPoc05ThreeLanes` (offline, the same `LaneContract` as `packages/chassis/tests/test_lane_contract.py::TestChassisLanes`); `pocs/poc-05-sandboxed/tests/test_poc05_kind_remote_lane.py::test_remote_gives_the_sidecar_events_for_the_same_request` (kind, `remote-lane.yml`, [run 37942450871](https://github.com/odere-pro/agent-playground/actions/runs/37942450871)). Since 2026-11-08 the CI gate runs the same suite through `packages/chassis/tests/test_lane_contract_remote.py::TestChassisRemoteLane`; the PoC-5 file runs locally.
- [ ] A hostile workload in the sandboxed pod cannot read secrets, reach the internet, write outside its scratch space, or call a tool that is not allow-listed. It closes when the T10 probe workload runs, owned by the user ([the T10 note](../../../pocs/poc-05-sandboxed/notes/2026-10-09-t10-probe-exception.md)). The secret, egress, and write halves already pass from outside the pod in `remote-lane.yml`: `test_poc05_kind_remote_controls.py::test_remote_holds_one_secret_its_own_token`, `::test_h19_remote_reaches_no_outside_address`, `::test_h21_root_is_read_only_and_tmp_is_writable`.
- [x] `test_poc05_kind_tool_gateway.py` runs in `remote-lane.yml` on every push and pull request. · Delivered by: PR #79, `deploy/kind/poc05/run.sh` `REMOTE_TESTS`, `pocs/poc-05-sandboxed/tests/test_poc05_ci_wiring.py::test_poc05_test_remote_runs_the_whole_sidecar_hostile_suite`, [run 42299094368](https://github.com/odere-pro/agent-playground/actions/runs/42299094368) (`175 passed, 2 skipped, 2 xfailed`). Since 2026-11-08 `remote-lane.yml` runs by hand (`workflow_dispatch`): PoC-5 is finished and checked locally ([what CI runs](../../guides/testing.md#what-ci-runs)).
- [x] A remote pod without its credential is refused by the proxies, and a remote pod with it still cannot reach LiteLLM or the MCP gateway directly. · Delivered in PoC-5: `packages/chassis/tests/test_remote_auth.py::test_no_token_and_a_wrong_token_are_401_with_one_body`; `pocs/poc-05-sandboxed/tests/test_poc05_kind_remote_controls.py::test_h17_listener_answers_only_the_remote_token_inside_a_run`, `pocs/poc-05-sandboxed/tests/test_poc05_kind_remote_controls.py::test_h30_remote_reaches_no_other_pod` (kind, `remote-lane.yml`; LiteLLM and its MCP gateway share port 4000).
- [x] The admission check refuses an `untrusted` workload, and a third-party image, in the `sidecar` lane. · Delivered in PoC-5: `pocs/poc-05-sandboxed/tests/test_poc05_admission_static.py::test_policy_has_every_rule_and_the_params_check`; `pocs/poc-05-sandboxed/tests/test_poc05_kind_admission.py::test_rejected_fixture_is_refused_with_its_rule_message` (kind, in `REMOTE_TESTS`); `pocs/poc-05-sandboxed/notes/2026-10-09-close-runs.md` section 6.
- [x] Every pipeline stage runs on remote calls. What the chassis cannot control is listed per kind of remote. · Delivered in PoC-5, PoC-8: `pocs/poc-08-cross-cutting-build/tests/test_poc08_c7_stages_redteam.py::test_every_stage_runs_in_every_lane`; `pocs/poc-05-sandboxed/notes/2026-10-02-blind-spots.md` section 1; `docs/contracts/contract-v5.md` part B.8.
- [x] gVisor's latency and memory overhead are measured and recorded. · Delivered in PoC-5: `pocs/poc-05-sandboxed/notes/2026-10-09-gvisor-overhead.md` "Result".
- [x] A remote's output over the stream caps (`STREAM_EVENTS_MAX`, `STREAM_BYTES_MAX`, contract v8 E) ends the run with a fixed error (`pocs/poc-06b-bake-off-remote-lane/notes/backlog-changes.md`, line 20). · Delivered by: PR #85, `packages/chassis/tests/test_a2a_stream_cap.py::test_a_remote_that_streams_past_the_event_cap_ends_with_one_stream_too_large`, `::test_a_remote_that_streams_past_the_byte_cap_ends_with_one_stream_too_large`
- [x] The workload token Secret holds two keys, one per direction, and a test shows the key for one direction is refused in the other (gap review, 2026-11-07). · Delivered by: `packages/chassis/tests/test_remote_two_keys.py::test_the_chassis_presents_one_key_and_its_listener_accepts_only_the_other`, `::test_one_key_both_ways_fails_start_with_a_clear_message`; `pocs/poc-09-agent-mvp-template/tests/test_poc09_chart.py::test_remote_lane_the_token_secret_holds_two_keys_one_per_direction`
  - The PoC-5 and PoC-6 kind manifests and both `seed.sh` files make and mount the two keys (`pocs/poc-05-sandboxed/tests/test_poc05_two_key_token_static.py`). Still needed: a live kind run (`remote-lane.yml`, `poc06-kind.yml`) on a cluster seeded fresh, since the old one-key Secrets are not migrated in place.
  - Open: `kagent-adk` sends its inbound bearer back as its model key (`api_key_passthrough`) and holds no Secret, so the listener now refuses its model calls (401). It needs its own workload-to-chassis key, or a decision to drop it from the remote lane.

## Dependencies

- Depends on: [009 CH-1](009-CH-1-engine-connectors-a2a.md), [026 CH-4](026-CH-4-chassis-only-credentials-egress.md), [010 H-12](010-H-12-config-loader.md), [024 CH-3](024-CH-3-helm-library-chart.md)
- Blocks: [056 CH-7](056-CH-7-chassis-release-rings.md), [059 H-15](059-H-15-agent-factory-cli.md), [077 C-1](077-C-1-governance-enforcement.md), [116 X-1b](116-X-1b-terraform-second-cloud.md)

## References

- Source: [ADR-001](../adr/001-chassis-delivery-model.md), not an epic story. Epic phase: [Phase 0](../slm-agent-platform-epic-v3.md#phase-0-agent-harness)
- Epic context: [G.3](../slm-agent-platform-epic-v3.md#g3) · [G.4](../slm-agent-platform-epic-v3.md#g4) · [H](../slm-agent-platform-epic-v3.md#app-h)
- Backlog plan: [000-plan.md](000-plan.md)
