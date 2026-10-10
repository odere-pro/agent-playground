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

## Numbers (fill from the next CI run)

Run id: _pending_. Head: _pending_. Runner: GitHub-hosted, 4 CPUs.

| Engine | n | ok | err | p50 ms | p95 ms | wall s |
| ------ | - | -- | --- | ------ | ------ | ------ |
| echo-smolagents | _pending_ | | | | | |
| echo-claude-agent | _pending_ | | | | | |
| echo-typescript (remote) | _pending_ | | | | | |
| kagent-adk | _pending_ | | | | | |

## Reading

Pending the run. The criterion's load half passes if every row has `err=0`. A row with errors stays in this table.

## Controls

The same engines pass a single smoke run in `tests/test_poc06b_kind_engines.py`, so a failure here is about concurrency, not a broken pod.
