# PoC-5 close runs on kind (2026-10-09)

The wave 2 cluster pass on `kind-poc05`, branch `poc-05/cluster` at `1c9685a` plus this wave's uncommitted files:
- the Kafka pass and the H11 case (T25, exit criterion 3);
- the dispatcher with the ConnectTimeout retry (`0399a04`);
- the code-runner flake check;
- both kind suites;
- the gVisor overhead run (criterion 9, [its note](2026-10-09-gvisor-overhead.md)).

Each run is listed with its command, the tail of its output, and the load. "Node load" is `/proc/loadavg` in `poc05-control-plane` (1, 5, and 15 min). "Mac" is `uptime` on the host. `grep -c sk-` is 0 on every saved log.

The project's `make`/`uv` commands ran with `UV_NO_SYNC=1` and `PATH=/opt/homebrew/bin:$PATH`, and every kubectl call ran with `--context kind-poc05`.

## 0. Recovery after the host overload

From about 14:00Z the Mac ran a game at about 340 % CPU. The node's load reached 45 / 68 / 43. kube-apiserver restarted 2 times, kube-controller-manager 6, and kube-scheduler 5. agent-sandbox-controller exited with code 1, 4 times. No cluster step ran until the game was closed.

When work resumed, agent-echo was in `Init:CrashLoopBackOff` after 21 restarts:
- The chassis had exited with code 3: `startup failed after 88 attempt(s): the sidecar at http://127.0.0.1:9000 is not reachable`.
- The workload had been restarted by its own startup probe in the same window.
- After the overload, the workload answered its agent card (200), and the chassis sat in back-off.

So this was stale back-off from the overload, not a code fault. It was fixed by a rollout restart:

```
$ kubectl --context kind-poc05 -n poc05-agents rollout restart deploy/agent-echo
deployment "agent-echo" successfully rolled out
```

Health check, 14:30:50Z, node load 0.97 6.44 18.23:

```
$ deploy/kind/poc05/run.sh pods request
agent-echo           "hello"    HTTP 200  {"status":"ok","output":{"text":"ok"},"error":null}
agent-echo           "glossary" HTTP 200  {"status":"ok","output":{"text":"From the glossary: SLM means small language model.", ...
chassis-echo-remote  "hello"    HTTP 200  {"status":"ok","output":{"text":"ok"},"error":null}
chassis-echo-remote  "glossary" HTTP 200  {"status":"ok","output":{"text":"From the glossary: SLM means small language model.", ...
```

Every pod was Ready, the warm pool was at 2/2, and `default` held no pods.

## 1. Memory before Kafka

At 14:31:01Z, node load 0.82 6.23 18.03:

```
poc05-control-plane 3.628GiB / 7.748GiB
               total        used        free      shared  buff/cache   available
Mem:            7934        4014         282         240        4075        3919
pods_current_MiB=3185 pods_ws_MiB=2665
```

That left 3.9 GiB available, enough for Kafka (768Mi limit) and agent-echo-events. Nothing was scaled down.

## 2. The Kafka pass

At 14:31:11Z, node load 2.25 6.35 17.94, 31 s:

```
$ make kind-poc05 ARGS="kafka"
[seed] made kafka-sasl in poc05-platform (SCRAM users) and poc05-agents (the chassis password)
deployment "kafka" successfully rolled out
[seed] topic agents.task.completed.v1 is there
[seed] topic agents.task.failed.v1 is there
configmap/chassis-echo-events-config created
deployment "agent-echo-events" successfully rolled out
rc=0 in 31 s
```

## 3. H11 on kind

The first two runs failed at the control, not at a refusal:
- The first run hit a `TypeError` in the test's own consumer setup. aiokafka's `partitions_for_topic` returned `None`, so the test now subscribes and waits for the assignment.
- The second run got `{'run_status': 200, 'found': False}`. The topic's end offsets were 0 on all 3 partitions, and the broker logged no login from the chassis.
- The cause was a stale image. The chassis image on the node was built at 10:45Z, before the SASL commit `c06cf31` (13:56Z), and `chassis.adapters.kafka.events` in it had no `sasl_from_env`. So the control failed exactly when nothing was published.

After the rebuild (section 4), at 14:35:13Z, node load 1.49 3.54 14.12:

```
$ POC05_KIND=1 POC05_KAFKA=1 uv run pytest -m network pocs/poc-05-sandboxed/tests/test_poc05_kind_hardreq1.py -k kafka -q -rs
1 passed, 4 deselected in 2.90s
```

The workload container's raw results, and the broker's log for the same calls:

```
"api_versions": {"error_code": 0}
"no_sasl": {"closed": true}
"empty":   {"error_code": 58, "step": "first", "message": "Authentication failed during authentication due to invalid credentials with SASL mechanism SCRAM-SHA-512"}
"unknown": {"error_code": 58, "step": "first", "message": "...invalid credentials with SASL mechanism SCRAM-SHA-512"}
"guessed": {"error_code": 58, "step": "final", "message": "...invalid credentials with SASL mechanism SCRAM-SHA-512"}

Caused by: org.apache.kafka.common.errors.InvalidRequestException: Unexpected Kafka request of type METADATA during SASL handshake.
INFO [SocketServer listenerType=BROKER, nodeId=1] Failed authentication with /10.244.0.81 (... invalid credentials ...)   (x3)
```

Each refusal is the broker's own answer, not a client-side failure. The "no credential" logins (an empty user, and an unknown user with an empty password) reach the broker and get error 58. The control works: a run through `agent-echo-events` published `agents.task.completed.v1` with status `ok`, and the chassis's own SCRAM user read it back.

Side finding: a diagnostic `kubectl exec` that imported the whole `chassis` package in the chassis container got that container OOMKilled. The container has a 256Mi limit, and an extra Python process counts against it. The test's own script imports only aiokafka and stayed under the limit.

## 4. Rebuild and dispatcher rollout

At 14:33:46Z, node load 2.30 4.04 14.86, 26 s:

```
$ make kind-poc05 ARGS="build load"
Image: "kind.local/agent-platform/chassis:poc05" with ID "sha256:bdbb1008..." not yet present on node "poc05-control-plane", loading...
Image: "kind.local/agent-platform/code-runner:poc05" with ID "sha256:d8a3077b..." not yet present on node "poc05-control-plane", loading...
(fake-model-server, fake-mcp-server, echo-python, echo-typescript: already present)
rc=0 in 26 s
```

The pods were then moved to the new images:
- `rollout restart` of `code-runner-dispatch`, `agent-echo`, `chassis-echo-remote`, and `agent-echo-events`.
- The two warm pool pods deleted as the platform deployer. The pool refilled to 2/2 on the new code-runner image.

```
dispatcher before: code-runner-dispatch-c8f68fd69-6m7gx  uid=008a2804-9669-464a-9ad5-0a5b0e4dde61 image sha256:da8fbef0...
dispatcher after:  code-runner-dispatch-688f5c9f9c-dvvf4 uid=90ad4d96-4c58-45af-8518-04e352e02812 image sha256:01a16fdb...
$ kubectl exec deploy/code-runner-dispatch -- grep -c ConnectTimeout .../code_runner/dispatch.py
1
```

## 5. The code-runner flake

The command is `POC05_KIND=1 deploy/kind/poc05/run.sh with-gateway uv run pytest -m network pocs/poc-05-sandboxed/tests/test_poc05_kind_code_runner.py -q -rs`. It ran 10 times, 10 s apart. The pass mark was 5 of 5.

| Run | Start (Z) | Node load at start | Mac 15 min | Result |
| --- | --- | --- | --- | --- |
| 1 | 14:35:45 | 1.49 3.31 13.65 | 11.15 | 7 passed, 1 xfailed |
| 2 | 14:36:43 | 1.44 3.02 12.96 | 10.58 | 7 passed, 1 xfailed |
| 3 | 14:37:44 | 1.62 2.82 12.27 | 10.03 | 7 passed, 1 xfailed |
| 4 | 14:38:45 | 1.12 2.43 11.49 | 9.66 | 7 passed, 1 xfailed |
| 5 | 14:39:42 | 0.74 2.11 10.86 | 9.35 | 7 passed, 1 xfailed |
| 6 | 14:41:21 | 0.82 1.85 9.88 | 8.74 | 7 passed, 1 xfailed |
| 7 | 14:42:22 | 1.30 1.81 9.36 | 8.37 | 7 passed, 1 xfailed (76 s) |
| 8 | 14:43:50 | 1.27 1.71 8.62 | 7.87 | 7 passed, 1 xfailed |
| 9 | 14:44:56 | 1.72 1.75 8.17 | 7.94 | 7 passed, 1 xfailed |
| 10 | 14:46:01 | 1.31 1.64 7.69 | 7.92 | 7 passed, 1 xfailed |

The xfail is `test_dev_shm_is_capped` (strict, accepted, 055 CH-6). In every run, the 1-minute load was under 2.

Watches:
- Runs 1 to 5: the dispatcher log and the events watches ran. The claims, sandboxes, and pods watch died at start, because kubectl allows only one resource type with `-w`.
- Runs 6 to 10: every watch ran, one per resource type.

Over runs 6 to 10:

```
sandboxclaims ADDED=55 DELETED=55; pods ADDED=57 DELETED=55   (57 = 55 per-call pods + the 2 warm pods present at start)
dispatcher: 55 x "dispatch outcome=ok"; "sandbox_lost error=": 0
```

Runs 1 to 5 also had 55 dispatches with `outcome=ok` and no `sandbox_lost`.

The poc05-tools events show `Readiness probe failed` warnings on per-call pods: 68 in runs 1 to 5. Each is a fresh pod probed while it starts (`connection refused`, or the 1 s probe timeout). None ended a call.

## 6. The full kind tier and the remote lane

At 14:47:11Z, node load 1.14 1.52 7.21, Mac 5.20, 3 min 7 s:

```
$ POC05_KIND=1 POC05_KAFKA=1 deploy/kind/poc05/run.sh with-gateway uv run pytest -m network pocs/poc-05-sandboxed/tests -q -rs -rx
XFAIL test_poc05_kind_code_runner.py::test_dev_shm_is_capped - 2026-10-09 on kind: runsc mounts its own sentry tmpfs at /dev/shm ...
XFAIL test_poc05_kind_remote_shm.py::test_dev_shm_is_capped_at_32mi - runsc mounts its own sentry tmpfs at /dev/shm, and remote-echo.yaml sets no /dev/shm volume ...
150 passed, 2 skipped, 529 deselected, 2 xfailed in 187.83s (0:03:07)
```

That run's `-rx` hid the skip reasons. The same two skips, from the gateway file alone:

```
SKIPPED [1] packages/contract-suites/src/chassis_contracts/tool.py:157: provide other_write_arguments: valid write_call arguments of another value
SKIPPED [1] packages/contract-suites/src/chassis_contracts/tool.py:204: provide a make_unavailable fixture: the next call fails as unavailable
12 passed, 2 skipped in 3.35s
```

The remote `/dev/shm` xfail fails for the recorded reason. The same fill, run by hand, wrote 40 MiB with no error and left nothing behind:

```
{"small": null, "read_back": 1048576, "written_mib": 40, "error": null, "left": []}
```

At 14:50:47Z, node load 0.80 1.31 5.96, Mac 4.90:

```
$ deploy/kind/poc05/run.sh test-remote
24 passed, 2 xfailed in 138.43s (0:02:18)
```

## 7. gVisor overhead

At 14:53:15Z, node load 2.37 1.59 5.35, Mac 5.94, 117 s, exit 0. The table and the readings are in [the overhead note](2026-10-09-gvisor-overhead.md). Afterwards the scratch namespace `poc05-q9-overhead` was gone (NotFound).

## State left behind

- The cluster is running: every PoC-5 pod is Ready, Kafka and agent-echo-events are up, and the warm pool is at 2/2 with no claims.
- `default` holds no pods.
- The images on the node are the 14:33Z build.
