# poc-harness

Test support that more than one PoC folder, or a PoC demo, uses. It replaces the `sys.path` inserts that let a later PoC import an earlier PoC's harness. Not product code: nothing in `packages/*/src` imports it (`make lint` enforces it).

## Modules

| Module | What it holds |
| ------ | ------------- |
| `paths` | `ROOT`, the repo root, and the paths of the fake model script and the TypeScript echo. |
| `scenario` | The scripted prompts and answers, the route, the URLs a workload calls, `TIMEOUT_S`, the Python engines, `engine_params`. |
| `apps` | The chassis app with injected ports (`chassis_app`, `chassis_config`, `bundle_for`, `proxy_for`), `running`, `client_for`, `run_handle`. |
| `outbound` | A workload's outbound HTTP routed into an ASGI app and recorded (`route_outbound`, `patch_outbound`), and the workloads' MCP hooks (`point_tools_at`). |
| `sockets` | `on_unix_socket`, `serve_tcp`, `require_tcp`, `uds_transport`, `free_port`. |
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
| `drills` | The named marks: `POC04_STACK`, `POC04_KIND`, `POC05_KIND`, `POC06_KIND`. |
| `waits` | `wait_until` and `wait_until_async`: poll, and fail on timeout. |

## Use

```python
from poc_harness.apps import chassis_app, running
from poc_harness.outbound import route_outbound
```

Tests: `scripts/check_offline.sh packages/poc-harness`.
