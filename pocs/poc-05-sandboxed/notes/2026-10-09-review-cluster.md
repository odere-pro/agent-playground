# PoC-5 code review of the cluster work, 2026-10-09

Read-only review by the `reviewer` agent of the T19 to T22 work as merged in PR #10 (commits b439163, 4f1222f, ec8ac64, 4267a9f). Diff only: nothing was run against the cluster. Verdict: **accept**. No CRITICAL or HIGH finding.

## Findings

| Sev | Where | Finding | Fix |
| --- | ----- | ------- | --- |
| MEDIUM | `deploy/kind/poc05/run.sh:213-218` | `replace_changed_job` replaces a finished Job only when its manifest changed. A `minio-init` that ended Failed with an unchanged manifest stays; `wait --for=condition=Complete` times out and `up` fails until the 600 s TTL. | Delete a Job whose conditions include `Failed` regardless of the dry run. |
| MEDIUM | `deploy/kind/poc05/run.sh:367` | `run.sh test` is not wrapped in `with_gateway`, so the code-runner and host-side gateway tests skip under it. | Run it through `with_gateway`, or make the `tools` fixture fail when `POC05_KIND=1` and the variables are missing. |
| MEDIUM | `pocs/poc-05-sandboxed/tests/test_poc05_kind_code_runner.py:121` | The 1.1.1.1 refusal has no control showing the address is reachable from the cluster; with no outbound route it passes with or without `egress: []`. | Reuse the unpoliced-caller control from `test_poc05_kind_remote_controls.py:313`. |
| MEDIUM | `test_poc05_kind_remote_controls.py:65`, `test_poc05_kind_hardreq1.py:43`, `test_poc05_kind_sidecar_controls.py:45` | `REFUSED_TCP` accepts `ConnectionRefusedError` and `OSError`. A policy drop is a timeout; a refusal means no listener, so a target that is down passes as refused by policy. Matters most for H30 (Valkey, code runner). | Expect `TimeoutError` for policy-dropped edges; keep `ConnectionRefusedError` only where it is the claim (H13 loopback). |
| LOW | `test_poc05_kind_remote_controls.py:138-154` | The caller fixture applies a fixed pod name; a pod left Failed or Terminating by an interrupted run breaks the next run. | `delete pod --ignore-not-found --wait=true` before apply. |
| LOW | `packages/chassis/src/chassis/adapters/mcp/gateway.py:180` | `_rpc_error` maps `_UNKNOWN_TOOL` but not `_DENIED`; a denial sent as a JSON-RPC error becomes retryable `tool_unavailable`. | Map `_DENIED` there too. |
| LOW | `gateway.py:352-361` | The refusal-text match runs on any tool's error text, so an upstream tool can choose the code. Messages stay fixed text; nothing leaks. | Record as a known limit in the module docstring. |
| LOW | `packages/chassis/tests/test_tool_gateway_contract.py:350` | The no-body check bites only for the second parametrized case. | Assert `text not in str(info.value)` for every case. |
| LOW | `test_poc05_kind_hardreq1.py:82`, `poc05_kind.py:67` | On failure the assertion prints 120 bytes of a LiteLLM reply; with the chassis's real key, a 401 echoes the key's last 4 characters and its hash. | Drop the reply from the message when a key is set, or redact `sk-` runs in the probe. |

## Risk notes

- `with-gateway` puts the chassis virtual key in the host pytest environment (not argv, not a file). Child processes inherit it; `kubectl` and `docker exec` do not forward it into containers. Its name does not end in `_API_KEY`, so the offline key stripping does not catch it. Acceptable for the operator host; platform-security sign-off to record.
- Mutation runs are recorded for T22 (`2026-10-08-remote-suite.md`), not for T21.
- H19 and the code runner's outside check need 1.1.1.1 reachable: a flake source in CI.
- `remote-lane.yml` is not on push or pull_request yet, so the CI half of criteria 1 and 2 has not moved (T24 waits on the checksum values).

## Criteria the evidence moves

3 (LiteLLM, MCP gateway, Valkey with a control through the chassis; MinIO network-only, stated), 4, 5, 6, 8 partly, and 2 on kind only (not in CI).

## Disposition

The MEDIUM and LOW fixes go to the kind agent after the step A1 verdict runs, one agent on the cluster at a time. Each fix is checked by rerunning the kind tier and `run.sh test-remote`.
