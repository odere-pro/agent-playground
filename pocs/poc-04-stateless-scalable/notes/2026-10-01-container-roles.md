# Container roles on kind: native sidecar or preStop (2026-10-01)

Package P17 (plan `docs/plans/2026-10-01-poc-04-stateless-scalable.md`, section 8b). Exit criteria 9
(rolling restart fails no request; a hung workload restarts the pod) and 5 (read-only root).

Setup: kind v0.33.0, cluster `poc04` (one node, `kindest/node:v1.37.0`), every command with
`--context kind-poc04`. Images rebuilt from the working tree on 2026-10-01 (`run.sh up`, which runs
`create build load apply`). Manifests applied unfiltered (the chassis has `--drain-delay-s` and
`--drain-timeout-s`). 3 agent replicas, `maxUnavailable: 0`, `maxSurge: 1`.

Client path: the host reaches the NodePort through `127.0.0.1:18081` (checked: a 3 s, 2-worker
smoke run sent 138, ok 138). All drills ran the client from the host, no port-forward. The client
is `pocs/poc-04-stateless-scalable/load/steady_client.py --concurrency 20 --no-retry`, one
`httpx.AsyncClient` with keep-alive connections, each pinned by conntrack to one pod.

## Drill log

### native-sidecar, echo-python

Rolling, run 1: `deploy/kind/run.sh drill-rolling 90`, exit 1.

```
deployment "agent" successfully rolled out
steady_client: no-retry, 20 workers, 90 s -> sent 7071, ok 7070, failed 1, attempts 7071, retried 0, p50 232.68 ms, p95 525.42 ms
  failed steady-cab1192f4b46491aac3e7747848153e7: RemoteProtocolError: Server disconnected without sending a response.
```

Rolling, run 2: same command (wrapped to follow the old pods' logs with `kubectl logs -f
--timestamps` and save the events), exit 1.

```
steady_client: no-retry, 20 workers, 90 s -> sent 6726, ok 6716, failed 10, attempts 6726, retried 0, p50 240.68 ms, p95 527.57 ms
  failed steady-57edcf2238c3484988cb3a3296f67221: RemoteProtocolError: Server disconnected without sending a response.
  (all 10 failures are this kind)
```

Stop order on pod `agent-59b698fb45-hwnhn` (events and container logs, UTC):

```
19:23:13Z  Killing  spec.containers{chassis}        Stopping container chassis
19:23:13Z  Killing  spec.initContainers{workload}   Stopping container workload   (event only; no signal yet)
19:23:13.45 chassis  GET /ready 503                   (draining at once)
19:23:18.19 chassis  last POST /v1/run 200 OK         (still serving through --drain-delay-s 5)
19:23:18.195 chassis "Shutting down" (public server) ... 18.516 "Finished server process"
19:23:18.227 workload last request (agent card)
19:23:19.555 workload "Shutting down" ... 19.647 "Finished server process"
```

The chassis gets SIGTERM first; the workload only after the chassis exits (1.0 s later). No
workload request was cut: its last calls returned 200 before the chassis exited.

Why requests failed: each failure is `Server disconnected without sending a response` on a reused
keep-alive connection. During `--drain-delay-s` the chassis answers on existing connections
without `Connection: close`, so a keep-alive client never leaves the pod (kube-proxy pins an open
TCP connection to its pod; removing the endpoint only affects new connections). At step 4 of
`chassis.server.lifecycle.Drain.run` (`public.should_exit = True`) uvicorn closes the idle
keep-alive connections; a request the client writes on one of them at that moment is lost. This is
chassis source (`packages/chassis/src/chassis/server/lifecycle.py` and `app.py`), not the container
roles; not patched here (package rule). The container ordering itself worked.

Hung workload: `deploy/kind/run.sh drill-hung`, exit 0.

```
[kind-poc04] SIGSTOP workload 921285727bf7... in agent-bccbdb477-dvbdq (restarts so far: 0)
[kind-poc04] not Ready after 11 s
[kind-poc04] workload restarted after 37 s
[kind-poc04] PASS: Ready again after 42 s
```

Note: "not Ready after 11 s" is over the plan's "within 10 s". run.sh counts in whole seconds
(`$SECONDS`) and checks the bound before its 0.5 s sleep, so it let 11 through. Treat this as at
the bound, not a clean pass of the 10 s target.

Read-only root (exit criterion 5), `kubectl exec` into a live pod, open a file for writing:

```
chassis /x OSError Read-only file system
chassis /tmp/x WRITABLE
workload /x OSError Read-only file system
workload /tmp/x WRITABLE
```

### prestop, echo-python

`deploy/kind/run.sh apply prestop python`, then the same wrapped `drill-rolling 90` twice,
`drill-hung`, and the read-only check.

Rolling, run 1: exit 0.

```
steady_client: no-retry, 20 workers, 90 s -> sent 5262, ok 5262, failed 0, attempts 5262, retried 0, p50 317.03 ms, p95 640.03 ms
```

Rolling, run 2: exit 1.

```
steady_client: no-retry, 20 workers, 90 s -> sent 6120, ok 6113, failed 7, attempts 6120, retried 0, p50 281.75 ms, p95 529.83 ms
  failed steady-59588c3ff68548cb82dfce87b1898233: RemoteProtocolError: Server disconnected without sending a response.
   7 RemoteProtocolError   (all failures)
```

Same failure kind as the native sidecar: the keep-alive close at the end of the chassis drain.

Stop order on pod `agent-5d7bd5458b-2z67p` (UTC):

```
19:28:47Z   Killing  spec.containers{workload}  /  Killing  spec.containers{chassis}  (both preStops start)
19:28:52.75 chassis  "Waiting for connections to close"   (SIGTERM after the 5 s preStop; drain delay 0)
19:28:52.99 chassis  last POST /v1/run 200 OK
19:28:53.27 chassis  "Finished server process"           (chassis done 6.3 s after delete)
19:28:52.68 workload last POST / 200 OK                  (before the chassis exited)
19:29:22.69 workload "Shutting down"                      (SIGTERM after its 35 s preStop)
19:29:22.77 workload "Finished server process"           (pod done ~36 s after delete)
```

The chassis gets SIGTERM first, by timing only. In-flight calls finished. The workload then sits
idle for 29 s: the 35 s sleep is a guess that must cover the chassis's worst-case drain.

Hung workload: exit 0.

```
[kind-poc04] SIGSTOP workload 2550eb02bbb5... in agent-985fb9978-8hhm2 (restarts so far: 0)
[kind-poc04] not Ready after 9 s
[kind-poc04] workload restarted after 38 s
[kind-poc04] PASS: Ready again after 42 s
```

Read-only root:

```
chassis /x OSError Read-only file system
chassis /tmp/x WRITABLE
workload /x OSError Read-only file system
workload /tmp/x WRITABLE
```

### native-sidecar, echo-typescript

`deploy/kind/run.sh apply native-sidecar typescript` (the switch deletes and recreates
`deployment/agent`), then the same drills.

Rolling, run 1: exit 1.

```
steady_client: no-retry, 20 workers, 90 s -> sent 19417, ok 19403, failed 14, attempts 19417, retried 0, p50 76.82 ms, p95 182.3 ms
  failed steady-91c81256734f4fbeb4725b5ebf3db162: RemoteProtocolError: Server disconnected without sending a response.
  13 RemoteProtocolError
   1 ReadError   (httpx gives no message: the connection was reset while the response was read)
```

Rolling, run 2: exit 1.

```
steady_client: no-retry, 20 workers, 90 s -> sent 18634, ok 18631, failed 3, attempts 18634, retried 0, p50 86.65 ms, p95 183.41 ms
   3 RemoteProtocolError
```

Stop order on pod `agent-5c68785746-d7lpr` (UTC): `Killing` for both containers at 19:32:40Z;
chassis `/ready` 503 at 41.73; last `POST /v1/run` 200 at 45.21; chassis "Shutting down" at 45.10
and "Finished server process" at 45.55. echo-typescript logs nothing on shutdown, so its SIGTERM
time is not visible in its log; the kubelet stops a native sidecar only after the main containers
exit (the native-sidecar contract, as seen with echo-python above).

Hung workload: exit 0.

```
[kind-poc04] SIGSTOP workload 09d61c9cc964... in agent-5556c7cf9d-7r78q (restarts so far: 0)
[kind-poc04] not Ready after 10 s
[kind-poc04] workload restarted after 41 s
[kind-poc04] PASS: Ready again after 45 s
```

Read-only root (the workload check uses `node -e` and prints the error code):

```
chassis /x OSError Read-only file system
chassis /tmp/x WRITABLE
workload /x EROFS
workload /tmp/x WRITABLE
```

### prestop, echo-typescript

`deploy/kind/run.sh apply prestop typescript`, then the same drills. Six rolling runs: runs 1, 3,
and 4 are void (harness faults, explained below); runs 2, 5, and 6 are the clean ones.

Rolling, run 1: exit 1, void. `sent 15980, ok 15690, failed 290` (10 `RemoteProtocolError`
listed; 10 `envelope status 'error'` with `transport_error: Server disconnected` from the model
hop). The fake model server was OOMKilled during the run (`lastState.terminated.reason:
OOMKilled`, exit 137, at 19:36:01Z, 256Mi limit). Cause:
`packages/fake-model-server/src/fake_model_server/app.py:69` appends every request body to
`app.state.calls`, so it grows with every call (about 75k calls since the cluster started).

Runs 3 and 4: void. I first made `run.sh drill-rolling` restart the fake model server before each
drill. Both runs then failed 141 and 112 calls with `envelope status 'error': connect_error: All
connection attempts failed`, which the chassis LiteLLM client
(`chassis/adapters/litellm/client.py:135`) returns when it cannot connect to the fake model
server. No run without that restart showed this error. So the restart itself caused it, likely
the NetworkPolicy dataplane catching up with the new fake pod's IP (hypothesis, not checked). I
reverted the restart. Instead the fake model server's memory limit is now 1Gi
(`deploy/kind/poc04/base/fake-model-server.yaml`, suggested), reapplied before run 5.

Rolling, run 2: exit 1.

```
steady_client: no-retry, 20 workers, 90 s -> sent 18368, ok 18362, failed 6, attempts 18368, retried 0, p50 87.75 ms, p95 196.01 ms
   6 RemoteProtocolError
```

Rolling, run 5: exit 1.

```
steady_client: no-retry, 20 workers, 90 s -> sent 24279, ok 24269, failed 10, attempts 24279, retried 0, p50 55.45 ms, p95 160.61 ms
   9 RemoteProtocolError
   1 ReadError
```

Rolling, run 6: exit 1.

```
steady_client: no-retry, 20 workers, 90 s -> sent 18309, ok 18304, failed 5, attempts 18309, retried 0, p50 85.71 ms, p95 205.24 ms
   5 RemoteProtocolError
```

Hung workload, three runs (after rolling runs 2, 4, and 6), all exit 0:

```
not Ready after 11 s   workload restarted after 41 s   PASS: Ready again after 45 s
not Ready after 11 s   workload restarted after 43 s   PASS: Ready again after 47 s
not Ready after 11 s   workload restarted after 41 s   PASS: Ready again after 45 s
```

Read-only root, three checks, the same each time:

```
chassis /x OSError Read-only file system
chassis /tmp/x WRITABLE
workload /x EROFS
workload /tmp/x WRITABLE
```

## Second harness fault: Valkey full

After the drills above, every call returned `HTTP 503: {"detail":"the chassis cannot reach its
state store; retry later"}`. Valkey was full, not down:

```
used_memory_human:256.02M  maxmemory_human:256.00M  maxmemory_policy:noeviction
dbsize 186218
SET probe 1 EX 5 -> OOM command not allowed when used memory > 'maxmemory'.
```

Each call stores an idempotency entry with a fresh key, so about 186k calls fill the 256mb store.
`noeviction` is right for idempotency (an evicted entry would allow a duplicate run), so this is
sizing, not a bug. It did not touch the drill runs above: their failures are all transport
errors, none 503. `run.sh drill-rolling` now runs `valkey-cli flushall` in the Valkey container
before the client starts (the password stays in that container's environment).

## Diagnosis: is it keep-alive?

Diagnosis only, not a drill result: the same client with keep-alive off
(`httpx.Limits(max_keepalive_connections=0)`, a new TCP connection per call), native sidecar,
echo-typescript, Valkey flushed first.

```
baseline, no restart, keep-alive off, 30 s: sent 5473, ok 5473, failed 0
baseline, no restart, keep-alive on,  30 s: sent 5855, ok 5855, failed 0
rolling restart, keep-alive off,      90 s: sent 17892, ok 15930, failed 1962 (all RemoteProtocolError)
```

So keep-alive is not the only cause. With a new connection per call the rolling restart loses
about 11% of calls, all "Server disconnected" on a fresh connection. That means new connections
still reach a pod after its chassis closed the public listener: the host port forward accepts,
then the backend resets. Either the endpoint removal on the node lags the 5 s
`--drain-delay-s` under this load, or the Docker Desktop port forward adds its own lag. Not
settled here. What is settled: both variants lose calls the same way, at the moment a chassis
closes its listener or its idle connections, and the container roles do not change that.

## Results

Rolling restart, 20 clients, no retry, 90 s, through `127.0.0.1:18081` (exit criterion 9 needs
0 failed). Void runs left out.

| Variant | Engine | Run | Sent | OK | Failed | Failure kinds |
| ------- | ------ | --- | ---- | -- | ------ | ------------- |
| native-sidecar | echo-python | 1 | 7071 | 7070 | 1 | RemoteProtocolError 1 |
| native-sidecar | echo-python | 2 | 6726 | 6716 | 10 | RemoteProtocolError 10 |
| native-sidecar | echo-typescript | 1 | 19417 | 19403 | 14 | RemoteProtocolError 13, ReadError 1 |
| native-sidecar | echo-typescript | 2 | 18634 | 18631 | 3 | RemoteProtocolError 3 |
| prestop | echo-python | 1 | 5262 | 5262 | 0 | none |
| prestop | echo-python | 2 | 6120 | 6113 | 7 | RemoteProtocolError 7 |
| prestop | echo-typescript | 2 | 18368 | 18362 | 6 | RemoteProtocolError 6 |
| prestop | echo-typescript | 5 | 24279 | 24269 | 10 | RemoteProtocolError 9, ReadError 1 |
| prestop | echo-typescript | 6 | 18309 | 18304 | 5 | RemoteProtocolError 5 |

Totals: native sidecar 28 failed of 51848 (0.054%); preStop 28 failed of 72338 (0.039%). One
clean run in nine (preStop, echo-python, run 1). Neither variant passes.

Hung workload (SIGSTOP from the node; seconds from the stop):

| Variant | Engine | /ready false | Workload restarted | Ready again |
| ------- | ------ | ------------ | ------------------ | ----------- |
| native-sidecar | echo-python | 11 | 37 | 42 |
| native-sidecar | echo-typescript | 10 | 41 | 45 |
| prestop | echo-python | 9 | 38 | 42 |
| prestop | echo-typescript | 11, 11, 11 | 41, 43, 41 | 45, 47, 45 |

The pod restarts the workload and comes back Ready in every run: that part of criterion 9 holds
for both. "/ready false within 10 s" holds only at the bound (9 to 11 s, whole seconds). Most of
it is the chassis's readiness monitor (3 failed probes) plus the 2 s readiness period.

Read-only root (criterion 5): holds in both variants and both engines, both containers: `/x`
fails with "Read-only file system" or `EROFS`, `/tmp` is writable.

Stop order:

| | native sidecar | preStop |
| - | - | - |
| Who gets SIGTERM first | the chassis; the kubelet stops the sidecar only after the chassis exits | the chassis, by timing: its preStop is 5 s, the workload's 35 s |
| Workload SIGTERM after chassis exit | 1.0 s (echo-python) | 29.4 s idle wait |
| Pod gone after delete | about 6.6 s | about 36 s |
| In-flight workload calls cut | none seen | none seen |
| Timing guess to keep in step | none | the workload's sleep must cover the chassis's worst-case drain |

## Recommendation for 024 CH-3

Native sidecar, but not yet as a pass. Neither variant met criterion 9's "fails no request". Both
lose a handful of calls per rolling restart, all transport errors at the moment a chassis stops,
and the difference between them (28 of 51848 against 28 of 72338) is within the noise of nine
runs. The cause is in the chassis drain and the endpoint path, which both variants share, not in
the container roles. On everything the roles do decide, the native sidecar is better: the order
is a Kubernetes guarantee rather than a 35 s guess, the pod is gone in about 7 s instead of 36,
and nothing sits idle. The plan's rule is "the native sidecar wins if both pass"; neither passed,
so the rule does not decide by itself, and this recommendation rests on the stop-order evidence.
No ADR is needed: the plan asks for one only if preStop wins.

Before criterion 9 can be ticked:

1. Chassis (not patched here, chassis source): during `--drain-delay-s`, answer with
   `Connection: close` so keep-alive clients move off a draining pod before uvicorn closes idle
   connections (`packages/chassis/src/chassis/server/lifecycle.py`, step 3). Then re-run.
2. Find why new connections still reach a pod after its listener closed (keep-alive-off
   diagnosis above): a longer `--drain-delay-s`, or a check inside the cluster (a client pod,
   not the Docker Desktop port forward) to rule the host path out.
3. Fake model server (not patched here): `app.state.calls` grows without bound
   (`packages/fake-model-server/src/fake_model_server/app.py:69`); cap it or make it opt-in.

## The pytest drills

`tests/test_kind.py` reruns both drills per variant and engine when `POC04_KIND=1` (skipped
otherwise, and never in the offline gate). Run on the live cluster, after the `run.sh` flushall
step went in:

```
$ POC04_KIND=1 uv run pytest pocs/poc-04-stateless-scalable/tests/test_kind.py -q -k "hung and native-sidecar and python"
1 passed, 7 deselected in 57.98s

$ POC04_KIND=1 uv run pytest pocs/poc-04-stateless-scalable/tests/test_kind.py -q -k "rolling and native-sidecar and python"
E         steady_client: no-retry, 20 workers, 90 s -> sent 8420, ok 8416, failed 4, ...
E           failed steady-0d202629fc024160b45c198ce3f76d1e: RemoteProtocolError: Server disconnected without sending a response.
(fails, as expected: 4 RemoteProtocolError)
```

Cluster deleted at the end: `deploy/kind/run.sh delete`, then `kind get clusters` empty.

## After the drain fix (2026-10-01, later the same day)

What changed, from other packages: while draining, every public chassis response carries
`Connection: close` (`CloseWhenDraining`, `packages/chassis/src/chassis/server/lifecycle.py`),
so a keep-alive client reconnects through the Service to another replica. The fake model
server keeps only its last 1000 calls (a `deque`, plus a `calls_total` counter).

What changed here:

- native sidecar: `--drain-delay-s 10` (was 5) and `terminationGracePeriodSeconds: 50` (was 45;
  10 + 30 + 10, suggested). The delay must exceed endpoint removal plus the client's idle
  keep-alive window (httpx `keepalive_expiry` 5 s): a connection idle through the whole delay
  never sees the header. `tests/test_read_only_kind.py` asserts the new values (9 passed; with
  the delay put back to 5 it fails 3 tests, so it does check the value). `deploy/README.md`'s
  variant row says the same.
- prestop: unchanged (preStop sleep 5, `--drain-delay-s 0`, grace 50).

Images rebuilt and the cluster recreated: `deploy/kind/run.sh up native-sidecar python`, exit 0.
Client on the host through `127.0.0.1:18081`, as before. `run.sh drill-rolling` still runs
`valkey-cli flushall` first (see "Valkey and the idempotency TTL" below).

### native-sidecar, echo-python: 3 runs, 0 failed

```
run 1: sent 10536, ok 10536, failed 0, attempts 10536, retried 0, p50 151.35 ms, p95 355.18 ms
run 2: sent 4103, ok 4103, failed 0, attempts 4103, retried 0, p50 378.87 ms, p95 911.99 ms
run 3: sent 3025, ok 3025, failed 0, attempts 3025, retried 0, p50 476.18 ms, p95 1370.44 ms
```

Throughput fell between runs (10536 to 3025 calls). There were no restarts, Valkey held 5.26M,
the node had 1.87 GiB in use, and every container's CPU was low after the run. So the cause is
not in the cluster that I could see; host load (the shared Docker VM) is the likely one. It does
not change the result: no call failed.

Stop order, pod `agent-5bd5c4785-2w9xj`, run 1 (UTC):

```
20:32:24Z     Killing chassis / Killing workload (events)
20:32:24.599  chassis GET /ready 503 (draining)
20:32:24.757  chassis last POST /v1/run 200 OK  <- clients left 0.16 s after SIGTERM (Connection: close)
20:32:34.677  chassis "Shutting down" (public, after the 10 s delay) ... 34.979 proxy "Finished"
20:32:35.595  workload "Shutting down" ... 35.696 "Finished server process"
```

### native-sidecar, echo-typescript: 2 runs, 0 failed

```
run 1: sent 13815, ok 13815, failed 0, attempts 13815, retried 0, p50 108.35 ms, p95 276.74 ms
run 2: sent 14571, ok 14571, failed 0, attempts 14571, retried 0, p50 92.94 ms, p95 295.12 ms
```

### prestop, echo-python: 3 runs, 0 failed

```
run 1: sent 4765, ok 4765, failed 0, attempts 4765, retried 0, p50 324.74 ms, p95 786.91 ms
run 2: sent 5585, ok 5585, failed 0, attempts 5585, retried 0, p50 255.18 ms, p95 750.93 ms
run 3: sent 5625, ok 5625, failed 0, attempts 5625, retried 0, p50 288.48 ms, p95 674.59 ms
```

### prestop, echo-typescript: 4 runs from the host, 3 with failures

```
run 1: sent 13829, ok 13829, failed 0, attempts 13829, retried 0, p50 92.63 ms, p95 294.81 ms
run 2: sent 11863, ok 11862, failed 1, ... RemoteProtocolError: Server disconnected without sending a response.
run 3: sent 12091, ok 12090, failed 1, ...
  failed steady-c66f8fd7...: RemoteProtocolError: Server disconnected without sending a response. [20:51:55.844Z, reused connection]
run 4: sent 13951, ok 13948, failed 3, ...
  failed steady-ba22edd3...: RemoteProtocolError: ... [20:53:18.044Z, reused connection]
  failed steady-8661f383...: RemoteProtocolError: ... [20:53:24.447Z, reused connection]
  failed steady-bbc5b0c4...: RemoteProtocolError: ... [20:53:30.910Z, reused connection]
```

For runs 3 and 4, `steady_client.py` now appends the failure time (UTC) and whether the
connection was new or reused, from httpx's `trace` extension (`connection.connect_tcp.started`).

Classification against each old pod (event `Killing`; chassis log `Shutting down` = SIGTERM after
the 5 s preStop, since `--drain-delay-s 0`; `Finished server process` = exit):

| Run | Pod | Killing | chassis SIGTERM | last POST /v1/run | chassis exit | Failure (reused conn) | After SIGTERM |
| --- | --- | ------- | --------------- | ----------------- | ------------ | --------------------- | ------------- |
| 3 | agent-748647668d-b78ts | 20:51:50 | 20:51:55.810 | 20:51:55.827 | 20:51:56.118 | 20:51:55.844 | +34 ms |
| 4 | agent-88ddb8964-sxtr6 | 20:53:12 | 20:53:18.000 | 20:53:17.580 | 20:53:18.305 | 20:53:18.044 | +44 ms |
| 4 | agent-88ddb8964-ndhkl | 20:53:19 | 20:53:24.424 | 20:53:24.421 | 20:53:24.730 | 20:53:24.447 | +23 ms |
| 4 | agent-88ddb8964-j7wgz | 20:53:25 | 20:53:30.877 | 20:53:30.909 | 20:53:31.179 | 20:53:30.910 | +33 ms |

Every failure is on a reused keep-alive connection, 23 to 44 ms after that pod's chassis got
SIGTERM and before it exited. The clients kept sending to the old pod through the whole 5 s
preStop sleep (the last `POST /v1/run` lands at SIGTERM): removing the endpoint does not move an
open connection. The preStop hook only sleeps, so the chassis does not know it is draining and
sends no `Connection: close`; then `--drain-delay-s 0` closes the idle connections at once, and a
request written at that moment is lost. That is why preStop still loses calls and the native
sidecar does not. Fixing it would mean making the chassis drain during the preStop (for example
an `httpGet` preStop to a drain endpoint, or `--drain-delay-s` instead of the sleep); I did not
redesign the variant, as asked.

In the cluster (to rule out Docker Desktop's port forward): `steady_client.py` in a ConfigMap,
run by a pod with the chassis image (python and httpx), `--url http://agent:8080 --concurrency 20
--duration-s 90 --no-retry`, plus a temporary NetworkPolicy that lets only that pod reach the
agent on 8080 and DNS (the namespace denies all egress by default). Both were created by hand in
the cluster and are not in the manifests. Same drill, prestop, echo-typescript:

```
phase=Succeeded exit=0
steady_client: no-retry, 20 workers, 90 s -> sent 17174, ok 17174, failed 0, attempts 17174, retried 0, p50 84.4 ms, p95 237.26 ms
```

One clean in-cluster run against 1 of 4 clean host runs. That does not show the host path is the
cause: the race is real and was timed above on the chassis's side. One run is too few to tell
whether the in-cluster path makes it rarer.

### Test checks (mutation)

- `POC04_KIND=1 uv run pytest tests/test_kind.py -k "rolling and native-sidecar and python"`:
  `1 passed, 7 deselected in 99.49s`.
- The same with the native sidecar's `--drain-delay-s` set to 0 (put back to 10 afterward):
  `sent 6505, ok 6500, failed 5`, so the test fails. The delay is what the pass depends on.

### Fake model server memory

With the bounded call log, its container held 47.8 MB after about 17k calls and 47.9 MB after
about 146k (`crictl stats`, no restart). The memory limit is back to 256Mi.

### Valkey and the idempotency TTL (a finding)

Each call stores one idempotency entry, TTL `ttl_s: 86400` (the default, 24 h). Measured after a
drill: 13948 keys, `used_memory` 20.26M, a sample key at `ttl 86303` s with `MEMORY USAGE` 1408
bytes. That is about 1.5 KB per call. With `--maxmemory 256mb` and `noeviction` (right for
idempotency: an evicted entry allows a duplicate run), the store holds about 175k to 186k
entries (186218 when it filled earlier today). Then every call returns 503 "the chassis cannot
reach its state store". At 24 h TTL, that budget allows a steady rate of only about 2 calls per
second (175k / 86400 s). These drills run at 50 to 270 calls per second and fill it in 11 to 60
minutes. So the `valkey-cli flushall` in `run.sh drill-rolling` stays: today's drills sent about
150k calls after the last fill. For 024 CH-3 and the idempotency design: size Valkey from rate
times TTL times about 1.5 KB, or shorten `ttl_s`. The error message also misleads: the store is
reachable but full.

## Results after the drain fix

Rolling restart, 20 clients, no retry, 90 s, client on the host through the NodePort unless
marked:

| Variant | Engine | Runs | Sent (total) | Failed | Runs with 0 failed | Failure kinds |
| ------- | ------ | ---- | ------------ | ------ | ------------------ | ------------- |
| native-sidecar | echo-python | 3 (+1 pytest) | 17664 (the 3 drill runs) | 0 | 4 of 4 | none |
| native-sidecar | echo-typescript | 2 | 28386 | 0 | 2 of 2 | none |
| prestop | echo-python | 3 | 15975 | 0 | 3 of 3 | none |
| prestop | echo-typescript | 4 | 51734 | 5 | 1 of 4 | RemoteProtocolError on reused connections, 23 to 44 ms after SIGTERM |
| prestop | echo-typescript (in cluster) | 1 | 17174 | 0 | 1 of 1 | none |

(The pytest native-sidecar run's numbers are not in its output on success; it is counted only as
a 0-failed run.)

## Recommendation for 024 CH-3 (after the drain fix)

The native sidecar. It is the only variant with 0 failed calls in every rolling restart on both
engines (5 drill runs, plus 1 run through pytest). That meets criterion 9's "the chosen container roles fail no
request under a rolling restart" for the native sidecar, on this cluster, with the client on the
host. The preStop variant still loses calls with echo-typescript, by a mechanism the timings
pin down: during its sleep the chassis does not know it is draining. The plan's rule ("the native
sidecar wins if both pass") is met in the stronger form: only the native sidecar passed. No ADR
is needed, since preStop did not win.

What CH-3 must carry with it: `--drain-delay-s` longer than endpoint removal plus the client's
idle keep-alive window (10 s here, suggested; a client with a longer idle keep-alive than
httpx's 5 s needs more), `terminationGracePeriodSeconds` of at least delay + drain timeout +
margin (50 here), and Valkey sized for rate times TTL.
