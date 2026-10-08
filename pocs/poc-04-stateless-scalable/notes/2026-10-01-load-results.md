# PoC-4 load results, 2026-10-01

Exit criterion 4 (throughput grows with replicas, every engine) and the inputs to criterion 11 (the sidecar's cost next to ADR-001). Plan: `docs/plans/2026-10-01-poc-04-stateless-scalable.md`, sections 8a, 9, 13. Scripts: `pocs/poc-04-stateless-scalable/load/`.

**Criterion 4 verdict:** of the 8 growth steps (4 engines, 1 to 2 and 2 to 4 pairs), 4 grow by the gate's rule: echo-pydanticai 1 to 2 and 2 to 4, echo-langgraph 1 to 2, and echo-typescript 1 to 2. The other 4 are strict xfails in `tests/test_load_results.py`: echo-python 1 to 2 and 2 to 4, echo-langgraph 2 to 4, and echo-typescript 2 to 4. 4 pairs do not fit this 7.9 GiB Docker Desktop VM without starving it.

Corrections after review (2026-10-01, evening), made in place. Superseded wording is marked, not deleted:
- (a) one ADR-001 statement, in the headline section;
- (b) the hop wording;
- (c) the failure count;
- (d) the verdict above;
- the time of the shared bottleneck check (it was taken from local time, UTC+2).

The headline is the **quiet pass** (18:28 UTC onward). It ran with every other agent stopped. The three earlier passes ran while other agents were building on the same Mac. They are kept further down, clearly labeled, as the record of why the rerun was needed.

## Quiet pass: setup

- Host: macOS 26.6.2 arm64, 14 CPUs. `uptime` at 18:28 UTC: `load averages: 1.80 4.17 9.99` (falling after the other agents stopped), and 1.78 at 18:29.
- Docker Desktop VM: 14 CPUs, 7,935 MiB.
- Background load: 17 unrelated containers (`paligo-*`, `opensearch-*`), left untouched. One `docker stats --no-stream` at 18:28 UTC: 9.16% CPU in total, so about 0.09 vCPU. File: `notes/load/passes/quiet/background-docker-stats.txt`.
- Images: `scale.sh build` from 16:22:01 to 16:24:57 UTC, not rebuilt.
- Changes before this pass, from the noisy passes:
  - `deploy/compose/traefik/poc04.yaml`: health check every 2 s with a 3 s timeout (suggested; was 1 s and 1 s). `tests/test_read_only_compose.py` was updated to match, and is green (24 passed).
  - `load/run_matrix.py`: the hop runs 1 user in one Locust process (was 8 users in 4 processes). A Locust run with failed requests is kept, with `ok_rps`. Load-phase `docker stats` are written even when Locust fails. A new `rate` kind runs 10 users at 1 call per second each (`LOAD_RATE_PER_USER`, `locustfile.py`), so 10 RPS on 1 pair per engine.
  - `load/run_quiet.sh`: the pass itself, 60 s per scenario after a 10 s warm-up.

## Quiet pass: load-shape check on echo-python (64 users, 60 s)

```console
$ uv run python pocs/poc-04-stateless-scalable/load/run_matrix.py --engine echo-python --pairs 1 --pairs 2 --only main --duration-s 60 --results .../sanity.json
echo-python-1p: 75.48 rps, p50 840.0 ms, p95 1100.0 ms, failures 0/4448
echo-python-2p: 81.95 rps, p50 770.0 ms, p95 1100.0 ms, failures 0/4951
```

Mean (max) vCPU per replica:

| Pairs | Chassis | Workload | Fake model server | Traefik |
| ----- | ------- | -------- | ----------------- | ------- |
| 1 | 0.65 (0.90) | 0.76 (0.92) | 0.13 | 0.02 |
| 2 | 0.70 (0.94) | 0.80 (1.08) | 0.21 | 0.04 |

At 1 pair the chassis and the workload are near their 1-CPU caps, so 1 pair is saturated by the stack, not by the users. Users were then raised to check that, with 2 pairs and 128 users for 45 s (`scale.sh up echo-python 2`, Locust by hand):

```console
Aggregated   3262   0(0.00%) |  1734 ms avg  1700 ms med | 72.62 req/s
docker stats at 25 s: poc04-workload-1-1 106.11%, poc04-workload-2-1 102.89%, chassis 52% and 68%, fake model server 8.5%, traefik 3.5%
host: each Locust process 0.5 to 1.0% CPU; load averages: 3.89
```

More users gave no more throughput: latency doubled instead. Both workloads sat at their 1-CPU cap. The client is not the limit, and neither are Traefik, the fake model server, or Valkey. The odd part: at 2 pairs each echo-python workload spends about 20 to 28 ms of CPU per call, against about 10 ms at 1 pair. The chassis goes from about 8.6 ms to about 17 ms per call. CPU per call grows with the number of pairs, so adding pairs adds little. The cause is not known. One candidate is the Docker Desktop VM scheduling vCPUs onto the host's efficiency cores, but that is a guess and is not measured. Users stay at 64 (suggested) for every main scenario. Raw files: `notes/load/passes/quiet/sanity-echo-python.json`, `sanity-raw/`.

## Quiet pass: throughput (exit criterion 4)

```console
$ pocs/poc-04-stateless-scalable/load/run_quiet.sh        # 18:34:26 to 19:06:47 UTC
== echo-python 18:34:26 load averages: 5.41 4.19 7.93
echo-python-1p: 50.79 rps, p50 1200.0 ms, p95 1600.0 ms, failures 0/2998
echo-python-2p: 74.67 rps, p50 820.0 ms, p95 1400.0 ms, failures 1/4512
echo-python-4p: 82.13 rps, p50 720.0 ms, p95 1400.0 ms, failures 0/4953
== echo-pydanticai 18:39:39 load averages: 17.00 9.31 8.90
echo-pydanticai-1p: 68.32 rps, p50 910.0 ms, p95 1200.0 ms, failures 0/4024
echo-pydanticai-2p: 77.78 rps, p50 800.0 ms, p95 1600.0 ms, failures 0/4591
echo-pydanticai-4p: 84.76 rps, p50 680.0 ms, p95 1400.0 ms, failures 0/5124
== echo-langgraph 18:44:53 load averages: 15.58 11.34 9.77
echo-langgraph-1p: 55.57 rps, p50 1100.0 ms, p95 1600.0 ms, failures 0/3268
echo-langgraph-2p: 71.77 rps, p50 840.0 ms, p95 1300.0 ms, failures 0/4236
echo-langgraph-4p: 69.31 rps, p50 830.0 ms, p95 1800.0 ms, failures 0/4190
== echo-typescript 18:50:04 load averages: 18.36 12.49 10.45
echo-typescript-1p: 157.36 rps, p50 390.0 ms, p95 630.0 ms, failures 0/9486
echo-typescript-2p: 194.44 rps, p50 320.0 ms, p95 700.0 ms, failures 0/11731
echo-typescript-4p: 192.6 rps, p50 250.0 ms, p95 940.0 ms, failures 4/11596
```

The host load average of 15 to 18 during the pass is the stack itself: the Docker VM's busy vCPUs count toward the macOS load. No other job ran. Failures were 5 in 70,709 calls: the sum of `requests` and `failures` over the 12 main scenarios in `results.json`. Corrected; an earlier version said 54,000, a miscount. With the 3 s health-check timeout, no 503 burst occurred.

| Engine | 1 pair | 2 pairs | 4 pairs | Chassis vCPU per replica, 1 / 2 / 4 | Chassis CPU per call, 1 / 4 pairs |
| ------ | ------ | ------- | ------- | ----------------------------------- | --------------------------------- |
| echo-python | 50.8 (and 75.5 in the shape check) | 74.7 (and 82.0) | 82.1 | 0.67 / 0.71 / 0.77 | 13 / 37 ms |
| echo-pydanticai | 68.3 | 77.8 | 84.8 | 0.58 / 0.66 / 0.61 | 8 / 29 ms |
| echo-langgraph | 55.6 | 71.8 | 69.3 | 0.66 / 0.67 / 0.72 | 12 / 42 ms |
| echo-typescript | 157.4 | 194.4 | 192.6 | 0.94 / 0.94 / 0.89 | 6 / 19 ms |

Successful RPS. Users: 64 in every main scenario.

Verdict on criterion 4, under the test's rule: the lowest run at the higher pair count must beat the highest run at the lower count by more than 5% (suggested).

- **echo-pydanticai grows 1 to 2 to 4** (+14%, then +9%; one run each).
- **echo-langgraph and echo-typescript grow 1 to 2** (+29%, +24%). They are **flat from 2 to 4**.
- **echo-python does not grow robustly.** Its 1-pair runs on the quiet host were 75.5 and 50.8, which overlap the 2-pair runs (74.7, 82.0). 4 pairs (82.1) is within 1% of the best 2-pair run.
- **4 pairs do not fit this VM without starving it.** In every 4-pair run, 6 to 13% of the chassis and workload `docker stats` samples are above the 1.0-vCPU cap (python 16/128, max 2.07; pydanticai 8/128; langgraph 9/120; typescript 11/128). In 1-pair runs there are none, and in 2-pair runs 1 to 2 of 64. CPU per call triples to quadruples from 1 to 4 pairs, while the work per call is the same. So each added pair makes every pair slower. The fake model server (at most 1.2 of its 2 vCPU), Traefik, Valkey, and the client are not the limit (see the 128-user check above).
- Recorded per step in `tests/test_load_results.py`: 6 pass, and 4 are strict xfails with these numbers (echo-python 1 to 2 and 2 to 4, echo-langgraph 2 to 4, echo-typescript 2 to 4). The 4-pair runs are flagged in `results.json` (`vm_starving_note`). A larger or dedicated VM, or the kind cluster, is needed to show 4 pairs.

## Quiet pass: the sidecar hop (1 user, 60 s each)

```console
hop-sidecar: 44.18 rps, p50 21.0 ms, p95 32.0 ms, failures 0/2615
hop-inprocess: 44.59 rps, p50 20.0 ms, p95 32.0 ms, failures 0/2639
hop_ms: {"p50_ms": 1.0, "p95_ms": 0.0}
```

**The hop is below this method's 1 ms resolution, and it is not isolated:** sidecar minus inprocess is 1.0 ms at p50 and 0.0 ms at p95. That is consistent with ADR-001's suggested 1 to 3 ms, but it is not a measurement of it. Locust reports whole milliseconds, and the two lanes run `echo_python` in two different hosts (the workload's own server, against an in-process call). A chassis-side timer around the outbound A2A call, per request, would measure the hop itself. The chassis has none today. (Corrected; this paragraph first said "inside ADR-001's suggested 1 to 3 ms".) No container was CPU-starved: chassis 0.50 vCPU (sidecar) and 0.88 (inprocess), workload 0.59, so all are below the cap.

What this isolates: the end-to-end cost, through Traefik, of running echo_python behind the localhost A2A hop in its own container, against running it in the chassis process through in-memory A2A. Both are complete calls with no streaming, at 1 user.

What it does not isolate:
- It also includes the difference between the two `echo_python` hosts: the workload's own server, against an in-process call.
- It ignores that the inprocess chassis does both jobs on one CPU.
- It ignores Locust's whole-millisecond resolution (a 1 ms difference is one tick).
- It covers no streaming. Per-delta cost is PoC-2's figure.

A direct measure needs the chassis to log its own outbound hop time per request. That is not available today.

## Quiet pass: the chassis's cost at idle and at 10 RPS, against ADR-001

`rate` scenarios: 1 pair, 10 users at 1 call per second each (`LOAD_RATE_PER_USER=1`), 60 s. The result was 10.14 to 10.15 RPS and 0 failures for every engine. Idle is one `docker stats` sample before the load.

| Engine | Chassis vCPU at 10 RPS, mean (max) | Chassis MiB at 10 RPS | Chassis idle vCPU / MiB | Workload vCPU / MiB at 10 RPS | p50 / p95 ms |
| ------ | ---------------------------------- | --------------------- | ----------------------- | ----------------------------- | ------------ |
| echo-python | 0.115 (0.24) | 161 | 0.14 / 150 | 0.10 / 80 | 110 / 160 |
| echo-pydanticai | 0.123 (0.23) | 161 | 0.13 / 150 | 0.12 / 129 | 92 / 150 |
| echo-langgraph | 0.129 (0.21) | 161 | 0.08 / 150 | 0.12 / 125 | 110 / 170 |
| echo-typescript | 0.105 (0.18) | 155 | 0.14 / 150 | 0.03 / 57 | 34 / 52 |

At saturation the chassis runs 0.58 to 0.94 vCPU per replica and 163 to 182 MiB (table above).

Against ADR-001's sidecar assumptions (suggested: 0.05 to 0.1 vCPU and 128 to 256 MiB reserved; hop 1 to 3 ms):

**ADR-001 Revisit input (the one statement of this note; it supersedes the noisy-section verdict below):**

- **Memory:** 150 MiB idle and 155 to 182 MiB under any load. Inside ADR-001's 128 to 256 MiB.
- **CPU:**
  - At idle: 0.08 to 0.14 vCPU (a single sample each).
  - At 10 RPS: 0.105 to 0.129 vCPU.
  - That is at or just above the top of ADR-001's 0.05 to 0.1.
  - At saturation: about 0.6 to 1.3 vCPU per 100 RPS.
  - Whether this is "far above" the estimate is the epic owner's call. This note sends the CPU figures to the epic owner as a Revisit input; it does not decide it.
- **Hop:** below this method's 1 ms resolution and not isolated (see the hop section). Not a measured input yet.

Earlier wording of this list, kept for the record (superseded by the statement above):

- (superseded) **Memory:** 150 MiB idle and 155 to 182 MiB under any load. Inside 128 to 256 MiB. Not triggered.

- (superseded) **CPU:** at 10 RPS the chassis uses 0.105 to 0.129 vCPU, just above the top of the 0.05 to 0.1 range. Idle samples are 0.08 to 0.14, a single sample each, taken soon after start. A 0.1 vCPU reservation carries about 8 to 10 RPS per replica with these fake-model calls. Past that, the chassis needs about 1 vCPU per 100 RPS (1p: 0.6 vCPU per 100 RPS with TypeScript, 0.8 to 1.3 with the Python engines). Above the range but not "far above" at a moderate rate. So this is a sizing input for the reservation (reserve by expected RPS), not a trigger.
- (superseded) **Latency:** the hop is about 1 ms. Not triggered.
- (superseded) **Revisit rule "the sidecar costs too much": not triggered** by these figures. The CPU reservation should be stated per RPS, not as a flat 0.05 to 0.1.

## Idempotency cost, measured back to back (2026-10-01 21:10 to 21:27 UTC, after the review)

This supersedes the "not a finding" below. The review was right that 93.9 against 50.8 and 75.5 RPS was outside the 5% margin. The cause was drift between runs taken 30 minutes apart. When keyed and unkeyed runs alternate back to back on the same stack, the gap almost disappears.

Method: `load/idem_cost.py`, on the images of 21:05 UTC. One pair of echo-python, with the stack not restarted between runs. Each alternation is a keyed run (a fresh `Idempotency-Key` per call), then an unkeyed run (`LOAD_NO_KEY=1`), 60 s each, 0 failures in all 16 runs. Chassis CPU per call is mean `docker stats` vCPU divided by RPS. Valkey commands per call is the change in `total_commands_processed` divided by requests. Locust reports latency above 100 ms to 2 significant figures, so 10 ms steps. Raw: `notes/load/idem-cost.json`, `notes/load/idem-raw/`.

```console
$ deploy/compose/scale.sh up echo-python 1
$ uv run python pocs/poc-04-stateless-scalable/load/idem_cost.py
rate10-1-key: 10.15 rps, p50 110.0 ms, p95 170.0 ms, chassis 0.1086 vCPU = 10.7 ms/call, valkey 4.16 cmd/call, failures 0/600
rate10-1-nokey: 10.15 rps, p50 110.0 ms, p95 150.0 ms, chassis 0.1375 vCPU = 13.54 ms/call, valkey 0.1 cmd/call, failures 0/600
rate10-2-key: 10.14 rps, p50 110.0 ms, p95 170.0 ms, chassis 0.141 vCPU = 13.9 ms/call, valkey 4.12 cmd/call, failures 0/600
rate10-2-nokey: 10.14 rps, p50 110.0 ms, p95 160.0 ms, chassis 0.0895 vCPU = 8.83 ms/call, valkey 0.1 cmd/call, failures 0/600
rate10-3-key: 10.15 rps, p50 110.0 ms, p95 170.0 ms, chassis 0.1384 vCPU = 13.63 ms/call, valkey 4.12 cmd/call, failures 0/600
rate10-3-nokey: 10.15 rps, p50 110.0 ms, p95 180.0 ms, chassis 0.1385 vCPU = 13.65 ms/call, valkey 0.1 cmd/call, failures 0/600
rate30-1-key: 29.99 rps, p50 320.0 ms, p95 390.0 ms, chassis 0.2701 vCPU = 9.01 ms/call, valkey 4.12 cmd/call, failures 0/1780
rate30-1-nokey: 29.84 rps, p50 320.0 ms, p95 420.0 ms, chassis 0.2308 vCPU = 7.73 ms/call, valkey 0.04 cmd/call, failures 0/1771
rate30-2-key: 29.99 rps, p50 380.0 ms, p95 700.0 ms, chassis 0.4491 vCPU = 14.97 ms/call, valkey 4.11 cmd/call, failures 0/1778
rate30-2-nokey: 29.92 rps, p50 380.0 ms, p95 630.0 ms, chassis 0.3032 vCPU = 10.13 ms/call, valkey 0.03 cmd/call, failures 0/1776
rate30-3-key: 30.35 rps, p50 340.0 ms, p95 630.0 ms, chassis 0.2796 vCPU = 9.21 ms/call, valkey 4.12 cmd/call, failures 0/1770
rate30-3-nokey: 30.36 rps, p50 340.0 ms, p95 520.0 ms, chassis 0.2655 vCPU = 8.74 ms/call, valkey 0.03 cmd/call, failures 0/1800
sat-1-key: 52.53 rps, p50 1200.0 ms, p95 1600.0 ms, chassis 0.7041 vCPU = 13.4 ms/call, valkey 4.25 cmd/call, failures 0/3098
sat-1-nokey: 55.47 rps, p50 1100.0 ms, p95 1400.0 ms, chassis 0.6195 vCPU = 11.17 ms/call, valkey 0.02 cmd/call, failures 0/3349
sat-2-key: 55.86 rps, p50 1100.0 ms, p95 1500.0 ms, chassis 0.6592 vCPU = 11.8 ms/call, valkey 4.05 cmd/call, failures 0/3378
sat-2-nokey: 55.95 rps, p50 1100.0 ms, p95 1400.0 ms, chassis 0.6577 vCPU = 11.76 ms/call, valkey 0.02 cmd/call, failures 0/3286
```

Medians, with the [min to max] over the alternations:

| Point | Keyed p50 / p95 ms | Unkeyed p50 / p95 ms | Keyed chassis ms/call | Unkeyed chassis ms/call | RPS keyed / unkeyed |
| ----- | ------------------ | -------------------- | --------------------- | ----------------------- | ------------------- |
| 10 RPS (3 alternations) | 110 [110] / 170 [170] | 110 [110] / 160 [150 to 180] | 13.6 [10.7 to 13.9] | 13.5 [8.8 to 13.7] | 10.15 / 10.15 |
| 30 RPS (3 alternations) | 340 [320 to 380] / 630 [390 to 700] | 340 [320 to 380] / 520 [420 to 630] | 9.2 [9.0 to 15.0] | 8.7 [7.7 to 10.1] | 30.0 / 29.9 |
| Saturated, 64 users (2 alternations) | 1150 [1100 to 1200] / 1550 [1500 to 1600] | 1100 [1100] / 1400 [1400] | 12.6 [11.8 to 13.4] | 11.5 [11.2 to 11.8] | 54.2 [52.5 to 55.9] / 55.7 [55.5 to 56.0] |

The cost of idempotency, with its spread:

- **Below saturation, 10 and 30 RPS: no cost the method can see.** p50 is identical in every alternation. The keyed and unkeyed p95 and CPU-per-call ranges overlap. The spread (for example 8.8 to 13.7 ms per call, unkeyed, at 10 RPS) swallows any difference of a millisecond or two.
- **At saturation: about 3% of throughput** (54.2 against 55.7 RPS; ranges 52.5 to 55.9 and 55.5 to 56.0, so they touch). That is **about 1 ms of chassis CPU per call** (12.6 against 11.5 ms; ranges 11.8 to 13.4 and 11.2 to 11.8, which barely separate). It also adds **about 50 ms at p50 and 150 ms at p95** (keyed p95 1500 and 1600, against 1400 and 1400). This is a small, consistent cost, near the edge of the spread, from only 2 alternations.
- **Valkey work matches the design.** Keyed calls run 4.05 to 4.25 Valkey commands per call, against 0.02 to 0.10 unkeyed (health checks and background only). That is consistent with about 3 round trips per call plus a renew or a release. These are commands counted, not round trips timed: a pipelined pair would count 2. Valkey itself used under 0.04 vCPU.
- **The earlier gap was drift, not idempotency.** The 93.9 against 50.8 RPS figures were runs 30 minutes apart. Back to back, the same scenario gives 55.7 against 54.2.

## Quiet pass: idempotency cost (superseded by the back-to-back measurement above)

```console
echo-python-1p (keyed):        50.79 rps, p50 1200 ms, p95 1600 ms     (18:34)
echo-python-1p-nokey:          93.93 rps, p50  680 ms, p95  840 ms     (19:05)
cost_ms: {"p50_ms": 520.0, "p95_ms": 760.0}
```

**Not a finding.** The keyed scenario itself gave 75.48 RPS (p50 840 ms) in the shape check at 18:29, so the gap is inside the run-to-run spread at saturation. A fair figure needs keyed and unkeyed runs at a fixed rate, back to back. Valkey stayed under 0.04 vCPU throughout.

## Quiet pass: files and commands

- `notes/load/results.json`: the quiet pass. Per scenario `ok_rps`, `quiet_runs_ok_rps`, latency, and per-role CPU and MiB with idle figures. Also `hop`, `rate`, `idempotency`, and `vm_starving_note`.
- `notes/load/passes/quiet/`: `results.json`, `raw/` (Locust CSVs and `docker stats` per scenario), `sanity-echo-python.json`, `sanity-raw/`, and `background-docker-stats.txt`.
- Commands: `uptime`; `docker stats --no-stream` (background); `run_matrix.py --engine echo-python --pairs 1 --pairs 2 --only main --duration-s 60 --results .../sanity.json`; the 128-user Locust check; `load/run_quiet.sh`.

## Earlier noisy passes (16:39 to 18:20 UTC, other agents building on the same host): superseded

These three passes and the exploratory runs ran at 30 s per scenario, with the Traefik health check at 1 s / 1 s and the hop at 8 users. Single runs varied up to 3x. They are kept as the record of why the quiet rerun was needed, not as results. Their medians are in `notes/load/passes/noisy-median-results.json`, and each pass in `passes/0` to `passes/3`.

### Host and images

- macOS 26.6.2 arm64 (`platform` in `results.json`), 14 CPUs. Docker Desktop VM: 14 CPUs, 7,935 MiB, shared with 17 unrelated running containers (`paligo-*`, `opensearch-*`; together about 12% of one CPU at idle, `docker stats` 16:51 UTC).
- Other agents were building and running tests on the same Mac during the whole run. Locust runs on the Mac, not in the VM.
- Images: `deploy/compose/scale.sh build`, 2026-10-01 16:22:01 to 16:24:57 UTC. They hold the working tree of that moment.
- Per container (Compose, suggested): chassis and workload 1 CPU and 512 MiB each, fake model server 2 CPUs, Traefik 2 CPUs.
- Load: Locust 2.46.6, 4 processes, 64 users (8 for the hop), native `POST /v1/run` complete mode with a fresh `Idempotency-Key` per call. **Duration 30 s per scenario after a 10 s warm-up, not the suggested 60 s**, to fit three passes in the time.

### Exploratory single runs (pass 0): too noisy to use alone

```console
$ uv run python pocs/poc-04-stateless-scalable/load/run_matrix.py --engine echo-python --only main --duration-s 30
echo-python-1p: 14.35 rps, p50 3800 ms, p95 5600 ms, failures 0/416
echo-python-2p: failed (scenario error; the message was cut by the output tail)
echo-python-4p: 61.7 rps, p50 980.0 ms, p95 1900.0 ms, failures 0/1796
$ uv run python pocs/poc-04-stateless-scalable/load/run_matrix.py --engine echo-python --pairs 2 --only main --duration-s 30
echo-python-2p: 84.52 rps, p50 720.0 ms, p95 1100.0 ms, failures 0/2452
$ uv run python pocs/poc-04-stateless-scalable/load/run_matrix.py --engine echo-python --pairs 1 --pairs 4 --only main --duration-s 30
echo-python-1p: 22.39 rps, p50 1700.0 ms, p95 5400.0 ms, failures 0/589
echo-python-4p: 14.31 rps, p50 3900.0 ms, p95 7900.0 ms, failures 0/418
```

The same scenario moved 3x to 4x between runs (4 pairs: 61.7, then 14.3 RPS). In the slow 4-pair run, `docker stats` showed chassis and workload containers at a mean of 1.5 vCPU and peaks of 4.5 vCPU, above their 1-CPU cap. A capped container cannot use that, so the samples themselves are distorted: the VM was short of CPU time, not the stack. Raw files: `notes/load/passes/0/`. Because of this, the matrix was rerun as three full passes and each scenario is reported as the median pass (`load/run_passes.sh`, `load/median.py`).

### Three passes

```console
$ pocs/poc-04-stateless-scalable/load/run_passes.sh 3 30      # 16:52:57 to 18:20:43 UTC
$ uv run python pocs/poc-04-stateless-scalable/load/median.py
```

Locust exits 1 when any request fails, and `run_matrix.py` then dropped the whole scenario. 21 of 45 scenarios were dropped this way. `run_matrix.py` now passes `--exit-code-on-error 0` and records `ok_rps` (successful calls per second). `median.py` rebuilt each dropped scenario from its raw Locust CSV, using `run_matrix`'s own reader. It ranks passes by `ok_rps`, so fast failures do not count as throughput. A rebuilt run has no load-phase `docker stats`, because the sampler's rows were written only on success. Three scenarios (pass 1 echo-python 2p, pass 2 echo-python 1p and 2p) failed during the warm-up and have no data at all.

Failed requests per scenario, from `passes/<n>/raw/*_failures.csv`: all were `HTTP 503` or `HTTP 502` from Traefik. Most runs had 0 to 4. A few had bursts: pass 1 echo-python 1p had 3,695 503s within 5 s, pass 2 echo-langgraph 1p had 2,168 within 1 s, and pass 1 echo-pydanticai 2p had 89 of 89. That is Traefik with no healthy server behind it. The suspected cause is the `/ready` health check (suggested: 1 s interval, 1 s timeout, `traefik/poc04.yaml`) timing out on a chassis that is at its CPU cap. A repeat (`scale.sh up echo-python 1`, 64 users, 40 s at 18:22) gave 3,063 calls with 0 failures, so this is **not confirmed**. Next step: log the health check at INFO, or raise its timeout to 3 s (suggested), and rerun.

### Throughput per engine and pair count (exit criterion 4)

`ok_rps` per pass (failed/total requests in brackets); the median pass is in bold. 64 users, 30 s.

| Engine | Pairs | Pass 1 | Pass 2 | Pass 3 | Median ok RPS | p50 / p95 ms (median pass) |
| ------ | ----- | ------ | ------ | ------ | ------------- | -------------------------- |
| echo-python | 1 | 7.7 (3695/3935) | no data | 31.3 (0) | **7.7** | 43 / 6000 |
| echo-python | 2 | no data | no data | 28.7 (0) | **28.7** | 2000 / 4200 |
| echo-python | 4 | 17.9 (94) | 22.4 (2) | 26.6 (2) | **22.4** | 2300 / 6000 |
| echo-pydanticai | 1 | 8.2 (7) | 29.1 (0) | 33.4 (0) | **29.1** | 2000 / 2900 |
| echo-pydanticai | 2 | 0.0 (89/89) | 36.1 (0) | 37.6 (0) | **36.1** | 1700 / 3000 |
| echo-pydanticai | 4 | 11.8 (1) | 26.0 (0) | 18.2 (0) | **18.2** | 3200 / 5700 |
| echo-langgraph | 1 | 11.2 (154) | 26.2 (2168) | 34.1 (0) | **26.2** | 10 / 2700 |
| echo-langgraph | 2 | 23.0 (0) | 36.5 (0) | 56.4 (0) | **36.5** | 1700 / 3400 |
| echo-langgraph | 4 | 14.7 (1) | 20.2 (0) | 69.3 (0) | **20.2** | 2800 / 5200 |
| echo-typescript | 1 | 93.6 (0) | 90.1 (0) | 151.4 (0) | **93.6** | 670 / 1200 |
| echo-typescript | 2 | 108.0 (0) | 75.4 (0) | 198.9 (0) | **108.0** | 560 / 1300 |
| echo-typescript | 4 | 88.7 (4) | 95.7 (2) | 263.0 (1) | **95.7** | 490 / 1800 |

Before the passes (exploratory, pass 0), echo-python gave 14.4 and 22.4 RPS at 1 pair, 84.5 at 2 pairs, and 61.7 and 14.3 at 4 pairs.

**Criterion 4 is not shown by this run.** No engine's median grows from 1 to 2 to 4 pairs. Pass 3 (18:00 to 18:20 UTC, the quietest window) grows cleanly for echo-langgraph (34 to 56 to 69) and echo-typescript (151 to 199 to 263), but not for echo-python or echo-pydanticai. Nothing saturated for every engine at once. The fake model server stayed between 0.12 and 0.67 vCPU of its 2, and Traefik at 0.02 to 0.12. Within a pass the numbers move together: pass 3 is higher for everything. That points at the shared host, not the stack. A capped container cannot really use more than 1 vCPU, yet `docker stats` showed chassis and workload containers at 1.5 vCPU mean (4.5 peak) in slow runs. So the VM was starved of CPU time, and the samples were distorted with it. The 4-pair runs need 8 one-CPU containers plus the fake model server and Traefik. They are the most exposed to that, and they are the ones that drop. `tests/test_load_results.py` records this per engine as a strict xfail with these numbers; it never invents a passing number. A rerun on a quiet host (no other agents, `paligo-*` stopped by their owner, 60 s per scenario) is the fix.

### The chassis's cost next to ADR-001

From `docker stats` every 2 s, per chassis replica; the median-pass and pass 3 records in `results.json` and `passes/3/results.json`.

| Figure | Measured | ADR-001 (suggested) |
| ------ | -------- | ------------------- |
| Chassis memory, idle | 150 to 153 MiB | 128 to 256 MiB |
| Chassis memory, under load | 163 to 176 MiB (mean per replica) | 128 to 256 MiB |
| Chassis CPU, idle | 0.01 to 0.39 vCPU (mostly 0.12 to 0.20, a single sample after start) | 0.05 to 0.1 vCPU reserved |
| Chassis CPU, under load | 0.55 to 0.83 vCPU mean per replica (peaks at the 1.0 cap) | 0.05 to 0.1 vCPU reserved |
| Chassis CPU per call | about 5 ms with echo-typescript (0.53 vCPU per 100 RPS, pass 3 1p); 16 to 20 ms with the Python engines (1.6 to 2.0 vCPU per 100 RPS) | not given |
| Sidecar hop p50 / p95 | not measurable here: sidecar minus inprocess came out **negative**, -50 / -100 ms (passes: -50/-100, -70/-170, -20/-60) | 1 to 3 ms |
| Workload memory | 66 to 77 MiB (TypeScript), 120 to 160 MiB (Python) | not given |

The Python engines cost the chassis more per call, because those workloads call back into the chassis's model proxy and MCP endpoint on `127.0.0.1:8090` (`POST /mcp` in the chassis log). The TypeScript workload has no tool client.

Why the hop is negative: at 8 users the chassis is near its 1-CPU cap. In the inprocess lane, `echo_python` runs inside that same 1-CPU container (pass 3: chassis 0.79 vCPU, 46.7 RPS, p50 150 ms). In the sidecar lane, the chassis and the workload each have their own CPU (pass 3: chassis 0.52, workload 0.69, 55.9 RPS, p50 130 ms). The comparison measures CPU caps, not the hop. A real hop figure needs an unloaded run (1 user), or equal total CPU in both lanes. The A2A hop overhead itself is PoC-2's measurement.

**ADR-001 Revisit rule, "the sidecar costs too much" (SUPERSEDED: noisy host; see the one statement in the quiet pass's ADR-001 section above):**

- Memory: not triggered. 150 to 176 MiB is inside 128 to 256 MiB.
- (superseded) CPU: triggered, if the reservation must carry load. 0.05 to 0.1 vCPU covers about 1 to 2 RPS per replica with the Python engines (16 to 20 ms per call) and 10 to 20 RPS with TypeScript (5 ms per call). These are fake-model calls with no model wait. Each call is mostly chassis work: envelope, Valkey idempotency claim and store, the A2A hop, the proxy. Idle is also at or above the top of the range. Confirm on a quiet host before acting. The per-call figure was consistent across passes (1.6 to 2.0 and 0.5 to 0.9 vCPU per 100 RPS at 1 to 2 pairs), unlike the RPS.
- Latency: not measured (see the hop above).

### Idempotency cost

One pass only (pass 2 had no keyed echo-python 1p result to compare against). Pass 3: keyed echo-python 1p gave 31.3 RPS, p50 1,900 ms; with `LOAD_NO_KEY=1`, 60.2 RPS, p50 1,000 ms. So `cost_ms` is p50 900 and p95 1,700. That is the same noise order as the passes themselves, so it is **not a finding** until rerun.

### Files

- `notes/load/passes/noisy-median-results.json`: per scenario, the median pass (full record), `passes_ok_rps`, `passes_failures`, and `median_pass`; `hop` and `idempotency` with every pass.
- `notes/load/passes/<1|2|3>/results.json` and `raw/`: each pass as `run_matrix.py` wrote it. `passes/0/`: the exploratory runs.
- Scripts: `load/run_passes.sh` (new), `load/median.py` (new), `load/run_matrix.py` (keeps runs with failures, `ok_rps`).

## Shared bottleneck check, 2026-10-01 19:00 to 19:17 UTC (quiet host)

Corrected time: it was first given as 21:00 to 21:17, which was local time (UTC+2) from `uptime`.

Hypothesis (from the coordinator): every engine tops out near the same number of model calls per second, so one fake model server (one uvicorn process, so at most about 1 core of useful work) is a shared limit, and adding pairs cannot help. Tested, not assumed.

**(a) Model server CPU in the existing runs.** Mean (max) vCPU of `fake-model-server` in the saturated quiet-pass runs:

| Engine | 1 pair | 2 pairs | 4 pairs (starved) |
| ------ | ------ | ------- | ----------------- |
| echo-python | 0.12 (0.29); 0.13 (0.20) in the shape check | 0.18 (0.40); 0.21 (0.38) | 0.32 (0.76) |
| echo-pydanticai | 0.12 (0.28) | 0.22 (0.48) | 0.39 (1.09) |
| echo-langgraph | 0.14 (0.32) | 0.19 (0.47) | 0.37 (0.79) |
| echo-typescript | 0.20 (0.35) | 0.34 (0.59) | 0.55 (1.23) |

The model server is nowhere near 1.0 at 1 or 2 pairs. The 1.1 to 1.2 peaks are 2 s samples in the starved 4-pair runs only.

**(b) More model capacity.** `docker-compose.scale.yaml`: `fake-model-server` gets `scale: ${MODEL_REPLICAS:-1}`, so with `MODEL_REPLICAS=2` two model servers sit behind the one service name. `scale.sh` documents the variable. Hardening is unchanged on both: `docker inspect` gives `ro=true cap=[ALL]` for `poc04-fake-model-server-1` and `-2`. Inside `poc04-chassis-1-1`, `getaddrinfo("fake-model-server")` returns `['172.26.0.4', '172.26.0.7']`. `tests/test_read_only_compose.py`: 24 passed.

**(c) Rerun, 2 model servers, 60 s, 64 users** (`uptime` before: load 2.76):

```console
$ MODEL_REPLICAS=2 uv run python pocs/poc-04-stateless-scalable/load/run_matrix.py --engine <e> --pairs 1 --pairs 2 --only main --duration-s 60 --results .../passes/model2/results.json
echo-python-1p: 91.09 rps, p50 700.0 ms, p95 870.0 ms, failures 0/5486
echo-python-2p: 59.85 rps, p50 980.0 ms, p95 1900.0 ms, failures 0/3530
echo-typescript-1p: 74.71 rps, p50 790.0 ms, p95 1400.0 ms, failures 0/4510
echo-typescript-2p: 138.52 rps, p50 430.0 ms, p95 1100.0 ms, failures 2/8351
```

| Engine, pairs | ok RPS | Model servers: mean (max) vCPU each | Chassis mean (max) | Workload mean (max) |
| ------------- | ------ | ----------------------------------- | ------------------ | ------------------- |
| echo-python 1 | 91.1 | 0.07 (0.17) | 0.63 (0.78) | 0.79 (0.96) |
| echo-python 2 | 59.9 | 0.16 (0.61) | 0.70 (1.12) | 0.81 (1.04) |
| echo-typescript 1 | 74.7 | 0.19 (0.50) | 0.98 (1.12) | 0.28 (0.52) |
| echo-typescript 2 | 138.5 | 0.28 (0.65) | 0.92 (1.14) | 0.28 (0.55) |

**Verdict: the hypothesis is not supported.**

- With twice the model capacity, echo-python's 2 pairs did *worse* than 1 pair (59.9 against 91.1).
- echo-typescript at 1 pair *halved* (74.7, against 157.4 with one model server), with its chassis at the same 0.98 vCPU. Its CPU per call doubled, from about 6 to about 13 ms.
- The model servers stayed under 0.3 vCPU mean each.
- What moves throughput is the CPU each chassis and workload spends per call. That changes up to 2x between runs of the same scenario on this Docker Desktop VM, whatever the model capacity.
- Under the gate test's rule (2 pairs beats every 1-pair run by more than 5%), neither engine passes, so echo-pydanticai and echo-langgraph were not rerun. 4 pairs was not tried.
- The xfails in `tests/test_load_results.py` stay as they were.
- `results.json`: the main entries are marked `stack_shape: shared model server (1 replica)`. This check is under `shared_bottleneck_check`, marked `MODEL_REPLICAS=2`.
- Raw files: `notes/load/passes/model2/`.

Still open: why CPU per call moves 2x between runs on the same idle-ish VM. It could be vCPU placement on efficiency cores, or CFS quota throttling at `cpus: 1.0`; neither is measured. A test on Linux, or in kind with pinned CPU, would settle whether this is the host or the stack.
