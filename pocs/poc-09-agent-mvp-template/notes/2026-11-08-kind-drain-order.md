# The library chart on kind: drain order before and after the role swap, and H14 (2026-11-08)

## Why

ADR-012 (`docs/planning/adr/012-drain-order-on-rollout.md`), accepted 2026-11-08 with option 1: in the `sidecar` lane the chassis becomes the main container and the workload the native sidecar. This run does the kind checks the ADR leaves open:

- Item 5: the PoC-4 rolling-restart drill on the chart as built (the baseline, the measured size of the risk), then on the chart after the swap. It backs the 024 CH-3 box "A rolling restart under load fails no request, on this chart on kind".
- Item 4: the chart's own H14 check. A workload that binds `127.0.0.1:8090` at start leaves the pod not Ready, and the chassis logs the taken address. The paired control binds a free loopback port and goes Ready.
- Item 7: the API server keeps the workload's probe-level `terminationGracePeriodSeconds: 10` on a restartable init container.

## Setup

- Commit: `ff61aa5` (`main`), with this note's tooling added (`deploy/kind/chart/`, the H14 test).
- Charts:
  - Baseline: `deploy/helm` at `36441d9`, from `git archive` (`run.sh install 36441d9`). The chassis is the native sidecar, the workload the main container. `deploy/helm` is the same at `ff61aa5`.
  - After the swap: the P-29 change (branch `claude/p29-drain-order`), from its worktree before it was committed: five files under `deploy/helm` changed against `36441d9` (`README.md`, `_containers.tpl`, `_deployment.tpl`, `_validate.tpl`, `_values.tpl`; diff sha256 prefix `2db43af72901`). The workload is the native sidecar, the chassis the main container.
- Machine: macOS 27.0.1, Apple M1 Pro, Docker 29.9.2 (10 CPUs, 8 GB VM: `docker info --format '{{.MemTotal}}'` = 8319438848). The PoC-5 cluster `poc05` ran next to it, untouched.
- kind v0.33.0, node `kindest/node:v1.37.0` pinned by digest (`deploy/kind/chart/cluster.yaml`), server v1.37.0. helm v4.3.1, kubectl v1.36.1, uv 0.10.10, Python 3.12.13.
- Cluster `chart`, every call with `--context kind-chart` (`deploy/kind/chart/run.sh` as first written; the security review then renamed it `agent-chart`, context `kind-agent-chart`, with the same `cluster.yaml` otherwise). Images built from `ff61aa5` by `run.sh up`: `kind.local/agent-platform/chassis:poc09`, `echo-python:poc09`, and the H14 test workload `h14-squat:poc09`.
- Release `example-agent` in namespace `agents`: `values-kind.yaml` (the `fake` profile) plus `deploy/kind/chart/values-drill.yaml` (3 replicas, no HPA). Chart defaults: `--drain-delay-s 10`, `--drain-timeout-s 30`, `terminationGracePeriodSeconds: 50`, `maxUnavailable: 0`, `maxSurge: 1`.
- Client path: the host reaches the chassis port through `127.0.0.1:18082` and a NodePort Service the drill adds (`deploy/kind/chart/nodeport.yaml`), so calls go through kube-proxy, as in PoC-4. Smoke check before the runs: `steady_client --concurrency 2 --duration-s 3 --no-retry` sent 106, ok 106.
- Client: `python -m load_tools.steady_client --concurrency 20 --duration-s 90 --no-retry`, started 10 s before `kubectl rollout restart`. One `httpx.AsyncClient` with keep-alive connections.
- Each run was wrapped to follow every old pod's `chassis` and `workload` logs (`kubectl logs -f --timestamps`) and save the events, for the stop order below. The wrapper only reads.

## Command

```bash
deploy/kind/chart/run.sh up
deploy/kind/chart/run.sh install 36441d9            # baseline
deploy/kind/chart/run.sh drill-rolling 90           # twice
deploy/kind/chart/run.sh install <p29 worktree>/deploy/helm   # after the swap
deploy/kind/chart/run.sh drill-rolling 90           # twice
KIND_DRILLS=1 H14_CHART_DIR=<p29 worktree>/deploy/helm uv run pytest -m network \
  pocs/poc-09-agent-mvp-template/tests/test_poc09_kind_h14.py
```

`install` printed the roles of each pod:

```
baseline:   example-agent-7959cb7b7-hmj84 init=[chassis(Always) ] main=[workload ]
after swap: example-agent-557f4cd64f-7rlf7 init=[workload(Always) ] main=[chassis ]
```

## Output: the rolling-restart drill

Baseline, chart as built. Run 1, exit 1 (the failure list trimmed after 3 lines; the client prints 20, all of this one kind):

```
deployment "example-agent" successfully rolled out
steady_client: no-retry, 20 workers, 90 s -> sent 8587, ok 8072, failed 515, attempts 8587, retried 0, p50 206.07 ms, p95 384.83 ms
  failed steady-13569d96c9284eef9b4ea0f009cbac91: envelope status 'error': {"text": "", "error": {"code": "a2a.transport", "message": "the agent could not be reached"}}
  failed steady-3d967646c022442995def75518c75d0a: envelope status 'error': {"text": "", "error": {"code": "a2a.transport", "message": "the agent could not be reached"}}
  failed steady-50afecc85db144cc9850eab04a527a85: envelope status 'error': {"text": "", "error": {"code": "a2a.transport", "message": "the agent could not be reached"}}
  ... and 495 more
```

Baseline, run 2, exit 1 (trimmed the same way):

```
steady_client: no-retry, 20 workers, 90 s -> sent 8365, ok 7901, failed 464, attempts 8365, retried 0, p50 194.42 ms, p95 501.25 ms
  failed steady-4658a761d7bb4d30a5f1084d87f50cd2: envelope status 'error': {"text": "", "error": {"code": "a2a.transport", "message": "the agent could not be reached"}}
```

After the swap. Run 1, exit 0:

```
deployment "example-agent" successfully rolled out
steady_client: no-retry, 20 workers, 90 s -> sent 8009, ok 8009, failed 0, attempts 8009, retried 0, p50 211.77 ms, p95 399.59 ms
```

After the swap, run 2, exit 0:

```
deployment "example-agent" successfully rolled out
steady_client: no-retry, 20 workers, 90 s -> sent 6242, ok 6242, failed 0, attempts 6242, retried 0, p50 279.62 ms, p95 488.51 ms
```

| Chart | Run | Sent | Failed | Failed share |
| ----- | --- | ---- | ------ | ------------ |
| as built (`36441d9`) | 1 | 8587 | 515 | 6.0% |
| as built (`36441d9`) | 2 | 8365 | 464 | 5.5% |
| after the swap (P-29) | 1 | 8009 | 0 | 0 |
| after the swap (P-29) | 2 | 6242 | 0 | 0 |

## Stop order, from the container logs (UTC)

Baseline, run 1. `workload SIGTERM` is the workload's uvicorn `Shutting down` line. `runs after` counts the chassis's `POST /v1/run` access-log lines from that moment.

```
pod     workload SIGTERM  workload finished  chassis runs after  last run      chassis /ready 503  chassis shutting down
tdx26   18:01:29.745      18:01:29.846       143                 18:01:30.323  18:01:32.262        18:01:40.389
v8l5n   18:01:40.991      18:01:41.204       184                 18:01:41.740  18:01:49.940        18:01:51.734
hmj84   18:01:50.032      18:01:50.334       211                 18:01:50.722  18:01:58.254        18:02:00.775
```

Baseline, run 2: 150, 166, and 177 runs after the workload's SIGTERM, each pod's last within 0.6 to 0.7 s of it.

After the swap, run 1 (run 2 is the same shape):

```
pod     chassis /ready 503  last run      chassis shutting down  chassis finished  workload shutting down
cbh7c   18:07:21.357        18:07:21.438  18:07:31.195           18:07:31.498      18:07:32.702
7rlf7   18:07:31.329        18:07:30.646  18:07:40.553           18:07:40.855      18:07:41.973
lgzbk   18:07:40.356        18:07:39.171  18:07:48.848           18:07:49.150      18:07:50.415
```

## Reading: the rolling restart

- The baseline fails about 5.5 to 6% of calls on one rollout of 3 pods: 515 and 464. That is the measured size of the risk ADR-012 reasoned about.
- Every listed baseline failure is the same: an HTTP 200 envelope with `a2a.transport`, "the agent could not be reached". The kubelet stops the workload first. The chassis is not signaled, so it is not draining: no `Connection: close`, and its `/ready` stays 200 for another 2 to 8 s. Calls on the client's open keep-alive connections reach a chassis with no workload behind it. Each fails in a few milliseconds, so one pod turns 143 to 211 calls into failures before the calls stop.
- The calls stop 0.5 to 0.7 s after the workload exits, before the chassis's own `/ready` goes 503. The logs do not show what closes them. A likely cause: the kubelet marks the pod not Ready as soon as a main container exits, and the endpoint leaves the Service. ADR-012 expected 3 failed `/ready` probes first; the window is shorter than that, but the failures are many because each one is fast.
- After the swap: 0 failed in both runs. The 024 CH-3 box ticks.
- The order held on every pod: the chassis drained first (`/ready` 503 at once, the 10 s drain delay, then the listener closed), and the workload got SIGTERM 1.1 to 1.3 s after the chassis finished. No workload call was cut.
- The keep-alive race at the end of the drain delay (PoC-4, ADR-012 Consequences) did not show. Calls stopped reaching a draining pod within 0.1 s of its `/ready` 503 (`cbh7c`; on `7rlf7` and `lgzbk` the last run came before it). Since PoC-4, every response from a draining chassis carries `Connection: close` (`CloseWhenDraining`, `packages/chassis/src/chassis/server/lifecycle.py`), so each client connection closes after its in-flight call and the next call opens a new one to a Ready pod. No connection sat idle on a draining pod until step 4. ADR-012's "Revisit" analysis is not needed: there were no failures to explain.

## Output: H14 and item 7

The test, against the P-29 chart. It installs two releases of `example-agent` in namespace `h14`, one replica each, both with the H14 test workload (`deploy/kind/chart/h14/`). They differ in one value: `H14_BIND_PORT` 8090 (taken) or 8095 (free).

```
pocs/poc-09-agent-mvp-template/tests/test_poc09_kind_h14.py::test_h14_free_loopback_port_goes_ready PASSED [ 33%]
pocs/poc-09-agent-mvp-template/tests/test_poc09_kind_h14.py::test_h14_taken_proxy_port_fails_the_pod_closed PASSED [ 66%]
pocs/poc-09-agent-mvp-template/tests/test_poc09_kind_h14.py::test_api_server_keeps_probe_grace_on_the_restartable_init_container PASSED [100%]

========================= 3 passed in 75.97s (0:01:15) =========================
```

The same two releases installed by hand afterwards in namespace `h14ev`, read after 75 s, then removed:

```
NAME                         READY   STATUS             RESTARTS      AGE
h14-free-56fc6c7879-tlpx4    2/2     Running            0             83s
h14-taken-67c9455579-xdwqp   1/2     CrashLoopBackOff   3 (20s ago)   82s
--- taken chassis log (previous)
chassis: the proxy listener could not bind 127.0.0.1:8090: the address is in use or is not on this host. The chassis does not run without its proxy, so nothing else started.
--- taken workload log
h14: holding 127.0.0.1:8090
--- taken chassis state
exit=3 restarts=3 ready=False
--- free stored workload spec
workload restartPolicy=Always liveness.terminationGracePeriodSeconds=10 main=chassis ready=True
```

## Reading: H14 and item 7

- H14 holds by refusal on this chart. With 8090 taken, the chassis exits 3 (`ProxyBindFailed`) and names `127.0.0.1:8090`. The kubelet keeps restarting it, and the pod is never Ready, so the Service sends it nothing. The squatter itself was up: its startup probe passed and its agent card answered.
- The control: the same image with a free loopback port goes `2/2 Running`, Ready, with no chassis restart. So the refusal is the port, not the image or the chart.
- Item 7: the API server stored the workload as an init container with `restartPolicy: Always` and its liveness probe's own `terminationGracePeriodSeconds: 10`, and the chassis as the only main container. Read back from the live pod, not the render.
- The public port 8080 was not covered in this first run; the rerun below adds it.

## Limits

- The `fake` profile: no LiteLLM, Valkey, or tool gateway. The drill checks the stop order and the client's view, not a backend under drain.
- One kind node, a host client through Docker Desktop's port mapping. Two runs per chart.
- The first post-swap runs used the P-29 worktree before its commit; the rerun below uses the pushed head. The test's default chart is this checkout's `deploy/helm`, so it passes once P-29 is on `main`. On the old chart the taken release fails in the workload instead (the chassis binds 8090 first).
- The baseline's failure list shows 20 of 515 and 20 of 464. The chassis logs account for the rest: 538 and 493 `POST /v1/run` responses after the workloads' SIGTERM. That count also holds the calls already in flight, which the draining workload finished.

## Rerun on the P-29 head (`2f4fbe8`), 2026-11-08

Why: after the first post-swap runs, the P-29 chart changed (security review): the chassis startupProbe is an exec that checks a ready marker (`chassis serve --ready-marker /tmp/chassis-ready`, written after the proxies and the public listener bind, in `chassis-tmp`, which no other container mounts), and `_validate.tpl` caps the drain values. That needs a chassis image built from the same head.

Setup changes against the runs above:

- Chart and images: PR #107, branch `claude/p29-drain-order`, head `2f4fbe8`. Images built from a detached worktree of that head (`BUILD_ROOT=<worktree> deploy/kind/chart/run.sh build`, which logs `building from ... (2f4fbe8)`), chart from `run.sh install 2f4fbe8`.
- Cluster `agent-chart`, context `kind-agent-chart` (renamed after the security review; same `cluster.yaml` otherwise). The H14 test now has a third release, `h14-public` (below).

Rolling restart, run 1, exit 0:

```
steady_client: no-retry, 20 workers, 90 s -> sent 6312, ok 6312, failed 0, attempts 6312, retried 0, p50 283.98 ms, p95 490.4 ms
```

Run 2, exit 0:

```
steady_client: no-retry, 20 workers, 90 s -> sent 7736, ok 7736, failed 0, attempts 7736, retried 0, p50 204.89 ms, p95 474.55 ms
```

Stop order on the six old pods (UTC, from their logs as above):

```
run pod    /ready 503     last run      chassis shutting down  chassis finished  workload shutting down
1   lf2bq  18:44:02.839   18:44:01.716  18:44:11.373           18:44:11.676      18:44:12.957
1   tn42s  18:44:14.989   18:44:13.994  18:44:23.852           18:44:24.155      18:44:25.363
1   dx45d  18:44:26.976   18:44:26.879  18:44:36.265           18:44:36.567      18:44:37.799
2   7jxbz  18:45:57.314   18:45:57.274  18:46:07.202           18:46:07.505      18:46:09.025
2   jbrhn  18:46:08.213   18:46:08.305  18:46:17.905           18:46:18.207      18:46:19.356
2   lfqwp  18:46:19.756   18:46:18.672  18:46:28.172           18:46:28.474      18:46:29.719
```

The exec startup probe under the chassis's 500m CPU limit (period 2 s, timeout 2 s, failure threshold 30, so 60 s). The new pods of each rollout, from the pod status and the `Unhealthy` events (`startupProbe` failures on the chassis container only):

```
run 1: chassis start -> Ready 8 s, 8 s, 7 s; chassis startup-probe failures 3, 3, 3
run 2: chassis start -> Ready 8 s, 6 s, 8 s; chassis startup-probe failures 3, 2, 3
```

The probe command itself, timed through `kubectl exec` into a running chassis, ten times: 0.10 to 0.12 s wall clock.

H14 and item 7, 4 passed:

```
KIND_DRILLS=1 H14_CHART_DIR=<p29 head worktree>/deploy/helm uv run pytest -m network -p no:cacheprovider -v \
  pocs/poc-09-agent-mvp-template/tests/test_poc09_kind_h14.py
...::test_h14_free_loopback_port_goes_ready PASSED [ 25%]
...::test_h14_taken_proxy_port_fails_the_pod_closed PASSED [ 50%]
...::test_h14_taken_public_port_that_answers_200_fails_the_pod_closed PASSED [ 75%]
...::test_api_server_keeps_probe_grace_on_the_restartable_init_container PASSED [100%]
======================== 4 passed in 146.19s (0:02:26) =========================
```

The `h14-public` chassis log, from an earlier run of the same case (that run failed only on the test's log string, which was then fixed to match this text; trimmed to the error lines):

```
"message": "Uvicorn running on http://127.0.0.1:8090 (Press CTRL+C to quit)"
"message": "[Errno 98] error while attempting to bind on address ('10.244.0.15', 8080): [errno 98] address already in use"
"message": "the public listener failed to start"
```

Reading:

- The chart as merged in #107 passes the drill: 0 failed in both runs, with no retry. The stop order held on all six pods: the chassis drained first, and the workload got SIGTERM 1.1 to 1.5 s after the chassis finished.
- The exec startup probe passes in 6 to 8 s from the chassis's container start, after 2 or 3 failed checks. That is the chassis importing and binding under 500m, not the probe being slow: the check itself runs in about 0.1 s. The budget is 60 s, so about 7 times the measured time.
- H14 holds by refusal on both chassis ports. The new case: a workload that holds `0.0.0.0:8080` and answers every GET with 200. In the same test, a GET from inside the pod to the pod IP's 8080 `/ready` returned 200, so an HTTP startup or readiness probe would have passed. The pod was never Ready and had no ready endpoint in its Service for the 60 s it was watched, across at least one chassis restart. The chassis could not bind the public port and exited. The startupProbe reads the marker only, so the readiness probe never ran against the squatter. This closes the security review's MEDIUM 3 on this chart.
- Item 7 holds on the head too: the stored spec keeps the workload's probe-level `terminationGracePeriodSeconds: 10` on the restartable init container.
