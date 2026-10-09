# PoC-5 remote suite on kind (T22, 2026-10-08)

Exit criteria 2, 4, 6, and 8 (8 partly shown: T10, the in-pod probe workload, is dropped) on `kind-poc05`, against `chassis-echo-remote` and the `remote-echo` Sandbox on gVisor, and the `code-runner` Sandbox. Every check runs from outside the pod: `kubectl exec` of echo-python's own Python, the live pod spec, the node's cgroup and containerd (`docker exec poc05-control-plane`), or the code runner's own tool with ordinary code. Every refusal asserts its paired allowed control in the same test. No value of any Secret is in a file, argv, or output: the remote's token is named by its env variable (`auth_env`) and read inside its own container.

Files: `tests/test_poc05_kind_remote_lane.py`, `tests/test_poc05_kind_remote_controls.py`, `tests/test_poc05_kind_code_runner.py`. The shared helper `tests/poc05_kind.py` gained four probe kinds (`read`, `write` with the errno name, `dns`: one A query over UDP to a literal server, `resolve`: the pod's own resolver) and `node_sh` (read-only `docker exec` on the kind node). `deploy/kind/poc05/run.sh test-remote` now runs through `with_gateway`, since the code-runner file calls the tool through the gateway; before, it would have skipped there.

## Results per H id

| Check | Criterion | Test | Result | The control refuses | The paired allowed control |
| ----- | --------- | ---- | ------ | ------------------- | -------------------------- |
| Lane parity | 2 | `test_poc05_kind_remote_lane.py::test_remote_gives_the_sidecar_events_for_the_same_request[hello, glossary, simplify, fail]` | pass | | the same four requests (`stream: true`) to `agent-echo` and `chassis-echo-remote` give the same event list and envelope (`chassis_contracts.lane.assert_same`; request id, call id, trace id, idempotency key, agent name, config hash masked); each stream also has the script's event types |
| Lane precondition | 2 | `test_the_two_deployments_are_the_two_lanes` | pass | | `agent-echo` runs `sidecar`, `chassis-echo-remote` runs `remote` to `remote-echo`, whose pod is on `gvisor` with one container |
| H17 | 6 | `test_h17_listener_answers_only_the_remote_token_inside_a_run` | pass | a model call to 8091 from the remote pod with no token (401 `remote_unauthenticated`), a token it did not issue (401), and its own token outside a run (403 `run_required`) | a run with the same traceparent ends `ok` with the fake model's reply, and the listener's 200 count for the remote's IP grows on `/v1/chat/completions` and `/mcp`; after the run the same call is 403 again |
| H18 (listener) | 6 | `test_h18_listener_refuses_every_caller_but_the_remote_pod` | pass | TCP to 8091 (ClusterIP and pod IP) from a pod in `default` labeled `app.kubernetes.io/name: remote-echo` (no egress policy), and from `agent-echo`'s workload | the same caller reaches the chassis's 8080 on the same pod IP; the remote pod reaches 8091 by both addresses |
| H18 (remote port) | 6 | `test_h18_remote_port_refuses_every_caller_but_its_chassis` | pass | TCP to the remote's 9000 (pod IP and Service) from the labeled caller | its own chassis connects to 9000 by both addresses; the caller reaches 8080 elsewhere |
| H30 | 6 | `test_h30_remote_reaches_no_other_pod` | pass | the remote's TCP to `agent-echo`:8080, its own chassis's 8080, LiteLLM 4000, Valkey 6379, code-runner 8000 | 8091 connects in the same call |
| H19 | 8 | `test_h19_remote_reaches_no_outside_address` | pass | the remote's TCP to 1.1.1.1:443 and :80 (literal, no DNS) | 8091 connects in the same call; the caller pod reaches 1.1.1.1:443, so the address answers from this cluster |
| H20 | 8 | `test_h20_remote_has_no_dns` | pass | the remote's A query to kube-dns (ClusterIP and a CoreDNS pod IP) and to 1.1.1.1 (timeout); its resolver finds no name; its policy's only egress port is 8091 | the same A query from its chassis is answered (rcode 0, one answer) |
| H21 | 8 | `test_h21_root_is_read_only_and_tmp_is_writable` | pass | a write into `/var/tmp` (mode 1777 on the root) with EROFS; `/proc/mounts` has `/` `ro` | the same write to `/tmp` works; `/tmp` is `rw` |
| H23 | 8 | `test_h23_pids_cap_is_the_configured_limit` | pass | | the pod's pids cgroup on the node has `pids.max` 256, the kubelet's `podPidsLimit` and `cluster.yaml`'s value; `pids.current` is above 0 and under it. Never exhausted |
| H28 | 8 | `test_h28_remote_runs_on_gvisor_next_to_a_runc_pod` | pass | | `runtimeClassName: gvisor`, handler `runsc`, containerd's sandbox handler `runsc`, `/proc/version` gVisor's; control: `agent-echo` has no RuntimeClass, a non-`runsc` handler, and `/proc/version` "Linux version" without gvisor |
| One secret (H25 remote) | 4 | `test_remote_holds_one_secret_its_own_token` | pass | any other Secret: the spec's only Secret source is `CHASSIS_API_TOKEN` from `remote-echo-token` in `workload`; no `envFrom` Secret, no Secret or projected volume, no service account token (automount off, `/var/run/secrets/kubernetes.io` absent); no chassis credential name in the live env | the live env has `CHASSIS_API_TOKEN` (name only); the path check sees `/etc/hosts` |
| Code runner, runsc and egress (H28, H19 tool lane) | 8 | `test_poc05_kind_code_runner.py::test_code_runs_on_gvisor_with_no_egress` | pass | the code's TCP to 1.1.1.1:443 and to LiteLLM 4000 | the code connects to its own server on 127.0.0.1:8000; `runtimeClassName: gvisor`; the code sees gVisor's kernel |
| Code runner, NPROC (H23 tool lane) | 8 | `test_process_limit_refuses_forks_past_the_allowance` | pass | a new process past `RLIMIT_NPROC` with EAGAIN: 31 started, the 32nd refused | a call that starts 5 children starts all 5 |

## Limits and observations

- H17's in-run 200 is shown through the run the chassis opens, not by a probe call of ours inside it. A run on kind lasts well under a second and the fake model has no delay, so a second caller cannot be timed into it without a race. The evidence: the run with that traceparent ends `ok` with the model's reply, the remote has no other way to a model (H30, H19), and the listener's access log (method, path, status; no header is logged) counts new 200s from the remote's IP. Only the counts leave the log.
- H18: the caller in `default` carries the remote's pod label and is still refused: the policy also pins the namespace (`poc05-remote`). Per-remote separation inside `poc05-remote` stays a recorded exception (review F3).
- H20: the remote lane has no DNS at all, so DNS cannot tunnel out of it. The sidecar lane keeps kube-dns; the threat model's tunneling caveat holds there.
- H21: under gVisor a write to `/` or `/app` gives EACCES (root-owned, mode 755), not EROFS, as the bring-up note says. `/var/tmp` (mode 1777) is the path where only the read-only mount can refuse, and it gives EROFS. `/dev/shm` is writable inside the sandbox (gVisor's own tmpfs, counted against the pod's memory); not a finding for H21, recorded for the gVisor note.
- H23: inside the sandbox `/sys/fs/cgroup/pids/pids.max` reads `max`. gVisor's own cgroup view does not show the cap; the cap is the host pod cgroup, read on the node.
- The H19 control needs internet on the host: the caller pod reaches 1.1.1.1:443. A cluster with no outside route would fail that half, not pass it.
- No missing control was found. No manifest fix was needed. One script fix: `run.sh test-remote` through `with_gateway`.

## Mutation check

Each test was broken once in a temporary copy (`test_poc05_kind_zzmutant.py`, deleted after) and failed every time. The mutations: the remote stream compared from another text; the remote lane's connector expected `sidecar`; the bare H17 call given the remote's token; the run's text set to `fail`; the caller's 8091 probe pointed at 8080; the caller's 9000 probe pointed at the chassis's 8080; H30's first target set to 8091; H19's outside address set to 8091; H20's queries run in the chassis; H21's write moved to `/tmp`; H23 reading `pids.current` as the cap; H28's `/proc/version` read in the runc pod; the one-secret check run on the chassis's spec; the code runner's outside address set to its own server; 10 forks instead of 64.

```text
lane | 1 failed, 4 deselected in 11.35s
lane-pre | 1 failed, 4 deselected in 0.32s
h17-bare | 1 failed, 9 deselected in 0.86s
h17-run | 1 failed, 9 deselected in 5.69s
h18-listener | 1 failed, 9 deselected in 3.84s
h18-port | 1 failed, 9 deselected in 1.30s
h30 | 1 failed, 9 deselected in 13.08s
h19 | 1 failed, 9 deselected in 4.05s
h20 | 1 failed, 9 deselected in 3.85s
h21 | 1 failed, 9 deselected in 0.76s
h23 | 1 failed, 9 deselected in 0.35s
h28 | 1 failed, 9 deselected in 1.21s
one-secret | 1 failed, 9 deselected in 0.24s
cr-egress | 1 failed, 1 deselected in 4.64s
cr-nproc | 1 failed, 1 deselected in 1.99s
```

## Runs

`grep -c 'sk-'` on every log below and the mutation log: 0.

```text
$ PATH=/opt/homebrew/bin:$PATH deploy/kind/poc05/run.sh test-remote
.................                                                        [100%]
17 passed in 95.91s (0:01:35)
```

The kind tier (73 before T22, 17 new):

```text
$ POC05_KIND=1 deploy/kind/poc05/run.sh with-gateway uv run pytest -m network pocs/poc-05-sandboxed/tests -q -rs
SKIPPED [1] packages/contract-suites/src/chassis_contracts/tool.py:157: provide other_write_arguments: valid write_call arguments of another value
SKIPPED [1] packages/contract-suites/src/chassis_contracts/tool.py:204: provide a make_unavailable fixture: the next call fails as unavailable
90 passed, 2 skipped, 353 deselected in 127.18s (0:02:07)
```

The offline gate. The 17 new kind tests skip there, and `test_poc05_run_sh_test_remote_fails_naming_missing_files` now skips too (every remote file exists), so 2424 passed and 218 skipped became 2423 and 236:

```text
$ PATH=/opt/homebrew/bin:$PATH make check
==== 2423 passed, 236 skipped, 6 xfailed, 15 warnings in 219.77s (0:03:39) =====
cd docs/planning && python3 tools/check.py
127 issues, 113 epic stories
OK
uv run python scripts/harness_lint.py
harness-lint: ok
```

## 2026-10-09: code runner and remote on the new layout

Task 6, part B of the [per-call sandbox plan](../../../docs/plans/2026-10-09-poc-05-per-call-sandbox.md): the code-runner and remote kind tests on the per-call layout (warm pool 2, the dispatcher in `poc05-platform`), plus the MEDIUM and LOW fixes from the [code review](2026-10-09-review-cluster.md) and the [security review](2026-10-09-review-security-cluster.md). Branch `poc-05/cluster` at `7acf846` plus the test changes. Every `kubectl` call pinned `--context kind-poc05`. `grep -c sk-` on every saved log: 0. Nothing under `deploy/` changed.

### Fixes

- `test_code_runs_on_gvisor_with_no_egress`: reads the `SandboxTemplate` and the pool's unclaimed pods (`warm_sandboxes()` in `poc05_kind.py`: Running, Ready, not deleting, no `claim-uid` label), and the code's hostname must be one of them. The code's connects to 1.1.1.1:443, LiteLLM, the dispatcher pod, the node IP:6443, and the API VIP:443 must time out; 127.0.0.1:8000 connects. The connects run in one thread with `select`: threads hit the runner's limits (`RuntimeError: can't start new thread`), and five 3 s connects in a row hit the 10 s call ceiling.
- Ingress: new `test_only_the_dispatcher_reaches_a_sandbox`. The live policy's one ingress peer is the dispatcher on 8000, with no egress. LiteLLM and the unpoliced caller time out to a warm sandbox's pod IP:8000; the dispatcher connects.
- `test_h30_remote_reaches_no_other_pod`: targets the dispatcher Service and a warm sandbox pod IP instead of the old `code-runner` Service. Each target is shown up from an allowed peer in the same test: the unpoliced caller (both 8080 ports), `agent-echo`'s workload (LiteLLM), its chassis (Valkey), LiteLLM (the dispatcher), and the dispatcher (the sandbox).
- `REFUSED_TCP` is gone from all four files. A policy refusal expects `POLICY_DROPPED` (`TimeoutError`) only. H13's loopback keeps `ConnectionRefusedError`, which is its claim. Up-controls added where a target had none: H03/H04 (the dispatcher reaches node:6443 and the VIP), MinIO (inside its pod, an unsigned call to its own pod IP gets 403, and the Service's EndpointSlice holds that IP; its one allowed peer, the `minio-init` Job, has finished).
- The code runner's 1.1.1.1 refusal has the unpoliced-caller control. The caller pod moved to `poc05_kind.unpoliced_caller()`; it deletes a leftover pod (`--ignore-not-found --wait=true`) before apply.
- Reply bytes: the probe's `http` check redacts the reply when the check sends a real credential (`auth_env`): the value, its last four characters, `sk-` runs, and hex runs of 32 or more. `test_poc05_kind_hardreq1.py` prints only the status (or the error name) for calls that carry a key, including those through the chassis's proxy.
- H01 is live: a fixture adds 169.254.169.254/32 to the kind node's loopback and starts a one-line perl HTTP listener on port 80 (`docker exec` into the node), then removes both after the test. The workload's connect times out; an unpoliced pod in `default` gets the listener's 200 from the same address. Nothing was added to `run.sh`. After the runs: `ip -4 addr show dev lo | grep -c 169.254` gave 0, and no listener was running.

### New tests (`test_poc05_kind_code_runner.py`)

| Test | Criterion, H id | Result | Refusal | Allowed control |
| ---- | --------------- | ------ | ------- | --------------- |
| `test_python_child_burst_leaves_the_dispatcher_up` | 8, the HIGH (054 H-16), H23 tool lane | pass | the burst cannot reach another caller | a concurrent call from a second gateway session works; the next call (5 children) works; same dispatcher pod uid, restart count unchanged |
| `test_files_do_not_cross_calls` | 8, B12 tool lane | pass | call B sees neither `/tmp/<name>` nor `/dev/shm/<name>`, on another host | call A reads both files back in its own call |
| `test_dev_shm_is_capped` | 8, the `/dev/shm` LOW | **xfail(strict=True)** | expected ENOSPC after about 8 MiB | a 1 MiB write works |
| `test_claim_is_gone_after_the_call` | 8, per-call plan | pass | the claim and its pod are gone within 10 s of the call's end | a claims watch saw the claim ADDED and bound to the pod the code ran in |
| `test_only_the_dispatcher_reaches_a_sandbox` | 8, H30 tool lane | pass | LiteLLM and the unpoliced caller time out to a sandbox's 8000 | the dispatcher connects |

The burst, observed once with a print in a throwaway copy (load 1.74): `{"started": 31, "refused": null}`, exit 0, not timed out; the concurrent call ran on `code-runner-qkftd`; dispatcher `restartCount` 0.

**The `/dev/shm` cap does not hold under gVisor.** The emptyDir (`medium: Memory`, `sizeLimit: 8Mi`) is in the pod spec, but runsc mounts its own tmpfs there:

```
$ kubectl --context kind-poc05 -n poc05-tools exec code-runner-<warm> -- sh -c 'grep -E " /dev/shm | /tmp " /proc/mounts; df -k /dev/shm /tmp'
none /tmp 9p rw,trans=fd,...,directfs 0 0
none /dev/shm tmpfs rw 0 0
none             4062256     0   4062256   0% /dev/shm
none               65536     0     65536   0% /tmp
```

A call wrote 17 x 1 MiB files to `/dev/shm` with no error (`{'small': None, 'written_mib': 17, 'error': None}`). The pod's 256Mi memory limit still bounds it, and only for that one call. The assertion is kept and marked `xfail(strict=True)` with this evidence; it fails once the template is fixed. The fix is in `deploy/kind/poc05/tools/code-runner.yaml` (platform-security). Not checked: `remote-echo`'s `/dev/shm`.

### Mutation checks

Each in a temporary copy under `tests/`, deleted after (load 1.56). `7 failed in 69.10s`, each at its intended assertion:

| Copy | Mutation | Failure |
| ---- | -------- | ------- |
| m1 | the "outside" connect points at 127.0.0.1:8000 | `('outside', {'outside': 'connected', ...})`, `'connected' == 'TimeoutError'` |
| m2 | the "refused" ingress source is the dispatcher, not LiteLLM | `{'connected': True}`, `None == 'TimeoutError'` |
| m3 | the restart count compared one off | `(uid, 0) == (uid, 1)` |
| m4 | call B's check runs inside call A | `[True, True] == [False, False]` |
| m5 | the gone-check waits on a warm pod that stays | `left after 10 s: ['pod/warm']` |
| m6 | H01's workload probe run from the unpoliced caller | `{'connected': True}`, `None == 'TimeoutError'` |
| m7 | H30's Valkey target on 6380 (nothing listens) | the up-control: `{'host': '10.96.149.160', 'port': 6380}, {'error': 'TimeoutError'}`, `None is True` |

### Runs

```
$ POC05_KIND=1 deploy/kind/poc05/run.sh with-gateway uv run pytest -m network pocs/poc-05-sandboxed/tests -q -rs   # load 2.87 2.66 3.41
SKIPPED [1] packages/contract-suites/src/chassis_contracts/tool.py:157: provide other_write_arguments: ...
SKIPPED [1] packages/contract-suites/src/chassis_contracts/tool.py:204: provide a make_unavailable fixture: ...
146 passed, 2 skipped, 483 deselected, 1 xfailed in 167.33s (0:02:47)
$ deploy/kind/poc05/run.sh test-remote                                                                          # load 2.31 2.74 3.32
21 passed, 1 xfailed in 129.00s (0:02:09)
$ make test-poc POC=05
================= 481 passed, 150 skipped, 1 xfailed in 22.78s =================
```

The 1 xfailed in both kind runs is `test_dev_shm_is_capped`. The 1 xfailed in `make test-poc` is the earlier `test_poc05_serve_refuses_a_wildcard_public_host` (026 CH-4); the kind tests skip offline. After the runs: no `SandboxClaim` in `poc05-tools`, pool 2/2 Ready, no caller pod in `default`, dispatcher `restartCount` 0. The cluster is left running.

### The `/dev/shm` bound that holds (platform-security verdict, 2026-10-09)

`platform-security` accepted the finding and recorded it (owner 055 CH-6), with no fix in the template.

- `test_dev_shm_is_capped` stays `xfail(strict=True)`. Its reason now ends: "Accepted 2026-10-09, owner 055 CH-6; the per-call pod's memory limit bounds it."
- New, passing: `test_dev_shm_is_bounded_by_the_per_call_pod`. A call writes a file to `/dev/shm` and reads it back (the control). The pod the call ran in is one from a pool snapshot taken before the call, and that pod's spec sets memory limit and request to `256Mi`. Its docstring points to `test_files_do_not_cross_calls` for the second half: call B does not see call A's file.
- `deploy/kind/poc05/tools/code-runner.yaml` got one comment line on the `shm` volume: it is not a control, because gVisor ignores it and runsc mounts its own tmpfs. The hardening static test passes: `89 passed in 3.30s`.
- Mutation, in a temporary copy that was deleted after: with the expected value set to `128Mi`, the test fails with `assert '256Mi' == '128Mi'` (`1 failed in 2.14s`).
- `warm_pool_ready()` now waits until the pool is settled: no claims, and every Running pod unclaimed and Ready. `test_only_the_dispatcher_reaches_a_sandbox` runs its allowed control first and checks that the target pod is unchanged at the end. Why: twice (the first targeted run and the run below), the dispatcher's connect to the picked pod timed out. Picked by hand, settled pods connected 6 of 6 times, and a freshly replaced pod connected as soon as it was Ready. So the likely cause is a pod still in flux from the previous test's claim. That cause is inferred, not shown.

Runs, the code-runner kind file (`grep -c sk-` 0 on each log):

```
before the settle fix                            # load 2.66 2.88 3.11
1 failed, 6 passed, 1 xfailed in 55.09s          (test_only_the_dispatcher_reaches_a_sandbox: {'error': 'TimeoutError'} from the dispatcher)
after the settle fix, run 1                      # load 4.72 3.52 3.31
1 failed, 6 passed, 1 xfailed in 41.83s
  test_python_child_burst_leaves_the_dispatcher_up, line `after = await run_python(other_caller, PY_FORKS.format(want=UNDER_LIMIT))`:
  ToolResult(content='sandbox_lost: the sandbox ended before it answered', is_error=True)
run 2                                            # load 3.03 3.26 3.23
7 passed, 1 xfailed in 39.84s
run 3                                            # load 3.14 3.23 3.22
7 passed, 1 xfailed in 39.76s
$ make test-poc POC=05
================= 481 passed, 151 skipped, 1 xfailed in 22.32s =================
```

**Open: the call after the burst was lost once.** In 1 run of 3, at load 4.7, the 5-child call made after the burst got `sandbox_lost`. That call has its own claim and pod. The dispatcher did not restart (the restart assertion comes after this line and was not reached; it passed in the other runs). The cause is not found. Events in `poc05-tools` show only startup-probe refusals and `Killing` on pods being torn down. No OOM line was found for a code-runner pod (the node's `kubepods` `oom_kill` is 3 in total, and the one in `dmesg` is MinIO's `mc`). The test is not loosened. A `sandbox_lost` call is retryable by design, but the HIGH's claim is "the next call works". It needs a rerun under load with a watch on claims and pods.
