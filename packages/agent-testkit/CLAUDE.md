# packages/agent-testkit

The test fixture kit an agent's repo uses (PoC-9). It runs in the workload's environment. Usage and the chassis source table: `README.md`.

- Layout: `settings.py` (`[tool.agent-testkit]`, the chassis source), `processes.py` (start and stop both processes), `wire.py` (A2A client over `workload_a2a.mapping`), `lane_cases.py` (vendored lane cases), `contract.py` (`WorkloadContract`), `event_mapping.py` (`EventMappingContract`), `plugin.py` (fixtures), `stateless.py` (the 057 H-19 stateless check, in process), `stateless_lane.py` (the same check as processes in the `sidecar` lane, fresh pods against one reused pod), `readonly_root.py` (stdlib only: the write rules, and the launcher that gives the workload and the chassis process a read-only root), `evalcases.py` (the judged `make eval`: copies of the `eval_kit` case schema, scripted judge, and `score_case`), `data/` (the chassis config template and `chassis_serve.py`).
- Never import `chassis` or `chassis_contracts` in `agent_testkit`; `make lint` (import-linter) enforces it. `data/chassis_serve.py` is the one file that imports `chassis`; it is not a module of the package and runs only in the chassis's env, with `python -I`. It also applies `--model-script`.
- Dependencies: `workload-a2a`, `a2a-sdk`, `httpx`, `pytest`, `pyyaml`, `fake-model-server` (the scripted judge), `jsonschema`. Nothing a workload's env cannot hold.
- `evalcases.py` is a copy too: `eval_kit` pulls in `bakeoff` and `chassis`. Change `eval_kit.golden` or `eval_kit.evalcheck` first, then copy; `tests/test_agent_testkit_evalcases.py` fails when they drift.
- `lane_cases.py` is a copy. Change `chassis_contracts.lane` first, then copy; `tests/test_agent_testkit_vendored.py` fails when they drift.
- The test process opens Unix sockets only. Free TCP ports for the proxy are asked of the OS in a child process.
- Remove `data/chassis_serve.py` once `chassis serve --uds` exists (`pocs/poc-09-agent-mvp-template/notes/2026-10-29-testkit-asks.md`).
- Test: `UV_NO_SYNC=1 scripts/check_offline.sh packages/agent-testkit`. Test files start with `test_agent_testkit_`.
