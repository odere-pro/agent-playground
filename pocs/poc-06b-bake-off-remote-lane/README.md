# PoC-6b: Framework bake-off, part B: the `remote` lane

Status: in progress: offline evidence complete; kind green (`poc06-kind.yml` run 38010290978, 74 passed); waiting on the Mac run (`make poc06-mac`, hosted-model numbers) and on the user to accept ADR-006
Planning doc: [006-PoC-6-framework-bake-off.md](../../docs/planning/poc/006-PoC-6-framework-bake-off.md)
Time box: 1 week, after PoC-5

Part A is [PoC-6a](../poc-06a-bake-off-sidecar-lane/README.md). This README keeps exit criteria 1, 2 (the remote solution), 3, 4, 5, 6, 7 final, and 8. The remote solution is `kagent-adk`, kagent's Python runtime in plain-A2A mode, not kagent on Kubernetes (the probe said no-go for that).

## Question

Which untrusted frameworks and which remote solution does the chassis front in the `remote` lane, with no change to its core?

## Scope

- [x] The untrusted frameworks as `remote` workloads: Claude Agent SDK with its shell and file tools on, and smolagents. The Claude Agent SDK writes to the home directory, so it needs a scratch volume under the read-only root file system; note what it needs. · evidence: `packages/workloads/echo-claude-agent`, `packages/workloads/echo-smolagents` (`tests/test_echo_smolagents_tasks.py::test_lookup_task_calls_both_tools_in_order`, `tests/test_echo_claude_agent_handle.py::test_lookup_task`); scratch volume: `/tmp` 128Mi memory-backed emptyDir plus a per-run `HOME`, `tests/test_poc06b_kind_static.py::test_the_claude_pod_meets_the_cli_env_guard_and_has_a_scratch_volume`, [notes/2026-10-09-lanes-b-kind.md](notes/2026-10-09-lanes-b-kind.md) decision 5; `POST /v1/messages` on the model proxy: `packages/chassis/tests/test_model_proxy_messages.py::test_the_captured_body_streams_a_tool_use_round_trip`
- [x] One remote solution through the `remote` lane. No cloud is chosen, so kagent, the planning doc's suggested default. · evidence: [kagent probe](notes/2026-10-09-kagent-probe.md); plain-A2A mode: `packages/chassis/tests/test_a2a_plain.py::test_the_kagent_sequence_item_by_item`, `packages/chassis/tests/test_remote_connector_plain.py::test_regression_the_answer_text_is_not_dropped`; the model path goes through the chassis remote proxy with the remote token (`api_key_passthrough`), so no scoped key is needed. The cloud auth adapter is out of scope (user decision, 2026-10-09).
- [x] Two benchmark tasks for every engine. · evidence: offline, the remote lane: [scorecard](../poc-06a-bake-off-sidecar-lane/notes/2026-10-09-scorecard.md) pass table (smolagents and Claude 30 of 30); `pocs/poc-06a-bake-off-sidecar-lane/tests/poc06_harness.py` registry; on kind: `tests/test_poc06b_kind_engines.py::test_the_engine_passes_the_task_through_its_chassis` (waits on CI, below)
- [ ] Run every task on a hosted big model, through the chassis model proxy and the router. · waits on: the Mac run, `make poc06-mac`. The SLM runs are [PoC-6c](../poc-06c-pretrained-slm/README.md).
- [x] Score each engine against the bake-off criteria. · evidence: [scorecard](../poc-06a-bake-off-sidecar-lane/notes/2026-10-09-scorecard.md) (offline). The `kagent-adk` cells say "not measured: kind, from CI" and fill in from the `poc06-kind.yml` run.
- [x] Check router compatibility per engine. · evidence: [scorecard](../poc-06a-bake-off-sidecar-lane/notes/2026-10-09-scorecard.md), "Router compatibility"; `packages/workloads/echo-smolagents/tests/test_echo_smolagents_wire.py::test_lookup_passes_when_the_router_drops_stop`. Claude: the route drops `thinking`, `cache_control`, and others, and counts them (contract v5, A.5).
- [ ] Rerun the PoC-3 contract suite, the PoC-4 load test, and the PoC-5 hostile suite on each new engine, in its lane. · see criterion 4.
- [x] List what the chassis cannot see or control for the remote solution. · evidence: [contract v5](../../docs/contracts/contract-v5.md) B.8; [notes/2026-10-09-lanes-b-kind.md](notes/2026-10-09-lanes-b-kind.md), "What the chassis cannot see or control for kagent-adk"
- [x] Freeze the `handle` contract and the chassis event schema as v1. · see criterion 8.

## Exit criteria

Each one has a scenario test in `tests/` or a recorded reason it cannot have one yet.

- [x] 1. Every new Python workload passes the `EnginePort` contract suite over A2A on localhost and in memory, and runs offline against the fake model server. · for `echo-smolagents` and `echo-claude-agent`: `tests/test_poc06b_remote_engine_contract.py::TestRemoteEngineOverA2A` binds `EngineConnectorContract` in the `remote` lane, over A2A on a Unix socket, behind the template server's bearer check (17 passed, 0 skipped; 6 suite cases per engine, 2 JSON and `traceparent` cases through the suite's own `json_values_handle` in the same lane, one answer check per engine, one registry control). Offline: smolagents on the fake model server's code-reply script, Claude on the `handle.query_fn` replay (the CLI never starts). In memory: none, on purpose; the `inprocess` mode would run untrusted code inside the chassis, so the in-memory half is the trusted engines in [6a](../poc-06a-bake-off-sidecar-lane/README.md) (`TestEngineInMemory`). On kind both pass smoke, simplifier, and lookup through the connector (`tests/test_poc06b_kind_engines.py`, run 38010290978, 74 passed). Scorecard pass table: `uv run python -m bakeoff run`.
- [x] 2. The non-Python agent and the remote solution pass the same contract suite, with no change to the chassis core. · offline: `tests/test_poc06b_plain_a2a_contract.py::TestPlainA2ARemoteLane` (the suite over a plain-A2A stub shaped like the kagent probe). Core is unchanged: `git diff --stat main -- packages/chassis/src/chassis/core packages/chassis/src/chassis/ports packages/chassis/schemas/events.v0.json` prints nothing. On kind, the real `kagent-adk` passes smoke, simplifier, and lookup through the plain-A2A mode, and the TypeScript agent passes as a remote (`tests/test_poc06b_kind_engines.py`, run 38010290978). The TypeScript agent passes the suite in [6a](../poc-06a-bake-off-sidecar-lane/README.md).
- [x] 3. Every shortlisted engine is scored on every criterion, with numbers where the criterion is measurable. · evidence: [scorecard](../poc-06a-bake-off-sidecar-lane/notes/2026-10-09-scorecard.md) and `../poc-06a-bake-off-sidecar-lane/tests/test_poc06a_scorecard_records.py`. Offline numbers. The `kagent-adk` cells (image, RSS, latency, tokens) wait on CI and are marked so, not blank.
- [ ] 4. Every supported engine passes the contract, load, and hostile suites, in its lane. · hostile and contract on kind: pass for every new engine (`tests/test_poc06b_kind_remote_controls.py`, `tests/test_poc06b_kind_sidecar_controls.py`, `tests/test_poc06b_kind_engines.py`; run 38010290978, 74 passed; [run table](notes/2026-10-09-lanes-b-kind.md)); the static half passes offline (`tests/test_poc06b_kind_static.py`). Still open, load: the PoC-4 matrix runs on Compose only; for the remote engines the plan allows a load run on kind. Load on kind: `tests/test_poc06b_kind_load.py`, numbers in [notes/2026-10-10-remote-load-on-kind.md](notes/2026-10-10-remote-load-on-kind.md) after the next CI run.
- [ ] 5. The token overhead against plain Python is known per engine, on a hosted big model. · waits on: the Mac run, `make poc06-mac`. Offline proxy: smolagents sends +1918% prompt bytes over a lookup, Claude +335% ([scorecard](../poc-06a-bake-off-sidecar-lane/notes/2026-10-09-scorecard.md), criterion 5).
- [x] 6. What the chassis cannot control is listed for the remote solution. · evidence: [contract v5](../../docs/contracts/contract-v5.md) B.8; [lanes-b-kind note](notes/2026-10-09-lanes-b-kind.md); [kagent probe](notes/2026-10-09-kagent-probe.md). Tool calls and state are invisible, the runtime checks no inbound bearer, usage is self-reported.
- [ ] 7. An ADR names the default engine, the supported engines with their lane, and the rejected engines with reasons. · [ADR-006](../../docs/planning/adr/006-agent-engines-default-supported-lanes.md) is written, Proposed. Waits on: the user's acceptance at the PR. The hosted and SLM numbers may reopen the default.
- [x] 8. The `handle` contract and the chassis event schema are frozen as v1, or the changes they needed are listed. · decided in [contract v5](../../docs/contracts/contract-v5.md) part C: frozen as the first stable line, `schema_version` stays `"0"`, no engine needed an event change (the "v1" is not the string `"1"`). Pinned by `packages/chassis/tests/test_contract_freeze.py` (44 tests): the sha256 of the five v0 schema files, the `handle` and `wire` signatures, the six events and their fields, the schema versions, the four A2A metadata keys, and every frozen error code with its `retryable` value, each checked by running its emitter.

## How to run

```bash
make test-poc POC=06b
make kind-poc06 ARGS=up        # then ARGS=test; kind and Docker, see deploy/kind/poc06/
```

## Demo

Not recorded yet. The offline bake-off demo is [in 6a](../poc-06a-bake-off-sidecar-lane/demo/README.md). The kind record is the CI run table in [notes/2026-10-09-lanes-b-kind.md](notes/2026-10-09-lanes-b-kind.md); no separate kind demo is recorded.

## Findings from the kind work

- **Threads under gVisor.** runsc with `oci-seccomp` turns RuntimeDefault's `clone3 -> ENOSYS` into EPERM (google/gvisor#14688; the fix is google/gvisor#14721 and is not released). So glibc 2.34 and later cannot create threads in a gVisor pod. PoC-6 gives its gVisor pods a Localhost seccomp profile, `profiles/poc06-runsc-clone3.json`, under the kubelet's seccomp root on each node. It is not a file in the repo: `deploy/kind/poc06/run.sh seccomp` derives it at `up` from the node's own containerd RuntimeDefault through `deploy/kind/poc06/seccomp/derive_profile.py`, with only `clone3` allowed. Details are in [notes/2026-10-09-lanes-b-kind.md](notes/2026-10-09-lanes-b-kind.md). With it, every PoC-6 gVisor pod starts and passes on kind (run 38010290978).
- **kagent-adk runs as uid 65532.** That is the uid of its upstream Dockerfile. The image is built from pinned source, and `packages/chassis/tests/test_image_uids.py` has an `UPSTREAM_UIDS` table for such images.

## Notes and decisions

Dated files in `notes/`: [Claude CLI capture](notes/2026-10-09-claude-cli-capture.md), [kagent probe](notes/2026-10-09-kagent-probe.md), [engines on kind](notes/2026-10-09-lanes-b-kind.md). [notes/backlog-changes.md](notes/backlog-changes.md) lists what the backlog should change.
