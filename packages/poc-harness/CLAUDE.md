# packages/poc-harness

Test support shared across PoC folders. The root `CLAUDE.md` rules apply. `README.md` lists the modules.

## Rules

- A module goes here only when more than one PoC folder, or a PoC demo, uses it. Folder-local helpers stay in their PoC `tests/`.
- Domain names, never a PoC number, in a module name.
- Product code never imports this package. An import-linter contract in the root `pyproject.toml` enforces it.
- Find repo files through `poc_harness.paths.ROOT`, never `Path(__file__).parents[N]`.
- Offline: Unix sockets and ASGI only. A TCP helper (`serve_tcp`, `free_port`) is for `network` tests.
- No `sys.path` insert. A PoC test imports `poc_harness.<module>`.
- Each module under about 400 lines; each function under 50.
- The one threaded-uvicorn runner and `free_port` live in `chassis_contracts.serving`, because the contract suites cannot depend on this package. The one SSE parser is `chassis_contracts.inbound.sse`.
