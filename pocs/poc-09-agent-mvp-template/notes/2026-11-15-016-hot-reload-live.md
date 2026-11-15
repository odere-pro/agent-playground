# 016 H-9: live hot reload on an agentctl repo (2026-11-15)

The box "A code change in the workload is live without a rebuild or a restart of the stack", run on Docker Desktop on the Mac (8 GB), one stack.

## Command

```bash
env -u VIRTUAL_ENV DEV_RELOAD_LIVE=1 uv run pytest -s -v -p no:cacheprovider \
  packages/agentctl/tests/test_agentctl_dev_reload_live.py
```

The test renders `reload-live-<6 hex>` with `agentctl new` (python, transformer, sidecar) into `tmp_path`, then runs `docker compose -p dev-reload-live-<6 hex> --profile dev up -d --wait --build` with free host ports. It calls `/v1/run`, records the container id and `State.StartedAt` of `chassis-fake` and `workload-dev`, and adds one `delta` event with a fresh marker to `src/<pkg>/handle.py` on the host. It checks the file in the container shows the marker, then polls `/v1/run` every 0.5 s up to 20 s (suggested). Teardown is `down -v --remove-orphans` and `docker image rm` of the two images it built, in a `finally`.

## Images

| Image | Id | Note |
| ----- | -- | ---- |
| `agent-platform/chassis:poc09` | `0920459f64c5` | existing, not rebuilt; no chassis change on this branch |
| `agent-platform/fake-model-server:poc09` | `0560001773bd` | built by `--build` |
| `reload-live-<hex>:dev`, `reload-live-<hex>-watchfiles-1.3.0-debugpy:1.8.22` | built per run | removed after the run |

## Runs

1. Failed. No answer showed the marker within 20 s.
2. Failed again, with `WATCHFILES_FORCE_POLLING=true` in a test override and a check that the container saw the saved file (it did). So the cause was not inotify on the bind mount.
3. Green, with no polling override. Log: `/private/tmp/claude-501/live-016b-run.log`.

The cause was in the product, not the test. `workload-a2a serve --reload` called `watchfiles.run_process` with the default filter. That filter drops every path with a `.venv` part. The dev profile watches `/app/.venv/lib/python3.12/site-packages/<pkg>`, so no save ever reloaded. The fix passes `DefaultFilter(ignore_dirs=RELOAD_IGNORE_DIRS)`, the default list without `.venv` (`packages/workload-a2a/src/workload_a2a/cli.py`). The offline test, red first: `packages/workload-a2a/tests/test_workload_a2a_reload.py::test_serve_reload_sees_a_change_in_an_installed_package`.

## Timings (run 3)

| Step | Time |
| ---- | ---- |
| `up -d --wait --build` (base layers cached) | 13.5 s |
| save to the first `/v1/run` with the marker | 1.7 s |
| pytest, render to teardown | 21.5 s |

`workload-dev` logs: `watchfiles.main` "1 change detected" at 15:26:19.898Z. Server process 8 stopped, and process 22 was serving at 15:26:21.125Z.

## What it proves

- A save in `src/<pkg>` on the host is live behind the chassis in about 2 s, with no build and no `up`.
- `chassis-fake` kept its container id `206a5be8708e` and start time. `workload-dev` kept `ab86a241cb5b` and its start time. The reloader restarted the child, not the container.
- Polling is not needed on this Docker Desktop: inotify events from host saves reach the bind mount. The README's `WATCHFILES_FORCE_POLLING` hint stays for other file sharing setups.

## What it does not prove

- Linux Docker, Colima, or other Docker Desktop file sharing modes. One Mac, one run.
- Other frameworks than `python`. They share the same `workload-dev` service and command.
- A call in flight during the restart, and a save that does not import. The README describes both. Neither was run.
- A change to `pyproject.toml` or a new dependency, which still needs `--build`.
