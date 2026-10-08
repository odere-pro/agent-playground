# PoC-4 load scripts

Load and drill scripts for the Compose scale stack (`deploy/compose/scale.sh`, project `poc04`, Traefik on `127.0.0.1:18080`). Design: `docs/plans/2026-10-01-poc-04-stateless-scalable.md`, sections 6, 9, and 10. They feed exit criteria 3 (killed replica), 4 (throughput grows), 6 (stopping a pair fails nothing), and 11 (sidecar cost).

Build the images first: `deploy/compose/scale.sh build`. The scripts only touch containers of project `poc04`. The Docker VM is shared and small, so the matrix runs one engine and one pair count at a time.

| File | What it does |
| ---- | ------------ |
| `locustfile.py` | One Locust user: native `POST /v1/run`, complete mode, a fixed short input. `LOAD_KEY_SHARE` (0 to 1, suggested default 1.0) is the share of calls with a fresh `Idempotency-Key`; `LOAD_NO_KEY=1` sends none. |
| `run_matrix.py` | Per scenario: `scale.sh up`, wait for `/ready`, warm up, Locust headless, `docker stats` samples of project `poc04` every 2 s, `scale.sh down`. |
| `steady_client.py` | Plain httpx steady load for the drills. `--no-retry` (default) or `--retry` (same key, same input). Prints totals; exit 1 when a call failed. |
| `kill_drill.py` | `--mode kill`: SIGKILL one pair under retried load, then replay the retried keys and check the same envelope comes back. `--mode graceful`: SIGTERM the chassis, wait, stop the workload, with no retry; expects 0 failures. |

Locust is not a workspace dependency. It runs through `uv run --no-project --with "locust==2.46.6"` (pinned in `run_matrix.py`).

## The matrix

```bash
make load-test ARGS="--dry-run"                               # the plan, nothing runs
make load-test ARGS="--engine echo-python --pairs 1"          # one engine, one pair count
make load-test ARGS="--engine echo-typescript"                # one engine, 1, 2, and 4 pairs
make load-test ARGS="--only hop"                              # the sidecar hop only
```

Scenarios:

- `main`: each engine (`echo-python`, `echo-pydanticai`, `echo-langgraph`, `echo-typescript`) at 1, 2, and 4 pairs, 64 users (suggested).
- `hop`: `echo-python` at 1 pair, 8 users, in the sidecar lane and in the inprocess lane (`scale.sh up inprocess 1`, `packages/chassis/configs/scale-inprocess.yaml`). The hop is sidecar minus inprocess at p50 and p95.
- `idem`: `echo-python` at 1 pair with `LOAD_NO_KEY=1`. Its p50 and p95, minus those of the keyed `echo-python-1p` run, are the cost of idempotency.

Options: `--engine` and `--pairs` (repeatable) pick a subset. `--only main|hop|idem` (repeatable) picks the kinds. `--duration-s` (60), `--warmup-s` (10), `--users` (64), `--hop-users` (8), and `--locust-processes` (4) are the suggested defaults from the plan.

Output, under `../notes/load/`:

- `raw/<scenario>_stats.csv`, `_stats_history.csv`, `_failures.csv`, `_exceptions.csv`: Locust's CSVs.
- `raw/<scenario>-docker-stats.csv`: `phase` (`idle` or `load`), time, container, vCPU, MiB.
- `results.json`: each run merges into it, so partial runs add up. Its keys:
  - `engines.<engine>.<pairs>` holds `rps`, `requests`, `failures`, `p50_ms`, and `p95_ms`. It also holds `containers.<role>`, with `replicas`, `vcpu_mean`, `vcpu_max`, `mib_mean`, `mib_max`, `idle_vcpu`, and `idle_mib`. The chassis role also has `vcpu_per_100rps`.
  - `hop.sidecar`, `hop.inprocess`, and `hop.hop_ms` (`p50_ms`, `p95_ms`).
  - `idempotency.no_key` and `idempotency.cost_ms`.
  - `host`: CPUs, Docker VM memory, and how many other containers were running.

## The drills

```bash
deploy/compose/scale.sh up echo-python 2
uv run python pocs/poc-04-stateless-scalable/load/kill_drill.py --mode kill --pair 2 \
  --out pocs/poc-04-stateless-scalable/notes/load/drill-kill.json
deploy/compose/scale.sh up echo-python 2      # a fresh stack: the killed pair is gone
uv run python pocs/poc-04-stateless-scalable/load/kill_drill.py --mode graceful --pair 2
deploy/compose/scale.sh down
```

Or pass `--up <engine>` (with `--pairs 2` or `4`) so the drill brings the stack up and down itself. `--dry-run` prints the Docker commands. Defaults (suggested): 20 workers, 40 s of load, the action at 10 s, up to 10 attempts 0.5 s apart.

The kind rolling-restart drill (`deploy/kind/run.sh`) calls `steady_client.py --url <url> --concurrency 20 --duration-s <s> --no-retry` and uses its exit code.
