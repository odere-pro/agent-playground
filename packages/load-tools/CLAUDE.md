# packages/load-tools

Load tools for the Compose scale stack: the load matrix, the Locust user, and the steady client. CI (`.github/workflows/poc06-load.yml`), `make load-test`, `deploy/kind/run.sh`, and `deploy/compose/poc06/load_driver.py` run them. What each does: `README.md`.

## Rules

- Never imports `chassis` or `poc_harness`; `make lint` (import-linter) enforces it.
- Callers run `python -m load_tools.<module>` or import the module. No caller names a file in a PoC folder; `tests/test_load_tools_callers.py` checks it.
- `locustfile.py` imports no workspace package: Locust loads it by path from its own venv.
- Locust installs from the hashed lock `locust/requirements.txt` into its own venv (`run_matrix.install_locust`: `uv pip install --require-hashes --no-deps`). Never by name (`--with`). To bump: edit `locust/requirements.in`, then `uv pip compile packages/load-tools/locust/requirements.in --generate-hashes --universal --python-version 3.12 -o packages/load-tools/locust/requirements.txt`; `tests/test_load_tools_locust_lock.py` checks every input is pinned and every pin has a hash.
- Keep the flags, defaults, and output shape. Dated PoC-4 and PoC-6 notes record commands that must still run.
- The tools touch only containers of Compose project `poc04`. No secret in a flag, an output, or a log.
- Tests start with the package name (`test_load_tools_*`), run offline, and start no container.
