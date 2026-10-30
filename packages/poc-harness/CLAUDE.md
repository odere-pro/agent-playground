# packages/poc-harness

Test harness code shared across PoC folders. The root `CLAUDE.md` rules apply. `README.md` lists the modules.

## Rules

- A module goes here only when a second PoC folder imports it. Folder-local helpers stay in their PoC `tests/`.
- Domain names, no PoC number in a module name.
- Find repo files through `poc_harness.paths.ROOT`, never `Path(__file__).parents[N]`.
- Offline: Unix sockets and ASGI only. A TCP helper is for `network` tests.
- No `sys.path` insert anywhere. A PoC test imports `poc_harness.<module>`.
- The chassis never imports this package (import-linter contract).
