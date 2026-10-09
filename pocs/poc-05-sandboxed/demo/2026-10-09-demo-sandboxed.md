# PoC-5 demo: two lanes, the probes refused, their controls allowed (kind, cluster poc05)

Recorded 2026-10-09T15:04:29Z by pocs/poc-05-sandboxed/demo/demo.sh.
kubectl v1.36.1, Darwin arm64.

```console

## 0. The cluster is up

$ kctl -n poc05-agents get deployment agent-echo chassis-echo-remote
NAME                  READY   UP-TO-DATE   AVAILABLE   AGE
agent-echo            1/1     1            1           24h
chassis-echo-remote   1/1     1            1           24h

## 1. A normal request through each lane

$ /Users/aleksandrderechei/Git/agent-orchestration/deploy/kind/poc05/run.sh request
agent-echo           "hello"    HTTP 200  {"status":"ok","output":{"text":"ok"},"error":null}
agent-echo           "glossary" HTTP 200  {"status":"ok","output":{"text":"From the glossary: SLM means small language model.","tool_calls":[{"call_id":"call_bb9aa7a1","name":"glossary_lookup","arguments":{"term":"SLM"},"result":{"term":"SLM","definition":"A small language model: a model small enough to run cheaply on one GPU or a CPU."}}]},"error":null}
chassis-echo-remote  "hello"    HTTP 200  {"status":"ok","output":{"text":"ok"},"error":null}
chassis-echo-remote  "glossary" HTTP 200  {"status":"ok","output":{"text":"From the glossary: SLM means small language model.","tool_calls":[{"call_id":"call_4da2c0b3","name":"glossary_lookup","arguments":{"term":"SLM"},"result":{"term":"SLM","definition":"A small language model: a model small enough to run cheaply on one GPU or a CPU."}}]},"error":null}

## 2. The remote probe: H25, H19, H21, H23, H08, H17

$ run.sh with-gateway env POC05_KIND=1 uv run pytest -m network -q -rs pocs/poc-05-sandboxed/tests/test_poc05_kind_remote_controls.py::test_remote_holds_one_secret_its_own_token pocs/poc-05-sandboxed/tests/test_poc05_kind_remote_controls.py::test_h19_remote_reaches_no_outside_address pocs/poc-05-sandboxed/tests/test_poc05_kind_remote_controls.py::test_h21_root_is_read_only_and_tmp_is_writable pocs/poc-05-sandboxed/tests/test_poc05_kind_remote_controls.py::test_h23_pids_cap_is_the_configured_limit pocs/poc-05-sandboxed/tests/test_poc05_kind_tool_gateway.py::test_unlisted_probe_is_refused_with_its_control pocs/poc-05-sandboxed/tests/test_poc05_kind_remote_controls.py::test_h17_listener_answers_only_the_remote_token_inside_a_run
......                                                                   [100%]
6 passed in 11.59s

H25                                | remote pod reads a Secret other than its token       | refused: none mounted    | control: its own CHASSIS_API_TOKEN is there
H19                                | remote pod TCP to 1.1.1.1:443 and :80                | refused: policy drop     | control: an unpoliced caller reaches 1.1.1.1
H21                                | remote pod writes to /var/tmp on /                   | refused: EROFS           | control: the same write to /tmp works
H23                                | remote pod processes past podPidsLimit               | capped: pids.max=256     | control: pids.current above zero, under the cap
H08                                | call the unlisted probe tool                         | refused: not listed      | control: glossary_lookup answers
H17                                | model call to 8091, no or wrong token, or outside a run | refused: 401, 403        | control: own token inside a run: 200

## 3. A normal request again: both lanes still answer

$ /Users/aleksandrderechei/Git/agent-orchestration/deploy/kind/poc05/run.sh request
agent-echo           "hello"    HTTP 200  {"status":"ok","output":{"text":"ok"},"error":null}
agent-echo           "glossary" HTTP 200  {"status":"ok","output":{"text":"From the glossary: SLM means small language model.","tool_calls":[{"call_id":"call_9e4f09b1","name":"glossary_lookup","arguments":{"term":"SLM"},"result":{"term":"SLM","definition":"A small language model: a model small enough to run cheaply on one GPU or a CPU."}}]},"error":null}
chassis-echo-remote  "hello"    HTTP 200  {"status":"ok","output":{"text":"ok"},"error":null}
chassis-echo-remote  "glossary" HTTP 200  {"status":"ok","output":{"text":"From the glossary: SLM means small language model.","tool_calls":[{"call_id":"call_03290b21","name":"glossary_lookup","arguments":{"term":"SLM"},"result":{"term":"SLM","definition":"A small language model: a model small enough to run cheaply on one GPU or a CPU."}}]},"error":null}

## 4. The sidecar probe: H05 and H07 from the workload, then the in-pod probe suite

$ run.sh with-gateway env POC05_KIND=1 uv run pytest -m network -q -rs pocs/poc-05-sandboxed/tests/test_poc05_kind_hardreq1.py::test_litellm_refuses_the_workload_without_the_chassis_key pocs/poc-05-sandboxed/tests/test_poc05_kind_hardreq1.py::test_mcp_gateway_refuses_the_workload_without_the_chassis_key
..                                                                       [100%]
2 passed in 2.21s

H05                                | workload calls LiteLLM, no or wrong key              | refused: 401             | control: through the chassis proxy: 200
H07                                | workload opens the MCP gateway, no or wrong key      | refused: 401, no tool    | control: through chassis /mcp: tool listed
T10 | - | exception: in-pod probe not built (WIP) | notes/2026-10-09-t10-probe-exception.md

## 5. Admission: every fixture through a server dry run

$ run.sh with-gateway env POC05_KIND=1 uv run pytest -m network -q -rs pocs/poc-05-sandboxed/tests/test_poc05_kind_admission.py::test_fixture_outcome_matches_its_header
...............................................................          [100%]
63 passed in 4.32s

kind-cronjob/admitted              | apply --dry-run=server of the fixture                | as its header says       | control: the fixture's admitted twin
kind-cronjob/rejected              | apply --dry-run=server of the fixture                | as its header says       | control: the fixture's admitted twin
kind-deployment/admitted           | apply --dry-run=server of the fixture                | as its header says       | control: the fixture's admitted twin
kind-deployment/rejected           | apply --dry-run=server of the fixture                | as its header says       | control: the fixture's admitted twin
kind-sandbox/admitted              | apply --dry-run=server of the fixture                | as its header says       | control: the fixture's admitted twin
kind-sandbox/rejected              | apply --dry-run=server of the fixture                | as its header says       | control: the fixture's admitted twin
kind-sandbox-template/admitted     | apply --dry-run=server of the fixture                | as its header says       | control: the fixture's admitted twin
kind-sandbox-template/rejected     | apply --dry-run=server of the fixture                | as its header says       | control: the fixture's admitted twin
rule1-trust-label/admitted         | apply --dry-run=server of the fixture                | as its header says       | control: the fixture's admitted twin
rule1-trust-label/rejected         | apply --dry-run=server of the fixture                | as its header says       | control: the fixture's admitted twin
rule2-untrusted-sidecar/admitted   | apply --dry-run=server of the fixture                | as its header says       | control: the fixture's admitted twin
rule2-untrusted-sidecar/rejected   | apply --dry-run=server of the fixture                | as its header says       | control: the fixture's admitted twin
rule3-registry-prefix/admitted     | apply --dry-run=server of the fixture                | as its header says       | control: the fixture's admitted twin
rule3-registry-prefix/rejected-docker-hub | apply --dry-run=server of the fixture                | as its header says       | control: the fixture's admitted twin
rule3-registry-prefix/rejected-short-name | apply --dry-run=server of the fixture                | as its header says       | control: the fixture's admitted twin
rule3-registry-prefix/rejected     | apply --dry-run=server of the fixture                | as its header says       | control: the fixture's admitted twin
rule4-trusted-repository/admitted  | apply --dry-run=server of the fixture                | as its header says       | control: the fixture's admitted twin
rule4-trusted-repository/rejected  | apply --dry-run=server of the fixture                | as its header says       | control: the fixture's admitted twin
rule5-remote-shape/admitted        | apply --dry-run=server of the fixture                | as its header says       | control: the fixture's admitted twin
rule5-remote-shape/rejected-runtime-class | apply --dry-run=server of the fixture                | as its header says       | control: the fixture's admitted twin
rule5-remote-shape/rejected-sa-token | apply --dry-run=server of the fixture                | as its header says       | control: the fixture's admitted twin
rule5-remote-shape/rejected-secret | apply --dry-run=server of the fixture                | as its header says       | control: the fixture's admitted twin
rule6a-one-chassis/admitted        | apply --dry-run=server of the fixture                | as its header says       | control: the fixture's admitted twin
rule6a-one-chassis/rejected        | apply --dry-run=server of the fixture                | as its header says       | control: the fixture's admitted twin
rule6b-chassis-command/admitted    | apply --dry-run=server of the fixture                | as its header says       | control: the fixture's admitted twin
rule6b-chassis-command/rejected    | apply --dry-run=server of the fixture                | as its header says       | control: the fixture's admitted twin
rule6c-secret-in-workload/admitted | apply --dry-run=server of the fixture                | as its header says       | control: the fixture's admitted twin
rule6c-secret-in-workload/rejected | apply --dry-run=server of the fixture                | as its header says       | control: the fixture's admitted twin
rule7a-namespace-shape/admitted    | apply --dry-run=server of the fixture                | as its header says       | control: the fixture's admitted twin
rule7a-namespace-shape/rejected    | apply --dry-run=server of the fixture                | as its header says       | control: the fixture's admitted twin
rule7b-role-label/admitted         | apply --dry-run=server of the fixture                | as its header says       | control: the fixture's admitted twin
rule7b-role-label/rejected         | apply --dry-run=server of the fixture                | as its header says       | control: the fixture's admitted twin
rule7c-own-token/admitted          | apply --dry-run=server of the fixture                | as its header says       | control: the fixture's admitted twin
rule7c-own-token/rejected          | apply --dry-run=server of the fixture                | as its header says       | control: the fixture's admitted twin
rule7d-tool-gvisor/admitted        | apply --dry-run=server of the fixture                | as its header says       | control: the fixture's admitted twin
rule7d-tool-gvisor/rejected        | apply --dry-run=server of the fixture                | as its header says       | control: the fixture's admitted twin
rule8-pull-policy/admitted         | apply --dry-run=server of the fixture                | as its header says       | control: the fixture's admitted twin
rule8-pull-policy/rejected-if-not-present | apply --dry-run=server of the fixture                | as its header says       | control: the fixture's admitted twin
rule8-pull-policy/rejected         | apply --dry-run=server of the fixture                | as its header says       | control: the fixture's admitted twin
ruleC1-claim-env/admitted          | apply --dry-run=server of the fixture                | as its header says       | control: the fixture's admitted twin
ruleC1-claim-env/rejected          | apply --dry-run=server of the fixture                | as its header says       | control: the fixture's admitted twin
ruleC2-claim-pod-metadata/admitted | apply --dry-run=server of the fixture                | as its header says       | control: the fixture's admitted twin
ruleC2-claim-pod-metadata/rejected | apply --dry-run=server of the fixture                | as its header says       | control: the fixture's admitted twin
ruleC3-claim-pool/admitted         | apply --dry-run=server of the fixture                | as its header says       | control: the fixture's admitted twin
ruleC3-claim-pool/rejected         | apply --dry-run=server of the fixture                | as its header says       | control: the fixture's admitted twin
ruleC4-claim-lifecycle/admitted    | apply --dry-run=server of the fixture                | as its header says       | control: the fixture's admitted twin
ruleC4-claim-lifecycle/rejected-no-lifecycle | apply --dry-run=server of the fixture                | as its header says       | control: the fixture's admitted twin
ruleC4-claim-lifecycle/rejected-no-shutdown-policy | apply --dry-run=server of the fixture                | as its header says       | control: the fixture's admitted twin
ruleC4-claim-lifecycle/rejected-no-shutdown-time | apply --dry-run=server of the fixture                | as its header says       | control: the fixture's admitted twin
ruleC4-claim-lifecycle/rejected    | apply --dry-run=server of the fixture                | as its header says       | control: the fixture's admitted twin
ruleC5-claim-volumes/admitted      | apply --dry-run=server of the fixture                | as its header says       | control: the fixture's admitted twin
ruleC5-claim-volumes/rejected      | apply --dry-run=server of the fixture                | as its header says       | control: the fixture's admitted twin
ruleT1-network-policy-unmanaged/admitted | apply --dry-run=server of the fixture                | as its header says       | control: the fixture's admitted twin
ruleT1-network-policy-unmanaged/rejected-absent | apply --dry-run=server of the fixture                | as its header says       | control: the fixture's admitted twin
ruleT1-network-policy-unmanaged/rejected | apply --dry-run=server of the fixture                | as its header says       | control: the fixture's admitted twin
ruleT2-env-injection/admitted      | apply --dry-run=server of the fixture                | as its header says       | control: the fixture's admitted twin
ruleT2-env-injection/rejected      | apply --dry-run=server of the fixture                | as its header says       | control: the fixture's admitted twin
ruleT3-volume-claim-templates/admitted | apply --dry-run=server of the fixture                | as its header says       | control: the fixture's admitted twin
ruleT3-volume-claim-templates/rejected | apply --dry-run=server of the fixture                | as its header says       | control: the fixture's admitted twin
ruleT4-template-volumes/admitted   | apply --dry-run=server of the fixture                | as its header says       | control: the fixture's admitted twin
ruleT4-template-volumes/rejected   | apply --dry-run=server of the fixture                | as its header says       | control: the fixture's admitted twin
ruleT5-template-network-policy/admitted | apply --dry-run=server of the fixture                | as its header says       | control: the fixture's admitted twin
ruleT5-template-network-policy/rejected | apply --dry-run=server of the fixture                | as its header says       | control: the fixture's admitted twin

## 6. The code runner: run_python on gVisor, no egress

$ run.sh with-gateway env POC05_KIND=1 uv run pytest -m network -q -rs pocs/poc-05-sandboxed/tests/test_poc05_kind_code_runner.py::test_code_runs_on_gvisor_with_no_egress
.                                                                        [100%]
1 passed in 7.01s

H28                                | run_python TCP to outside, LiteLLM, dispatcher, API  | refused: policy drop     | control: its own 127.0.0.1:8000; each target up from an allowed peer

## 7. H11, the broker: Kafka with SASL

$ run.sh with-gateway env POC05_KIND=1 uv run pytest -m network -q -rs pocs/poc-05-sandboxed/tests/test_poc05_kind_hardreq1.py -k kafka
.                                                                        [100%]
1 passed, 4 deselected in 2.42s

-                                  | test_kafka_refuses_the_workload_without_the_chassis_credential | ok                       | control: in the test docstring

## 8. Where it was logged (pod logs through redact_logs; no secret values)

probe.check                          0 line(s)
chassis-echo-remote 401/403          8 line(s)
INFO:     10.244.0.82:45411 - "POST /v1/chat/completions HTTP/1.1" 401 Unauthorized
INFO:     10.244.0.82:63635 - "POST /v1/chat/completions HTTP/1.1" 403 Forbidden
INFO:     10.244.0.82:49244 - "POST /v1/chat/completions HTTP/1.1" 403 Forbidden
LiteLLM 401                          6 line(s)
INFO:     10.244.0.77:42992 - "POST /mcp/ HTTP/1.1" 401 Unauthorized
15:04:49 - LiteLLM Proxy:ERROR: auth_exception_handler.py:182 - litellm.proxy.proxy_server.user_api_key_auth(): Exception occured - Authentication Error, Invalid proxy server token passed. key=5a84ad5
INFO:     10.244.0.77:43004 - "POST /mcp/ HTTP/1.1" 401 Unauthorized

result: every step ok; T10 recorded exception
```
