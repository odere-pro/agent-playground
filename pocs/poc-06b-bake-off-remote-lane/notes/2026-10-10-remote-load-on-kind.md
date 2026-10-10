# Remote engines under concurrent load on kind (2026-10-10)

Status: the test exists; the numbers are not in yet. They come from the next `poc06-kind.yml` run. Nothing below is a measurement.

## Why

Exit criterion 4 asks that every supported engine passes the contract, load, and hostile suites in its lane. The PoC-4 load matrix runs on Compose, for trusted engines. The four remote engines run only on kind under gVisor, so the plan's other option applies: a load run on kind.

## What runs, and where

- Test: `pocs/poc-06b-bake-off-remote-lane/tests/test_poc06b_kind_load.py`, in `KIND_TESTS` in `deploy/kind/poc06/run.sh`, so `run.sh test` runs it.
- Cluster: `kind-poc05`, PoC-6 engines up (`deploy/kind/poc06/run.sh up`). CI: `.github/workflows/poc06-kind.yml`.
- Engines (the remote lane): `echo-smolagents`, `echo-claude-agent`, `echo-typescript`, `kagent-adk`.
- Each engine gets 2 rounds of N concurrent smoke runs through its own chassis (`POST /v1/run`, `stream: true`). Round 1 is cold, round 2 is warm. Both count.
- N (`suggested:`): 8 for smolagents, TypeScript, and kagent-adk. 3 for Claude: each run starts a `claude` CLI of 175 to 240 MB resident, and the pod limit is 1Gi.
- Pass: every run ends `end{status: ok}`, and one engine finishes inside 90 s (`suggested:`).
- The model is the fake model server. This tests the chassis, the lane, and the sandbox under concurrency. It says nothing about model speed.
- Latency is the wall time of one run from the test, through `kubectl exec` into the chassis container. The exec start is a fixed cost in every number.

## Command

```
deploy/kind/poc06/run.sh test
```

To read the numbers, take the lines from the CI log:

```
grep -E '^LOAD engine=' <log>
```

Line format: `LOAD engine=<name> lane=<lane> n=<runs> ok=<k> err=<e> p50_ms=<x> p95_ms=<y> wall_s=<z>`.

## Numbers

Head `978c2ea`, two runs of `poc06-kind.yml` on GitHub-hosted runners (4 CPUs). Copied from the `LOAD engine=` lines in the job logs. `n` is both rounds together.

Run 38064809801 (push), job 114250189940:

| Engine | n | ok | err | p50 ms | p95 ms | wall s |
| ------ | - | -- | --- | ------ | ------ | ------ |
| echo-smolagents | 16 | 16 | 0 | 5807 | 6196 | 12.3 |
| echo-claude-agent | 6 | 6 | 0 | 4525 | 4699 | 9.3 |
| echo-typescript (remote) | 16 | 16 | 0 | 6038 | 6454 | 12.6 |
| kagent-adk | 16 | 16 | 0 | 5689 | 5991 | 12.0 |

Run 38064813425 (pull_request), job 114250200592:

| Engine | n | ok | err | p50 ms | p95 ms | wall s |
| ------ | - | -- | --- | ------ | ------ | ------ |
| echo-smolagents | 16 | 16 | 0 | 7394 | 7719 | 15.4 |
| echo-claude-agent | 6 | 6 | 0 | 5899 | 5976 | 11.9 |
| echo-typescript (remote) | 16 | 16 | 0 | 7381 | 7622 | 15.2 |
| kagent-adk | 16 | 16 | 0 | 7231 | 7517 | 15.0 |

The same jobs' kind suites: `78 passed, 1 deselected` (the deselected test is the offline `test_nearest_rank_percentile`, which `-m kind` leaves out).

## Reading

- Every row has `err=0`: each remote engine took 8 runs in flight (3 for Claude), twice, under gVisor with the Localhost seccomp profile, and every run ended `end{status: ok}`. The load half of criterion 4 passes.
- The latency is not engine speed. Each run starts a `kubectl exec` into the chassis container, 8 at once on a 4-CPU runner that also runs the whole kind cluster, and the model is the fake. That is why the four engines land within about 0.5 s of each other on one runner, and why the two runners differ by about 1.5 s. Compare engines only within one run.
- Claude ran 3 in flight, not 8, so its lower latency is not a speed-up (module docstring of the test).
- Real-model latency and tokens per engine come from the Mac run (criterion 5).

## Controls

The same engines pass a single smoke run in `tests/test_poc06b_kind_engines.py`, so a failure here is about concurrency, not a broken pod.
