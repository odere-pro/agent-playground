# poc-harness

Test support that more than one PoC folder, or a PoC demo, uses. It replaces the `sys.path` inserts that let a later PoC import an earlier PoC's harness. Not product code: nothing in `packages/*/src` imports it (`make lint` enforces it).

## Modules

| Module | What it holds |
| ------ | ------------- |
| `paths` | `ROOT`, the repo root, and the paths of the fake model script and the TypeScript echo. |
| `scenario` | The scripted prompts and answers, the route, the URLs a workload calls, `TIMEOUT_S`, the Python engines, `engine_params`. |
| `apps` | The chassis app with injected ports (`chassis_app`, `chassis_config`, `bundle_for`, `proxy_for`), `running`, `client_for`, `run_handle`. |
| `outbound` | A workload's outbound HTTP routed into an ASGI app and recorded (`route_outbound`, `patch_outbound`), and the workloads' MCP hooks (`point_tools_at`). |
| `sockets` | `socket_path`, `on_unix_socket` (thread), `on_unix_socket_on_this_loop` (the test's loop), `serve_on_this_loop`, `serve_tcp`, `require_tcp`, `uds_transport`, `free_port`. |
| `servers` | `a2a_on_unix_socket` (the workload template server) and `chassis_tools_on_unix_socket`. |
| `scripted_model` | `ToolLoopModel`: calls the tool once, then answers. |
| `typescript` | The TypeScript echo as a child process (`typescript_echo`, `typescript_echo_on_unix_sockets`). |
| `mcp_client` | A plain JSON-RPC MCP client (`mcp_request`, `mcp_session`, `glossary_over_mcp`). |
| `interfaces` | A real chassis on Unix sockets for one engine and lane (`chassis_on_unix_sockets`), `OutboundRouter`, `ENGINES`, `AGENT`, `LANES`. |
| `sse` | `sse_frames` (`(event, payload)` pairs), `sse_data` (payloads alone), and `sse_events` (names optional, non-JSON data kept): thin wrappers over `chassis_contracts.inbound.sse`. |
| `plain_a2a` | A plain A2A stub agent (no chassis), for the remote connector tests and PoC-6b. |
| `replicas` | N chassis behind one store (`replicas_on_unix_sockets`, `Replicas`, `replica_config`). |
| `replica_doubles` | `HarnessModel`, `SeverableState`, `RecordingTaskStore`, `OffsetClock`, and the `SLOW`, `HANG`, `FAIL` prompts. |
| `replica_calls` | One call through any interface (`call`, `Reply`), `without_created`. |
| `plugin` | The `requires_env` drill gate, a pytest plugin loaded by entry point. |
| `drills` | The named marks: `POC04_STACK`, `POC04_KIND`, `POC05_KIND`, `POC06_KIND`. `required_env`: a variable a drill needs, or a failure. |
| `lane_tokens` | Who holds which token in a `remote` lane (`Tokens`), `new_token`, `new_trace_id`, `traceparent` (the header value). |
| `remote_lane` | The `remote` lane in one process (`remote_lane`, `RemoteLane`, `GatedTools`, `workload_server`), and the `LaneContract` factories. |
| `kind` | kubectl helpers pinned to `kind-poc05`, `exec_probe` (`ExecFailed`), `probe`, `node_sh`, `unpoliced_caller`, `tcp_check`. |
| `pod_probe` | `PROBE`, the stdlib script that checks controls from inside a pod. |
| `kind_controls` | What the sidecar and remote suites share: `secret_refs`, `secret_sources`, `metadata_stand_in`, `node_reaches_its_api`, `traceparent_headers` (a header map). |
| `manifests` | `load_manifests`, `containers_of`, `container_secret_names`, `secret_names_of`. |
| `admission_manifests` | The admission objects and fixtures as files, the RBAC model (`can_i`, `CAN_I`). |
| `admission_model` | A Python model of the admission rules (`AdmissionParams`, `broken_rules`, `all_broken_rules`). |
| `netpol_model` | The NetworkPolicies parsed from `deploy/kind/poc05/`. |
| `netpol_checks` | The checks over that model, each returning a list of problems. |
| `workflows` | GitHub workflow readers (`load_workflow`, `workflow_steps`, `workflow_runs`, `workflow_triggers`), `PINNED`, `SHA256`. |
| `bakeoff_tasks` | The bake-off tasks and pass checks (`TASKS`, `CHECKS`, `Verdict`), `ENGINES` (trust and lane from `bakeoff.registry`, plus the test hooks), `collect`, `two_tool_stub`. |
| `sidecar_lane` | An engine behind a lane's connector, against fake backends on Unix sockets (`serve_backends`, `wire_python_engine`, `connected`, `run_task`, `typescript_agent`). `tool_uds_preload.mjs` is the TypeScript agent's preload. |
| `sidecar_fixtures` | The fixtures of the sidecar-lane suites (`backends`, `typescript`, `address`, `wired`); a conftest imports them. |
| `bakeoff_kind` | The bake-off engines on kind (`ENGINES`, built by `kind_engine` from `bakeoff.registry`), `run_stream`, `gateway_view`, `probe_in`, the missing-profile pod. `node_probe.js` is the probe for an image with no Python. |
| `waits` | `wait_until` and `wait_until_async`: poll, and fail on timeout. |

## Use

```python
from poc_harness.apps import chassis_app, running
from poc_harness.outbound import route_outbound
```

Tests: `scripts/check_offline.sh packages/poc-harness`.
