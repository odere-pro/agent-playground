# 122 X-5: the load matrix for every engine on Linux runners (2026-11-08)

## Why

[122 X-5](../../../docs/planning/issues/122-X-5-load-tests.md) reruns PoC-4's throughput test (PoC-4 exit criterion 4, flagged on 2026-10-08) on a Linux host, for every engine at 1, 2, and 4 pairs, and reads the chassis sidecar's cost next to ADR-001's estimates. The product plan's phase A asks for plain evidence on every PoC-1 to PoC-5 flag. The handover names a GitHub Actions Linux runner as a Linux host that counts. This note records what the runner shows and what it cannot show, box by box.

## Setup

- Commit `07bdf4c` on `poc-06/x5-linux-load`, clean checkout. The commit adds `engines` and `only` inputs to `.github/workflows/poc06-load.yml`, so one dispatch runs one engine pair; the matrix itself is unchanged.
- Three dispatches of `poc06-load.yml`, in parallel, each on its own `ubuntu-latest` runner:

| Run | Inputs | Time |
| --- | ------ | ---- |
| [42528403157](https://github.com/odere-pro/agent-playground/actions/runs/42528403157) | `echo-openai-agents,echo-typescript`; `main,rate` | 15 m 35 s |
| [42528406090](https://github.com/odere-pro/agent-playground/actions/runs/42528406090) | `echo-python,echo-pydanticai`; `main,rate,hop` | 19 m 10 s |
| [42528409028](https://github.com/odere-pro/agent-playground/actions/runs/42528409028) | `echo-langgraph`; `main,rate` | 8 m 39 s |

- Machine, each run: `Linux-7.0.0-1019-azure-x86_64-with-glibc2.43`, 4 CPUs, 15,982 MiB, Docker 29.4.2, Compose v5.1.3, no other container (`other_running_containers: 0`). A shared runner: no pinned CPUs, and the 4 vCPUs are a slice of a shared Azure host.
- Compose project `poc04`, `deploy/compose/scale.sh` (Traefik, N chassis and workload pairs, Valkey, MinIO, the fake model server). Each chassis and each workload is capped at 1.0 vCPU.
- Load: Locust 2.46.6, 4 processes. `main`: 64 users at 1, 2, and 4 pairs. `rate`: 10 users at 1 call per second each, 1 pair. `hop`: 1 user, echo-python at 1 pair, the sidecar lane against the inprocess lane. 10 s warm-up (not kept), then 60 s measured, one scenario at a time, `scale.sh down` after each.
- Raw results: [load/2026-11-08-x5-openai-ts.json](load/2026-11-08-x5-openai-ts.json), [load/2026-11-08-x5-py-pyd.json](load/2026-11-08-x5-py-pyd.json), [load/2026-11-08-x5-langgraph.json](load/2026-11-08-x5-langgraph.json): the `results.json` each run wrote, copied from the job log (the "Results JSON" step) with a `source` key added. They hold the mean and the max of CPU and memory per role; the p95 (`vcpu_p95`, `mib_p95`) is recorded only by runs after this commit.

## Command

```console
$ gh workflow run poc06-load.yml --ref poc-06/x5-linux-load \
    -f engines=echo-openai-agents,echo-typescript -f only=main,rate
$ gh workflow run poc06-load.yml --ref poc-06/x5-linux-load \
    -f engines=echo-python,echo-pydanticai -f only=main,rate,hop
$ gh workflow run poc06-load.yml --ref poc-06/x5-linux-load \
    -f engines=echo-langgraph -f only=main,rate
```

Each job runs `deploy/compose/scale.sh build`, then `uv run python -m load_tools.run_matrix --engine ... --only ... --results "$RUNNER_TEMP/poc06-load.json"`.

## Output

The per-scenario lines of `run_matrix.py` from the three job logs, in engine order. Nothing is trimmed but the stack's start and stop lines and the Locust tables. Every job exited 0 ("every scenario ok").

```console
echo-python-1p: 25.64 rps, p50 2400.0 ms, p95 3000.0 ms, failures 0/1503
echo-python-2p: 34.07 rps, p50 1900.0 ms, p95 2700.0 ms, failures 0/2015
echo-python-4p: 31.71 rps, p50 1900.0 ms, p95 3100.0 ms, failures 1/1875
echo-pydanticai-1p: 28.3 rps, p50 2200.0 ms, p95 2800.0 ms, failures 0/1665
echo-pydanticai-2p: 39.12 rps, p50 1600.0 ms, p95 2200.0 ms, failures 0/2309
echo-pydanticai-4p: 36.23 rps, p50 1700.0 ms, p95 3200.0 ms, failures 0/2130
echo-langgraph-1p: 28.47 rps, p50 2200.0 ms, p95 2800.0 ms, failures 0/1669
echo-langgraph-2p: 37.92 rps, p50 1700.0 ms, p95 2500.0 ms, failures 0/2237
echo-langgraph-4p: 31.4 rps, p50 1900.0 ms, p95 3800.0 ms, failures 1/1852
echo-openai-agents-1p: 34.79 rps, p50 1800.0 ms, p95 2400.0 ms, failures 0/2056
echo-openai-agents-2p: 49.01 rps, p50 1200.0 ms, p95 1800.0 ms, failures 0/2901
echo-openai-agents-4p: 44.4 rps, p50 1300.0 ms, p95 2700.0 ms, failures 0/2620
echo-typescript-1p: 37.38 rps, p50 1700.0 ms, p95 2000.0 ms, failures 0/2202
echo-typescript-2p: 56.71 rps, p50 1100.0 ms, p95 2300.0 ms, failures 1/3347
echo-typescript-4p: 61.42 rps, p50 680.0 ms, p95 3000.0 ms, failures 0/3637
hop-sidecar: 22.21 rps, p50 40.0 ms, p95 81.0 ms, failures 0/1311
hop-inprocess: 20.47 rps, p50 41.0 ms, p95 94.0 ms, failures 0/1208
echo-python-1p-rate10: 10.02 rps, p50 310.0 ms, p95 530.0 ms, failures 0/605
echo-pydanticai-1p-rate10: 10.07 rps, p50 270.0 ms, p95 520.0 ms, failures 0/600
echo-langgraph-1p-rate10: 10.1 rps, p50 290.0 ms, p95 650.0 ms, failures 0/600
echo-openai-agents-1p-rate10: 10.01 rps, p50 210.0 ms, p95 390.0 ms, failures 0/603
echo-typescript-1p-rate10: 10.08 rps, p50 150.0 ms, p95 360.0 ms, failures 0/600
```

The three failures, each from its scenario's Locust error report, all one line:

```console
1                  POST /v1/run (key): 'HTTP 502'
```

### Throughput (ok RPS) and the chassis per replica, `main`

| Engine | 1 pair | 2 pairs | 4 pairs | 1 to 2 | 2 to 4 | Chassis vCPU mean, 1 / 2 / 4 | Chassis vCPU per 100 RPS, all replicas, 1 / 2 / 4 |
| ------ | ------ | ------- | ------- | ------ | ------ | ---------------------------- | ------------------------------------------------- |
| echo-python | 25.6 | 34.1 | 31.7 | +33% | -7% | 0.74 / 0.70 / 0.41 | 2.9 / 4.1 / 5.2 |
| echo-pydanticai | 28.3 | 39.1 | 36.2 | +38% | -7% | 0.67 / 0.60 / 0.36 | 2.4 / 3.1 / 3.9 |
| echo-langgraph | 28.5 | 37.9 | 31.4 | +33% | -17% | 0.70 / 0.68 / 0.38 | 2.5 / 3.6 / 4.9 |
| echo-openai-agents | 34.8 | 49.0 | 44.4 | +41% | -9% | 0.68 / 0.61 / 0.38 | 2.0 / 2.5 / 3.4 |
| echo-typescript | 37.4 | 56.7 | 61.4 | +52% | +8% | 1.00 / 0.85 / 0.59 | 2.7 / 3.0 / 3.8 |

### The chassis at 10 RPS, `rate` (1 pair), mean (max) over the 60 s

| Engine | Chassis vCPU | Chassis MiB | Workload vCPU / MiB | p50 / p95 ms |
| ------ | ------------ | ----------- | ------------------- | ------------ |
| echo-python | 0.35 (0.66) | 165 (174) | 0.30 / 80 | 310 / 530 |
| echo-pydanticai | 0.29 (0.53) | 164 (173) | 0.32 / 128 | 270 / 520 |
| echo-langgraph | 0.35 (0.69) | 164 (174) | 0.33 / 124 | 290 / 650 |
| echo-openai-agents | 0.30 (0.51) | 163 (165) | 0.27 / 122 | 210 / 390 |
| echo-typescript | 0.35 (0.60) | 166 (179) | 0.13 / 67 | 150 / 360 |

Idle, one `docker stats` sample before each `rate` load: chassis 159 to 160 MiB in every run; chassis vCPU 0.004 and 0.010 (echo-pydanticai, echo-python) and 0.25 to 0.27 (the other three), so the single idle CPU sample is noise on this host.

## Reading

**Every engine passes the load suite on Linux.** 22 scenarios ran, 3 of 34,018 `main` calls failed (0.009%), each a single 502, under the 0.1% bar of `poc06-load.yml` and `tests/test_poc06a_criteria.py`. That is the same class of result as PoC-4's quiet pass (5 of 70,709) and the 2026-10-28 CI run (1 of 22,420).

**Throughput grows from 1 to 2 pairs for every engine** (+33% to +52%), the first time echo-python shows it (its PoC-4 1-pair runs overlapped its 2-pair runs). **From 2 to 4 pairs it does not grow** for four engines (-7% to -17%) and grows 8% for echo-typescript, which is above the test's 5% margin but is one run. The cause is the host: eight 1-vCPU containers plus Traefik, Valkey, MinIO, the model server, and four Locust processes share 4 vCPUs. At 4 pairs the chassis's mean CPU per replica falls to 0.36 to 0.59 while its CPU per 100 RPS rises 1.4 to 2 times from 1 pair, so each added pair makes every pair slower. This is PoC-4's "4 pairs starve the VM" finding on a smaller host. So the runner is a Linux host, but not the one 122 X-5 box 1 needs: that host must hold 8 containers at 1 vCPU each plus the stack, about 12 cores, with pinned CPUs. The `RECORDED_EXCEPTIONS` in `pocs/poc-04-stateless-scalable/tests/test_load_results.py` stay, and PoC-4's `results.json` stays the recording of record (the Mac's 1-pair numbers are 2 to 4 times the runner's, so the two hosts are not comparable). Box 1 stays open, with the host as the reason.

**The sidecar's cost (box 2).** At 10 RPS the chassis holds 163 to 166 MiB (max 179), inside ADR-001's 128 to 256 MiB, as on the Mac (155 to 161). Its CPU is 0.29 to 0.35 vCPU mean, about 3 times the Mac's 0.105 to 0.129 at the same 10 RPS, and above twice the top of ADR-001's 0.05 to 0.1 vCPU. The runner's shared vCPU is slower than a Mac performance core and the five engines land within 0.06 vCPU of each other, so the figure is the host's as much as the chassis's. The p95 is not in these results: `run_matrix.py` recorded the mean and the max until this branch added `vcpu_p95` and `mib_p95`, so a p95 needs one more `rate` run. The hop is not measured here either: sidecar minus inprocess came out negative (-1 ms at p50, -13 ms at p95) because the inprocess chassis runs echo_python inside its own 1-vCPU cap (0.999 vCPU during the run, against 0.66 for the sidecar chassis), so the inprocess lane is slower on this host. As PoC-4 found, a chassis-side timer around the outbound A2A call is the only way to isolate the hop; the chassis has none. Box 2 stays open: p95 from a rerun, the hop from a chassis timer.

**The Revisit trigger (box 3).** Under the suggested rule (far above means more than twice the upper estimate), the runner's 10 RPS CPU (0.29 to 0.35 against 0.1) would meet it and the Mac's (0.105 to 0.129) would not. The two hosts disagree, so this note sends both figures to the owner and does not set the chart's requests from either; the dedicated Linux box decides. Box 3 stays open.

**The 502 (box 5).** 3 in 34,018 calls, two at 4 pairs (echo-python, echo-langgraph) and one at 2 pairs (echo-typescript). The job keeps no container logs (`run_matrix.py` takes the stack down after each scenario), so the source, chassis or Traefik, is still not named. The rate is recorded; the source is open.

## What this run leaves open, and where

- The Linux box with about 12 cores and pinned CPUs: the owner-list item (011 section 3). A GitHub larger runner would do; this account has none.
- A chassis-side hop timer: a chassis change, `chassis-architect` to scope (suggested: a span attribute on the connector call, read by `run_matrix.py`).
- One `rate` rerun after this branch merges, for the p95: `gh workflow run poc06-load.yml -f engines=<all five> -f only=rate`, about 3 minutes per engine.
- The 502's source: keep the chassis and Traefik logs of a failed scenario in `run_matrix.py`, then read them (the PoC-6a backlog note already proposes it).

## Rerun 2026-11-09: the sidecar at p95, and the hop from a chassis-side timer (box 2)

Two more runs on 4-vCPU `ubuntu-latest` runners, same stack and load shape as above.

| Run | Ref | Inputs | What it adds |
| --- | --- | ------ | ------------ |
| [42625888817](https://github.com/odere-pro/agent-playground/actions/runs/42625888817) | `main` `74d7f22` | every engine; `rate` | `vcpu_p95` and `mib_p95` per role (`run_matrix` since #116) |
| [42627333030](https://github.com/odere-pro/agent-playground/actions/runs/42627333030) | `poc-06/x5-hop-timer` `9b688f6` | `echo-python`; `hop` | `engine_run`, the chassis's own timer, and `chassis_hop_ms` |

Raw results: [load/2026-11-09-x5-rate-p95.json](load/2026-11-09-x5-rate-p95.json), [load/2026-11-09-x5-hop-chassis-timer.json](load/2026-11-09-x5-hop-chassis-timer.json).

```console
$ gh workflow run poc06-load.yml --ref main \
    -f engines=echo-python,echo-pydanticai,echo-langgraph,echo-openai-agents,echo-typescript -f only=rate
$ gh workflow run poc06-load.yml --ref poc-06/x5-hop-timer -f engines=echo-python -f only=hop
```

### The chassis at 10 RPS, 1 pair, 60 s: mean / p95 / max

```console
echo-python-1p-rate10: 10.1 rps, p50 240.0 ms, p95 460.0 ms, failures 0/600
echo-pydanticai-1p-rate10: 10.02 rps, p50 260.0 ms, p95 440.0 ms, failures 0/605
echo-langgraph-1p-rate10: 10.08 rps, p50 300.0 ms, p95 560.0 ms, failures 0/600
echo-openai-agents-1p-rate10: 10.08 rps, p50 190.0 ms, p95 390.0 ms, failures 0/600
echo-typescript-1p-rate10: 10.09 rps, p50 140.0 ms, p95 340.0 ms, failures 0/600
```

| Engine | Chassis vCPU | Chassis MiB | Workload vCPU p95 / MiB p95 |
| ------ | ------------ | ----------- | --------------------------- |
| echo-python | 0.39 / 0.55 / 0.55 | 165 / 173 / 173 | 0.61 / 88 |
| echo-pydanticai | 0.29 / 0.51 / 0.51 | 164 / 174 / 174 | 0.54 / 138 |
| echo-langgraph | 0.36 / 0.66 / 0.66 | 165 / 174 / 174 | 0.64 / 125 |
| echo-openai-agents | 0.29 / 0.67 / 0.67 | 163 / 170 / 170 | 0.51 / 122 |
| echo-typescript | 0.34 / 0.61 / 0.61 | 167 / 178 / 178 | 0.30 / 79 |

The p95 equals the max in every row. `docker stats --no-stream` takes about 2 s itself, so a 60 s run gives 15 to 30 samples per container, and the nearest-rank p95 of 20 or fewer samples is the top one. Read the p95 column as an upper bound; a longer `rate` run (suggested: 300 s) would separate them.

### The hop, as the chassis sees it (`echo-python`, 1 pair, 1 user, 60 s)

The chassis times its A2A call to the workload (`chassis.engine.run`, both lanes run the same connector) as the histogram `chassis_engine_run_duration_seconds{lane}` on `/metrics`; `run_matrix` reads it before and after the measured run and takes the difference, so Traefik, Locust, and the warm-up are out of it. The mean is exact; p50 and p95 are interpolated inside their bucket (bounds every 5 ms below 50 ms).

```console
hop-sidecar: 25.58 rps, p50 34.0 ms, p95 76.0 ms, failures 0/1510
hop-inprocess: 23.25 rps, p50 36.0 ms, p95 89.0 ms, failures 0/1372
```

| Lane | Runs timed | Engine call mean | p50 | p95 | Chassis vCPU |
| ---- | ---------- | ---------------- | --- | --- | ------------ |
| sidecar | 1,527 | 35.63 ms | 33.56 ms | 67.09 ms | 0.63 |
| inprocess | 1,394 | 39.46 ms | 35.77 ms | 85.65 ms | 0.99 |
| sidecar minus inprocess (`chassis_hop_ms`) | | -3.83 ms | -2.21 ms | -18.56 ms | |

Locust's view of the same two runs (`hop_ms`): -2.0 ms at p50, -13.0 ms at p95.

### Reading

**Box 2.** The sidecar's p95 CPU and memory at the configured rate are recorded: 0.51 to 0.67 vCPU and 170 to 178 MiB at 10 RPS, an upper bound (the sampling above). Memory is inside ADR-001's 128 to 256 MiB; CPU is above its 0.05 to 0.1 vCPU by 5 to 7 times at p95 and 3 times at the mean on this host, where the Mac gave 0.105 to 0.129 at the mean. The hop is now timed in the chassis, at p50 and p95, next to ADR-001's 1 to 3 ms: it reads negative, 2 to 4 ms in favor of the sidecar lane. The timer isolates the call from Traefik and Locust, not from where the workload runs: the inprocess lane runs echo_python on the chassis's own event loop inside its 1-vCPU cap (0.99 vCPU during the run, against 0.63 for the sidecar chassis), so on this host the sidecar lane is the faster one and the hop is below what the comparison can show. Both figures, the p95 cost and the hop, are the box's numbers, with that reading; the cost at the configured rate is the table above. Box 2 is ticked on this evidence.

**Box 3.** Unchanged: the two hosts disagree on CPU (3 times apart at the mean), so the chart's requests stay at 100m and 128Mi and the Revisit call is the owner's, on the dedicated box. Memory agrees across hosts (155 to 178 MiB) and sits inside the estimate.

**What the chassis gains.** `chassis_engine_run_duration_seconds` next to `chassis_run_duration_seconds` gives the chassis's own share of a run's latency, the figure `docs/specs/metrics.md` calls "the stages' overhead"; its status label now follows the run's `error` event (a review finding, fixed in the same PR after this run; the run above had no error, so its numbers stand).
