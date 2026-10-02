# What the chassis cannot see or control in the remote lane (2026-10-02)

Exit criterion 10. Task T29. Status: draft, written before the kind suites ran.

Each item names its threat-model id (H01 to H32, boundaries B1 to B12 in [the threat model](2026-10-02-threat-model.md)). Each says what the chassis sees instead, and who does see it. A claim cites a file, a test, or a note.

Decisions on 2026-10-02 that shape this note:

- T10, the in-pod probe workload, is dropped. Controls are checked from outside the pod: static manifest tests, `kubectl exec` of standard tools in our own pods, and the spike. Criterion 8 is "partly shown".
- PoC-5 runs no broker. H11 is a recorded exception ([the H11 note](2026-10-02-h11-queue-exception.md)).
- The cluster work could not run in the cloud container ([cloud host limits](2026-10-02-cloud-host-limits.md)). It runs on the user's Mac. The cluster facts below come from [the bring-up note](2026-10-02-bring-up.md) and [the spike](2026-10-02-kind-sandbox-spike.md), both run on that Mac.

## 1. The threat model's list (section 5)

| Blind spot | Ids | What the chassis sees instead | Who does see it |
| ---------- | --- | ----------------------------- | --------------- |
| The remote's own behavior: its code, threads, subprocesses, memory, and files | B9, H21 to H24, H28 | The A2A events the remote sends, and the model and tool calls it makes on 8091 | Nobody by default. `kubectl exec` into the pod; gVisor's own logs on the node |
| Syscalls and escape attempts | B9, H27, H28 | Nothing | gVisor (runsc) and seccomp on the node. The spike set `oci-seccomp = "true"` so runsc honors the pod's profile (spike, step 4). No Falco runs |
| Egress the chassis does not carry | B2, H19, H20, H30 | Nothing. A refused connection never reaches the chassis | The CNI. On kindnet nobody logs it (section 2) |
| Resource use and an OOMKill | B10, H22 to H24 | A failed or timed-out A2A run, and `/ready` going false | The kubelet: pod status (`lastState.terminated.reason`) and pod events |
| Calls a managed runtime makes with its own key (the ADR-001 item 8 relaxation) | H32, B11 | Nothing. Those calls skip the proxies, so no run budget and no trace correlation apply | LiteLLM's and the MCP gateway's logs, by that key |
| Abuse inside an allowed path: a legitimate tool call used to leak data, or DNS where DNS is allowed | B11, H20 | A normal tool call or model call, charged to the run | Nobody can tell use from abuse at this layer |
| The remote's identity beyond its token and its pod label | B8, H17, H18 | A good bearer token on 8091, and a run in flight (`RequireRun`) | Nobody. A compromised remote with a good token looks healthy. The label behind H18's NetworkPolicy edge can be forged by anyone who may create pods in `poc05-remote` ([security review](2026-10-02-review-security.md), F3) |

Sources: threat model section 5; [contract v4](../../../docs/contracts/contract-v4.md), "The remote listener"; plan section 5, "Where results are logged".

## 2. Added by PoC-5: what is not logged or not enforced

- **NetworkPolicy drops are not logged on kindnet (H18, H19, H20, H30).** kindnet runs kube-network-policies and enforces ingress and egress (spike, question 2). It writes no line for a dropped packet. The only record is the caller's timeout. Who would see it: a CNI with flow logs (suggested: Cilium with Hubble, or Calico flow logs). Source: plan section 5, "Where results are logged", item 4.
- **The trusted list is a hand-kept ConfigMap (B9, admission rule 4).** `trustedRepositories` in `agent-trust-params` (`deploy/kind/poc05/admission/params.yaml`) is typed by a platform admin. It is not registry metadata and not a signature. The chassis sees only `spec.trust` and reports it in `/manifest`. The API server sees the rule's refusal. Owner: 025 H-10 (contract v4, "Known gaps").
- **A third-party tool server may ignore the idempotency key (B11).** The chassis sends the key as an argument and in `_meta` (`packages/chassis/src/chassis/adapters/mcp/gateway.py`). The gateway does not forward `_meta` (bring-up, item 3). Our fake server and the code runner honor the argument. A server we do not own may not. Then a retried write runs twice, and the chassis cannot tell. Only that server's own logs show it.
- **The code runner's result cache is in memory (B11, B12).** `ResultCache` in `packages/code-runner/src/code_runner/server.py` keeps up to 256 results by idempotency key (`CACHE_SIZE`, suggested). A restart or a new sandbox loses it, and a retried call runs again. The oldest key is dropped past the size (`packages/code-runner/tests/test_code_runner_server.py::test_code_runner_forgets_the_oldest_key_past_the_cache_size`). Isolation between calls is per process, not per sandbox (security review, F5). The chassis sees one tool result per call.
- **DNS in the sidecar lane (H20).** The sidecar workload shares the pod's network namespace, so it inherits the chassis's DNS edge to kube-dns (security review, "DNS only where needed"). A name lookup can carry data out through kube-dns. The remote pod has no DNS at all: `getaddrinfo` fails and TCP to 10.96.0.10:53 times out (bring-up, item 7). Static check: `pocs/poc-05-sandboxed/tests/test_poc05_netpol_static.py::test_poc05_netpol_dns_only_for_the_listed_pods`. Nobody logs the lookups. That is one more reason untrusted code goes remote.
- **The metadata address cannot be shown refused on kind (H01, B3).** Nothing answers at 169.254.169.254 on Docker Desktop, even with no policy (spike, question 2 and "not proven"). A timeout there proves nothing. What PoC-5 has instead: a static check that no rule allows it (`test_poc05_netpol_static.py::test_poc05_netpol_no_rule_can_reach_metadata_or_api` and `::test_poc05_netpol_checks_bite_ipblock`), and this stated limit. The real proof needs a cloud node.
- **The PID limit is checked as set, not exhausted (H23, B10).** `podPidsLimit: 256` is in `deploy/kind/poc05/cluster.yaml` (line 23). Under gVisor, hitting it kills the whole sandbox and the pod restarts (`SandboxChanged`), instead of failing one `fork` (spike, question 6). So no PoC-5 check runs a process loop to the cap. The spike measured it on its own cluster. The code runner's `RLIMIT_NPROC` was shown in its pod: refused after 31 forks (bring-up, item 8). No PoC-5 test asserts the 256 in `cluster.yaml` yet (suggested: add a static check).

## 3. Added by PoC-5: what was not run, or stays open

- **H31, tag spoofing, not run.** The budget binds to the virtual key, not the `agent:<name>` tag a caller sends: `seed.sh` puts the tag in the key's `metadata.tags` (plan section 2.9; bring-up, item 2). No test sends a spoofed tag. 004 G-2's criterion "counted under the agent of its key" stays open. LiteLLM sees the key; the chassis sees the run.
- **H32, a managed runtime with its own key, not run.** It is PoC-6b's (plan section 5, last paragraph). See section 1: the chassis sees none of those calls.
- **B12, per-caller scope, open (PoC-8).** One Valkey ACL user with `~*` serves every caller (security review, B table). Tool idempotency keys are scoped to agent and request (`packages/chassis/tests/test_tool_idempotency.py`, M1), not to a caller. Do not claim B12 closed.
- **The TypeScript template has no token flag.** `packages/workload-a2a` has `--require-token-env` and `--previous-token-env` (T05). `echo-typescript` only sends `CHASSIS_API_TOKEN` on its outbound model call (`packages/workloads/echo-typescript/README.md`). Its A2A server checks no inbound token. So a TypeScript engine cannot be a remote yet (H17's workload half). Plan section 2.4 moves it to PoC-6.
- **In-pod checks not run (T10 dropped; criterion 8 partly shown).** The probe workload `packages/workloads/hostile` is an empty skeleton. Every check in plan section 5's table was meant to run from inside a workload container. What remains:
  - Some cluster evidence from `kubectl exec` in our own pods: H19 and H20 from remote-echo (no DNS, kube-dns times out; no literal public IP was tried; bring-up, item 7), H21 and H28 (read-only root and the gVisor marker; bring-up, item 8), H23 for the code runner only (`RLIMIT_NPROC`; bring-up, item 8).
  - Service-side refusals on the cluster, with their controls, but the note does not say they came from a workload container: H05 to H08 (bring-up, items 2 and 3) and H10 (item 5).
  - Offline evidence only: H08, H13, H15, H16, H29 (`test_poc05_hostile_offline.py`), H14 and H17 (`test_poc05_remote_lane_offline.py`, `packages/chassis/tests/test_remote_auth.py`, `packages/chassis/tests/test_server_cli.py`).
  - Static checks of the control only, with no in-pod run: H01 to H04, H09, H12, H18, H22, H24 to H27, H30. The checks read the manifests (`test_poc05_hardening_static.py`, `test_poc05_netpol_static.py`, `test_poc05_seed_static.py`); only H26, H05, H07, H10, and H12 are named by id in those files.
  - The H11 exception's closing step names "the probe workload's pod". With T10 dropped, that step needs another caller (suggested: a `kubectl exec` from the sidecar workload container).
- **A refused model route reaches the remote as HTTP 500 `http_401` (H29).** LiteLLM refuses a route the key does not list. The adapter raises `ModelError("http_401", retryable=False)`, and the model proxy sends every non-retryable model error as 500. The remote cannot tell "you may not" from "the chassis broke". Test: `test_poc05_hostile_offline.py::test_h29_a_route_outside_the_key_is_refused_and_an_in_scope_call_is_200` (asserts `>= 400` and `http_401`). Open question in contract v4, owner `chassis-architect` (suggested: PoC-6).
- **No queue in PoC-5 (H11).** No broker runs on kind. Every PoC-5 agent config keeps `events: none` (`test_poc05_events_agnostic.py::test_poc05_agent_configs_run_no_broker`). No test on any stack shows a broker refusing a client without a credential. The PoC-4 Compose Kafka has no auth. Owner: `platform-security`, closing in 020 X-8 ([the H11 note](2026-10-02-h11-queue-exception.md)).

## 4. What this note does not cover

- The gVisor overhead (criterion 9) is in its own note (T23, not written yet).
- The F6 restart window for B7 (the chassis restarts, and who owns 8080 and 8090 meanwhile) is in the security review's cluster list, item 12. It was not run.
