# ADR-012: In the `sidecar` pod, the chassis is the main container and the workload the native sidecar

- **Date:** 2026-11-08
- **Deciders:** the user, who picked option 1 of proposal P-29 on 2026-11-08; proposed by `chassis-architect`
- **Format:** Michael Nygard's template: Status, Context, Decision, Consequences
- **Related:** proposal P-29 (and the decided P-6) in the [backlog review proposals](../../reviews/2026-11-06-backlog-review/proposals.md); [024 CH-3](../issues/024-CH-3-helm-library-chart.md) (the rolling-restart box and the container-order box); [contract v3](../../contracts/contract-v3.md#shutdown) "Shutdown"; [contract v4](../../contracts/contract-v4.md) "Bind order" (H14); [the PoC-4 container roles note](../../../pocs/poc-04-stateless-scalable/notes/2026-10-01-container-roles.md); the gap review in [the product plan](../../plans/2026-11-06-agent-container-product.md#if-phases-a-to-f-are-all-built-what-is-still-missing)

## Status

Accepted 2026-11-08, by the user: option 1. Proposed the same day.

Evidence that is not in yet: items 4, 5, and 7 of the decision run on kind. Until the drill passes, 024 CH-3's rolling-restart box stays open.

## Context

**The rule.** The chassis drains on SIGTERM, then the workload stops. Contract v3 says it, and no later version changes it (`docs/contracts/contract-v3.md:102-116`). In order: `/ready` goes 503 `draining` and every public response carries `Connection: close`; the chassis sleeps `--drain-delay-s`; the public listener closes and in-flight runs get `--drain-timeout-s`; pending events get up to 5 s; the proxies close last. Line 116: "the workload must get SIGTERM after the chassis has drained (Kubernetes native sidecar, or preStop sleeps)". The spec in force says the same (`docs/specs/contract.md:304`). The code: `packages/chassis/src/chassis/server/lifecycle.py:11-25` (the steps), `:89-94` (`DrainSettings`, 5 s and 30 s, suggested).

**The chart breaks it.** In the `sidecar` lane the chart makes the chassis the native sidecar and the workload the main container:

- `deploy/helm/agent-lib/templates/_deployment.tpl:59-63`: the chassis goes in `initContainers`, the workload in `containers`. The comment at `:4-8` says why: the bind order, and "024 CH-3 leaves the pick to the owner".
- `deploy/helm/agent-lib/templates/_containers.tpl:17-21`: `restartPolicy: Always` on the chassis only.
- No `lifecycle` and no `preStop` anywhere under `deploy/helm` (searched; none).
- `terminationGracePeriodSeconds: 50`, shared by the whole pod (`_values.tpl:15-16`, rendered at `_deployment.tpl:56`). The chassis gets `--drain-delay-s 10 --drain-timeout-s 30` (`_values.tpl:32-33`, `_containers.tpl:37-40`); the workload `--drain-timeout-s 30` (`_values.tpl:52`).
- The rollout is `maxUnavailable: 0`, `maxSurge: 1` (`_deployment.tpl:38-42`).
- `deploy/helm/README.md:44` states the roles and that "PoC-4 measured the reverse for drain".

Kubernetes stops the main containers first. It stops a native sidecar only after every main container has exited, with what is left of the pod's grace. So on every rollout, scale-down, or eviction:

1. The workload gets SIGTERM first. It stops accepting and finishes its in-flight `handle` calls.
2. The chassis is not signaled. It is not draining, so it sends no `Connection: close` and `/ready` stays 200. It sees the workload gone only after 3 failed probes, 2 s apart (`packages/chassis/src/chassis/server/readiness.py:31-36`), then answers 503 `workload_unreachable`.
3. Removing the endpoint stops new connections only. kube-proxy keeps an open keep-alive connection pinned to its pod (PoC-4 note, lines 52-55). Calls on those connections reach a chassis with no workload behind it, and fail.
4. When the workload exits (up to 30 s), the chassis gets SIGTERM with as little as 20 s left. It needs up to 45 s (10 + 30 + 5). Its 10 s delay serves calls with no workload behind them.

This is reasoning from the code, not a measurement. PoC-5 chose this order and wrote down the cost: "on delete the workload gets SIGTERM before the chassis, so PoC-4's drain order does not hold here; PoC-5 does not measure drain" (`deploy/kind/poc05/agents/agent-echo.yaml:4-9`). The 024 CH-3 box "A rolling restart under load fails no request, on this chart on kind" is open (`docs/planning/issues/024-CH-3-helm-library-chart.md:56`).

**Why PoC-5 reversed PoC-4.** Bind order, H14 (`docs/plans/2026-10-02-poc-05-sandboxed.md:341`; contract v4 line 116). The kubelet starts the main container only after the native sidecar's `startupProbe` passes. The chassis answers `/health` only after it has bound its proxies. So the workload cannot take `127.0.0.1:8090` first. `lifecycle.py:36-40` states both halves: "a workload can never bind the proxy port first, and the chassis never runs without its proxy". Only the first half depends on the container order. `pocs/poc-05-sandboxed/tests/test_poc05_hardening_static.py:456-463` and `pocs/poc-09-agent-mvp-template/tests/test_poc09_chart.py:97-98` assert the chassis is the init container.

**What PoC-4 measured on kind** (`kindest/node:v1.37.0`, 3 replicas, 20 workers, no retry, drain delay 5 s; the container roles note):

- Workload as the native sidecar, chassis as the main container (lines 18-59): the chassis got SIGTERM first; the workload got it 1.0 s after the chassis exited. No workload call was cut. Two runs failed 1 and 10 requests of about 7,000. Every failure was the keep-alive race at the end of the drain delay, chassis source, not the container order. The chart's 10 s delay answers that race (`_values.tpl:15`; 024 CH-3 line 28).
- Both as main containers with preStop sleeps (lines 83-117): the order held "by timing only". The workload sat idle for 29 s. Run 2 failed 7 requests, the same race.

**The other stacks.**

- `inprocess`: one process. The chassis drains its own in-memory server. No container order.
- `remote`: the chassis is the only container in its pod (`_deployment.tpl:64-66`), so it drains its own pod in the right order. The order between a chassis and its remote workload pod across a rollout is a separate open point (contract v4 lines 309 and 366). This ADR does not settle it.
- Compose: the workload joins the chassis's network namespace, so it `depends_on` the chassis (`deploy/compose/docker-compose.sidecar.yaml:15-21`, `:99-103`; `docker-compose.scale.yaml:323-326`). `compose stop` and `down` stop dependents first: the workload first, the same wrong order. A reverse `depends_on` is a cycle. Compose runs no rollout. The drain drill stops the chassis first by hand (`pocs/poc-04-stateless-scalable/load/kill_drill.py:15-16`, `:72`). `stop_grace_period: 45s` on both (`docker-compose.scale.yaml:53-54`, `:133`).

**What the change touches.** No envelope, no event schema, no `handle`, no port, no A2A mapping, no `spec.*` key. The chart's pod shape is a deploy rule (contract v3 line 116). Only option 2 adds a contract surface (a route on the proxy listener).

**Options.**

| | Option | Drain order | Bind order (H14) | Contract | Cost |
| - | ------ | ----------- | ---------------- | -------- | ---- |
| 0 | As built | Wrong: the workload stops first | Holds by start order | None | Failed requests on every rollout (expected; not measured on the chart) |
| 1 | Swap the roles: the chassis is the main container, the workload the native sidecar (P-29 option 1; PoC-4's order) | Right, enforced by the kubelet. Measured in PoC-4 | No longer by start order. A workload that takes 8090 or 8080 first makes the chassis exit; the pod never goes Ready | None | Chart, its README, two tests, one code comment. A defense-in-depth loss for a hostile workload in the trusted lane (see Consequences) |
| 2 | Keep the roles. The workload's `preStop` calls a new loopback route that starts the chassis drain, then waits for it to end (P-29 option 2) | Right while the hook works. A failed hook is logged and the kubelet sends SIGTERM at once: back to option 0, silently | Holds by start order | A new route on the proxy listener; the drain gains an outside trigger; a chassis already drained must exit on its own SIGTERM | Every workload image needs an HTTP client for the hook (as `workload.probeCommand` today); the hook's wait counts against the grace |
| 3 | Both main containers; a `preStop` sleep on the workload (contract v3 line 116, "or preStop sleeps") | Right by timing only. The sleep is a guess that must track the drain values | Lost: regular containers start without waiting | None | Every delete waits the full sleep (PoC-4: 29 s idle). Worse than 1 on both counts |

## Decision

Option 1, picked by the user on 2026-11-08 (P-29).

1. In the `sidecar` lane, `agent-lib` renders the workload as the native sidecar (`initContainers`, `restartPolicy: Always`) and the chassis as the main container. The `remote` lane does not change.
2. The kubelet then sends the chassis SIGTERM first. The chassis drains as contract v3 says. The workload gets SIGTERM only after the chassis has exited, with nothing left in flight.
3. `terminationGracePeriodSeconds` stays 50 (suggested): the chassis's worst case is 10 + 30 + 5 = 45 s, and the workload exited in 1.0 s in PoC-4. The render refuses a grace below `drainDelaySeconds + drainTimeoutSeconds + 5` (suggested: the 5 s is the pending-events bound).
4. H14 changes from "the workload cannot bind first" to "a taken port fails the pod closed". The chassis already exits non-zero on a taken proxy port (`ProxyBindFailed`) or public port. The chart gets its own H14 check on kind: a workload that binds `127.0.0.1:8090` at start leaves the pod not Ready, and the chassis logs the taken address; the paired control, a workload that binds a free loopback port, goes Ready.
5. The 024 CH-3 rolling-restart box ticks on this chart on kind (suggested: the PoC-4 drill, 3 replicas, `steady_client --concurrency 20 --no-retry`, 90 s, two runs). First run it on the chart as built and record the failed count: that is the measured size of the risk. Then on option 1: zero failed requests in both runs.
6. What changes when the user picks option 1: `_deployment.tpl:59-63` and its comment; `_containers.tpl:17-21` (`restartPolicy` moves to the workload); `deploy/helm/README.md:44`; `test_poc09_chart.py:97-98`; the docstring at `lifecycle.py:36-40`; `docs/specs/architecture.md` (the `sidecar` lane line); one "Container order" line in `docs/specs/contract.md`, next to the drain rule. The PoC-5 manifests and their static test stay as PoC-5's record (suggested).
7. Check on kind, not assumed: the API server accepts the workload's probe-level `terminationGracePeriodSeconds: 10` (`_containers.tpl:169`) on a restartable init container.
8. Compose does not change. Its stacks run no rollout. A script that measures drain stops the chassis first, as `kill_drill.py` does.

## Consequences

Across the lanes:

- `inprocess`: no cost.
- `sidecar`: the order the contract asks for, enforced by the kubelet, with no hook to forget and no sleep to guess. The pod's start order flips: the workload starts first, and the chassis starts after the workload's `startupProbe` passes. The in-repo workloads reach the proxies only inside `handle` (for example `packages/workloads/echo-langgraph/src/echo_langgraph/tools.py:81`, per run), and PoC-4 ran echo-python and echo-typescript this way. A workload that calls the proxies at boot would fail its start.
- `remote`: no change in the chart's pods. The cross-pod order stays open.

Easier:

- A rollout, a scale-down, and an eviction drain through one path.
- `/ready` 503 and `Connection: close` reach clients before the listener closes, as PoC-4 measured.
- No new contract surface, no `schema_version` change, nothing new in a workload image.

Harder:

- The bind order no longer holds by time. It holds by refusal: the chassis exits on a taken port, and a pod without a running chassis is never Ready, so the Service sends it nothing. The NetworkPolicy admits the chassis port in (`_networkpolicy.tpl:9-11`, `:40-42`), from `networkPolicy.ingressFrom`, or from any pod when that list is empty. So a hostile workload that takes the pod IP's port 8080 first can answer a client that dials the pod IP directly. Its answer passes no auth, no guard, and no marking. It still holds no credential, and LiteLLM and the gateway refuse it (ADR-001 hard requirement 1). The `sidecar` lane runs trusted workloads only (admission rule 2); hostile code goes to `remote`. So this is a defense-in-depth loss, not an escape. If the user wants H14 to hold by privilege rather than by order, a follow-up can give the chassis ports below 1024 that only it may bind (`NET_BIND_SERVICE`, allowed by Pod Security `restricted`, plus a file capability in the chassis image). suggested: not in this change; its own ADR if wanted, reviewed by `platform-security`.
- H14 holds by refusal plus a marker-gated startup probe (platform-security review, 2026-11-08). The chassis is Running while it imports, so an `httpGet` startup probe on the public port could be answered by a workload that took that port first, and the pod would go Ready. The chart's chassis startupProbe checks a file instead: `chassis serve --ready-marker` writes it only once every listener has bound, in `chassis-tmp` (no other container mounts it), and the console script deletes a stale one before it imports anything, since the emptyDir outlives a container restart. Readiness and liveness stay HTTP; the kubelet runs them only after startup passes.
- Two assertions flip, and the chart's H14 check is new work (item 4).
- The keep-alive race at the end of the drain delay is still there, in any order. The 10 s delay is meant to cover it. Only the drill shows whether it does.

Option 2 would keep H14 by order. But it adds a route to the proxy listener and a hook to every workload image. A failed hook turns silently back into option 0.

## Revisit

- The option 1 drill on kind fails any request with no retry. If every failure is the keep-alive race, the drain delay or the client's retry is reopened, not this order.
- The chart's H14 check shows a path where a pod with a taken port goes Ready.
- A supported workload must call the proxies before its first `handle` (a tool list at boot, for example).
- Kubernetes gives an explicit stop order between containers. Then the bind order and the drain order can both hold by order.
