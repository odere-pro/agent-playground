# packages/code-runner

The code-execution tool: an MCP server with one write tool, `run_python`. It runs in a gVisor sandbox pod behind LiteLLM's MCP gateway. Design: `docs/plans/2026-10-02-poc-05-sandboxed.md`, section 2.8. Usage and the result shape: `README.md`.

## The rule that matters

The sandbox pod is the security boundary, not this code. The server runs any Python it gets. It must never run outside a pod with gVisor, no egress, no mounted secret, a read-only root, and PID and memory limits in a deployed lane. Do not add a "safe mode", an import filter, or a code scanner: they would look like a control and are not one.

## Map

- `src/code_runner/runner.py` `run_python(code, timeout_s, *, limits, tmp_root, python)`: the child process. `Limits`, `RunResult`, `RunnerError(code, message)`, `child_env`.
- `src/code_runner/isolation.py` the Linux-only isolation between calls: `ensure_reaper`, `harden_server`, `descendants`, `sweep`, `nproc_limit`, `lost`. README "Isolation between calls".
- `src/code_runner/server.py` `create_server(*, limits, tmp_root, cache_size, runner) -> FastMCP`: the tool, the idempotency cache (`ResultCache`), `GET /health`.
- `src/code_runner/cli.py` `code-runner --host --port --tmp-root --cache-size`: uvicorn, streamable HTTP at `/mcp`, stateless.
- `Dockerfile`: uid 10003, root-owned `/app`, read-only root, writes only `/tmp`.

## Rules

- Never imports `chassis` or `chassis_contracts`; `make lint` (import-linter) enforces it.
- The child gets `child_env(...)` only, no shell, `-I -S`, a new session, a fresh directory removed afterwards. Keep it that way.
- One call at a time per process (`MAX_CONCURRENT = 1`): the sweep kills every process started since the call began. Do not raise it.
- Isolation tests that need prctl or `/proc` are marked to skip off Linux; keep a portable test for the process-group path.
- Error codes are stable strings at the start of the tool error: `idempotency_key_required`, `bad_arguments`, `run_failed`.
- Never log the code, its output, or a key. The log line has sizes, exit code, and timing.
- Tests start with `test_code_runner_` (basenames are unique in the repo). They use the in-process FastMCP client and `httpx.ASGITransport`, no socket. A test that starts a child keeps the code tiny and the timeout short.
- `pyproject.toml` and `uv.lock` belong to T01 in PoC-5: ask the orchestrator for a dependency change.
