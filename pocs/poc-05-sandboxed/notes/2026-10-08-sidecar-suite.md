# PoC-5 sidecar suite on kind (T21, 2026-10-08)

Exit criteria 3, 4, 5, and 7 (sidecar half) on `kind-poc05`, against the `agent-echo` pod (chassis native sidecar plus echo-python). Every check runs from outside the pod: `kubectl exec` of the image's own Python, the live pod spec, or the node's own curl. Every refusal asserts its paired allowed control in the same test. No probe workload (T10 dropped), no value of any Secret in a file, argv, or output: credentials are named by env variable and read inside the chassis container.

Files: `tests/test_poc05_kind_hardreq1.py`, `tests/test_poc05_kind_sidecar_controls.py`, and the shared helper `tests/poc05_kind.py`. The key wrapper is now `deploy/kind/poc05/run.sh with-gateway CMD...` (shellcheck clean).

## Results per H id

| Check | Criterion | Test | Result | The control refuses | The paired allowed control |
| ----- | --------- | ---- | ------ | ------------------- | -------------------------- |
| Hard req 1, LiteLLM | 3 | `test_litellm_refuses_the_workload_without_the_chassis_key` | pass | a chat call from the workload with no key and with a key LiteLLM did not issue (401) | the same call through the chassis's model proxy (127.0.0.1:8090) is 200; from the chassis container with its own key, 200 |
| Hard req 1, MCP gateway (H07) | 3 | `test_mcp_gateway_refuses_the_workload_without_the_chassis_key` | pass | the MCP handshake from the workload with no key and a stranger key (401) | through the chassis's `/mcp`, `tools/list` holds `fake_tools-glossary_lookup`; the chassis's key at the gateway lists it too |
| Hard req 1, Valkey | 3 | `test_valkey_refuses_the_workload_without_the_chassis_password` | pass | PING with no AUTH (`-NOAUTH`), AUTH as `chassis` with a wrong password (`-WRONGPASS`) | from the chassis container, AUTH with its own user and password, then PING: `+OK`, `+PONG` |
| Hard req 1, MinIO | 3 | `test_minio_refuses_the_workload_and_any_unsigned_call` | pass | the workload's TCP connection to the MinIO Service and pod IP (no edge, review F10); an unsigned ListBuckets inside MinIO's pod (403) | the workload's edge to LiteLLM connects in the same call; `mc ls local/agent-configs` signed with the platform's credential exits 0 |
| H11 (broker) | 3 | none | exception | | recorded exception, `notes/2026-10-02-h11-queue-exception.md` |
| H02 | 4 | `test_h02_no_service_account_token_in_the_workload` | pass | a token directory: `automountServiceAccountToken: false`, `/var/run/secrets/kubernetes.io` absent in the workload | the same path check sees the kubelet-mounted `/etc/hosts` |
| H25 | 4 | `test_h25_no_secret_value_reaches_the_workload` | pass | any Secret in the workload: no `secretKeyRef`, no `envFrom` Secret, no Secret or token volume; live env has neither `LITELLM_API_KEY` nor `VALKEY_PASSWORD` | the chassis container's spec references exactly those two, and its live env has both names (names only) |
| H26 | 4 | `test_h26_chassis_environ_not_readable_from_the_workload` | pass | the chassis's `/proc/<pid>/environ`: no `shareProcessNamespace`; the workload's /proc shows only its own processes (pid 1 is `workload-a2a`), none holding a chassis Secret name | the same scan in the chassis container finds `chassis serve` with both names in a readable environ |
| H13 (kind half) | 5 | `test_h13_public_port_refused_on_loopback_served_on_the_pod_ip` | pass | 127.0.0.1:8080 from the workload (`ConnectionRefusedError`) | POD_IP:8080/health is 200; 127.0.0.1:8090 connects |
| H03, H04 | 5 | `test_h03_h04_kubernetes_api_unreachable_from_the_workload` | pass | the workload's connection to the node IP 172.18.0.2:6443 (H04) and the Service VIP 10.96.0.1:443 (H03) (timeout) | the node's own curl to `https://172.18.0.2:6443/livez` answers `ok`; the workload's edge to LiteLLM connects in the same call |
| H01 | 5 | `test_h01_metadata_address_denied` | pass, with a limit | egress to 169.254.169.254: no live NetworkPolicy in a `poc05-*` namespace has an `ipBlock` holding it or an egress rule without `to`, so default deny applies; the live connection does not open | `default-deny` exists in poc05-agents, -platform, -remote, -tools; the workload's allowed edge connects |
| Admission | 7 | `test_poc05_kind_admission.py` (51 cases) | pass | every rejected fixture, by `agent-trust-rule` with its rule message | every admitted twin; RBAC controls |

## Limits and observations

- H01: nothing listens at 169.254.169.254 on kind, so the live refusal alone proves nothing. The evidence is the static check of the live policies. A cloud cluster with a real metadata service must rerun the live half.
- MinIO: no chassis holds a MinIO key in PoC-5 (`config: memory`, review F10). The "same call through the chassis" half is not possible yet. The control used is the platform's credential inside MinIO's own pod. It returns with the first `config: s3` chassis, which also adds the edge back.
- H26: `/proc/1/environ` is readable from the workload, but it is the workload's own process, not the chassis's. The test names pid 1 and checks every visible process.
- The workload's env holds the `KUBERNETES_SERVICE_*` variables. The kubelet sets them even with `enableServiceLinks: false`. They are addresses, not credentials, and the address is refused (H03).
- The chassis's `/mcp` passes the gateway's `<server>-<tool>` names on unchanged (`fake_tools-glossary_lookup`).
- No missing control was found. No manifest or code fix was needed.

## Mutation check

Each test was broken once in a temporary copy (`test_poc05_kind_zzmutant.py`, deleted after) and failed every time. Examples: the bare LiteLLM call pointed at the proxy; the bare gateway handshake given the chassis's key (`auth_env`); the token path set to `/etc`; loopback 8080 changed to 8090; the workload env and `/proc` scans run in the chassis container; the API address swapped for the proxy; the metadata probe swapped for the allowed edge; the `default-deny` name misspelled.

```text
litellm | 1 failed, 3 deselected in 0.70s
mcp_gateway | 1 failed, 3 deselected in 0.62s
valkey | 1 failed, 3 deselected in 0.70s
minio | 1 failed, 3 deselected in 3.77s
h02 | 1 failed, 5 deselected in 0.58s
h13 | 1 failed, 5 deselected in 0.62s
h25 | 1 failed, 5 deselected in 0.97s
h26 | 1 failed, 5 deselected in 0.99s
h03 | 1 failed, 5 deselected in 3.84s
h01 | 1 failed, 5 deselected in 0.81s
h01 | 1 failed, 5 deselected in 0.23s
```

## Runs

The new files on their own:

```text
$ POC05_KIND=1 uv run pytest -m network pocs/poc-05-sandboxed/tests/test_poc05_kind_hardreq1.py -q -rs
4 passed in 9.65s
$ POC05_KIND=1 uv run pytest -m network pocs/poc-05-sandboxed/tests/test_poc05_kind_sidecar_controls.py -q -rs
6 passed in 12.80s
$ POC05_KIND=1 uv run pytest -m network pocs/poc-05-sandboxed/tests/test_poc05_kind_admission.py -q -rs
51 passed in 4.19s
```

The kind tier, through the wrapper (`grep -c 'sk-'` on the log: 0):

```text
$ POC05_KIND=1 deploy/kind/poc05/run.sh with-gateway uv run pytest -m network pocs/poc-05-sandboxed/tests -q -rs
SKIPPED [1] packages/contract-suites/src/chassis_contracts/tool.py:157: provide other_write_arguments: valid write_call arguments of another value
SKIPPED [1] packages/contract-suites/src/chassis_contracts/tool.py:204: provide a make_unavailable fixture: the next call fails as unavailable
73 passed, 2 skipped, 353 deselected in 32.23s
```

The offline gate (the 10 new kind tests skip there, so 208 skipped became 218):

```text
$ PATH=/opt/homebrew/bin:$PATH make check
==== 2424 passed, 218 skipped, 6 xfailed, 15 warnings in 227.57s (0:03:47) =====
cd docs/planning && python3 tools/check.py
127 issues, 113 epic stories
OK
uv run python scripts/harness_lint.py
harness-lint: ok
```
