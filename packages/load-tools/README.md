# load-tools

Load tools for the Compose scale stack (`deploy/compose/scale.sh`, project `poc04`, Traefik on `127.0.0.1:18080`). PoC-4 built them; CI and PoC-6 still run them. Design: `docs/plans/2026-10-01-poc-04-stateless-scalable.md`, sections 9 and 10.

| Module | What it does | Run it |
| ------ | ------------ | ------ |
| `load_tools.run_matrix` | Per scenario: `scale.sh up`, wait for `/ready`, warm up, Locust headless, `docker stats` samples of project `poc04` every 2 s, `scale.sh down`. Merges into `results.json`. | `make load-test ARGS="--dry-run"` or `uv run python -m load_tools.run_matrix --dry-run` |
| `load_tools.locustfile` | One Locust user: native `POST /v1/run`, complete mode, a fixed short input. `LOAD_KEY_SHARE`, `LOAD_NO_KEY`, `LOAD_RATE_PER_USER`, `LOAD_TEXT`. | `locust -f packages/load-tools/src/load_tools/locustfile.py` (`run_matrix` does this) |
| `load_tools.steady_client` | Plain httpx steady load for the drills. `--no-retry` (default) or `--retry`. Prints totals; exit 1 when a call failed. | `uv run python -m load_tools.steady_client --url <url> --concurrency 20 --no-retry` |

Locust is not a workspace dependency. Before the first scenario `run_matrix` installs it into a venv in a new temp dir, removed when the run ends, from the hashed lock `locust/requirements.txt`, with `uv pip install --require-hashes --no-deps`. Locust loads `locustfile.py` by path in that venv, so `locustfile.py` imports no workspace package.

`run_matrix` writes under `pocs/poc-04-stateless-scalable/notes/load/` by default, where the dated PoC-4 results live. `--results` moves `results.json`; `deploy/compose/poc06/load_driver.py` moves the raw files too.

The PoC-4 folder keeps thin entry scripts with the old file names, so its dated commands still run, and its drills (`kill_drill.py`, `idem_cost.py`, `median.py`) import this package. The flags, defaults, and outputs are those of the PoC-4 files.
