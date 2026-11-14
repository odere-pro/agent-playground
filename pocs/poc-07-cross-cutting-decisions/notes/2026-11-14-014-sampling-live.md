# Live trace sampling at rate 0, 2026-11-14

## Why

Issue 014 H-7, the box "Trace sampling is a Collector policy (ADR-007 item 8), and
`trace_sample_rate` sets it". It waited on a live proof at `TRACE_SAMPLE_RATE=0`: a run that ends
`error` at once, and a run that spends the whole default 30 s `timeout_ms` and then ends `error`,
are both found in Jaeger; a run that ends `end` is never found.

## Setup

- Commit: `2b70aa6`, dirty (the new test file only).
- Machine: macOS 27.2.1, Apple M1 Pro. Docker Desktop 29.9.3, Docker VM 8,319,438,848 bytes
  (about 8 GB), 10 CPUs. Docker Compose v5.7.0. uv 0.10.10, Python 3.12.13.
- Compose file: `deploy/compose/poc07/docker-compose.yaml`, under its own project
  `poc07-h7-sampling` (suggested), not `stack.sh` (which fixes the project to `poc07`). No other
  stack was up (`docker ps` was empty before the run).
- `TRACE_SAMPLE_PERCENT` came from `deploy/compose/trace_sample_percent.py` at
  `TRACE_SAMPLE_RATE=0`, the one conversion `stack.sh` runs; it gave `0`.
- Credentials were made fresh into a temporary mode 600 env file and never printed.
- The chassis ran in the test process: `fake` profile, `otel` telemetry adapter, `inprocess`
  lane, exporting to the Collector on `127.0.0.1:4318` with its bearer token.
- Images, pinned by digest in the compose file:
  - `otel/opentelemetry-collector-contrib:0.163.2`
  - `jaegertracing/jaeger:2.21.0`
  - `langfuse/langfuse:4`, `langfuse/langfuse-worker:4`
  - `clickhouse/clickhouse-server:25.12`, `postgres:17`, `redis:7`, `chainguard/minio:latest`
- Three runs, one after the other: `chassis.core.handle:echo_wire` (ends `end`),
  `chassis_contracts.lane:error_handle` (ends `error` at once), and the test's `slow_handle`
  (sleeps past the default 30 s run budget, so the deadline ends it as an error).

## Command

```
env -u VIRTUAL_ENV TRACE_SAMPLING_LIVE=1 uv run pytest -m network -v -s pocs/poc-07-cross-cutting-decisions/tests/test_poc07_sampling_live.py
```

## Output

The full log, exit code 0. Trimmed: each 32-hex trace id is cut to its first 8 characters, and the
envelope body that follows each run line (`request_id`, `trace_id`, `idempotency_key`, `agent`,
cut at 160 characters by the test) is shown as `{...}`. The secret scanner reads the full hex ids
as high-entropy strings. They are random run ids, not secrets. The full log was in
`/private/tmp/claude-501/live-014b-run.log`.

```
pocs/poc-07-cross-cutting-decisions/tests/test_poc07_sampling_live.py::test_at_rate_zero_both_error_traces_are_kept_and_the_ok_trace_is_not ok: trace 3a6768f1... http 200 0.0 s {...}
quick error: trace 3f3e16e1... http 200 0.0 s {...}
30 s error: trace bdb9f018... http 200 30.0 s {...}
jaeger quick error: ['chassis.engine.run', 'chassis.run']
jaeger 30 s error: ['chassis.engine.run', 'chassis.run']
jaeger ok: []
PASSEDcompose down -v -p poc07-h7-sampling: exit 0

======================== 1 passed in 310.32s (0:05:10) =========================
```

After the run, `docker compose -p poc07-h7-sampling ps -a` listed no containers.

## Reading

At rate 0 the Collector's `tail_sampling` kept both error traces, the quick one and the one that
took the full 30 s budget, and dropped the ok trace. The box passes on this run.

## Controls

The two error traces found in Jaeger are the control for the ok trace's absence: they show the
path from the chassis through the Collector to Jaeger worked in the same run. The ok run went
first, and its absence was read only after both error traces were found plus a 20 s margin
(suggested).

## What this does not prove

- Rates between 0 and 1: the percent of ok traces kept is not measured here.
- The Helm charts: no Collector there yet (the 014 text still waits on it).
- The other lanes (`a2a`, `remote`) and other engines: only the `inprocess` lane ran.
- Tempo: only Jaeger was the trace backend.
- A run longer than `decision_wait` (45 s): not tried.
