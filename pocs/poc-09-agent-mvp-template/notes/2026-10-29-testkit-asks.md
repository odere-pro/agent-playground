# 2026-10-29: asks from the test fixture kit

For `packages/chassis` and `packages/workload-a2a`. The kit works around each one today.

1. **`chassis serve --uds PATH`** (chassis). Serve the public listener on a Unix socket. The offline gate refuses TCP in the test process, so the kit cannot reach `/v1/run` on `--port`. Workaround: `packages/agent-testkit/src/agent_testkit/data/chassis_serve.py` calls `cli.parse_args` and `cli.build_all`, sets `servers.public.config.uds`, and repeats the tail of `cli.main`. It depends on those internals, so it breaks if they change.
2. **`chassis serve --profile fake` with no config file** (chassis). The plan suggests `uvx chassis serve --profile fake`. Today `--config` is required, so the kit writes a config from `data/chassis-fake-sidecar.json`. A `--profile fake --engine-uds PATH --agent-name NAME` form would remove the file.
3. **`--proxy-port 0` with the bound port reported** (chassis). The kit asks the OS for a free port in a child process, closes it, and passes it to both processes. A short race exists between close and bind. A port file or a log line with the bound port would end it.
4. **A published `chassis` package** (release). Outside the monorepo, `uv tool run --from chassis==X` needs an index that holds it. Until then an agent repo points `platform_path` at a checkout.
5. No ask for `workload-a2a`. The kit uses its CLI (`--uds`, `--name`), `mapping.py`, and `server.validate_event` as they are.
