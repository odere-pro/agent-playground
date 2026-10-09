# packages/bakeoff

The PoC-6 benchmark kit: `python -m bakeoff smoke | run`. Usage, the matrix, and the columns are in `README.md`.

- No agent framework, no product SDK, and no key. Never imported by the chassis; import-linter enforces it. It may import `chassis.fakes` (tool schemas), `fake_model_server`, `httpx`, `uvicorn`, and `yaml`. It declares no dependency of its own: the workspace venv has them (adding one changes `uv.lock`).
- No Dockerfile.
- The CLI is not a test, so it may use localhost TCP. A test that spawns it is marked `network`. All other tests run offline; `httpx.MockTransport` stands in for the chassis.
- Trust rule: an untrusted engine runs only in `remote`. Keep `registry.lanes_for`, `registry.check_lane`, and the generated `spec.trust` in step.
- Every spawned process gets `envs.build_env(...)`: `PATH`, a temp `HOME`, the variables it needs. Never pass `os.environ`. Never print a variable that starts with `ANTHROPIC_` or `CLAUDE_`, or ends in `_TOKEN` or `_KEY`. The model key is the dummy in `config.py`.
- Every process runs through `procs.Cleanup` (own process group, killed at exit). Ports come from `procs.free_port`.
- A new engine is one `Engine` row in `registry.py`: handle or runner, trust, script, mapping files, and the skip rule. Add its row to the tests in `test_bakeoff_matrix.py`.
- Tests start with the package name.
