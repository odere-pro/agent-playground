# packages/fake-mcp-server

A test MCP server (FastMCP, streamable HTTP at `/mcp/`). Design: `docs/plans/2026-10-02-poc-05-sandboxed.md`, section 2.7. It stands where a real MCP server would sit behind LiteLLM's MCP gateway on kind, and in process offline for the `McpGatewayTools` contract binding.

## Tools

- `glossary_lookup(term)`: read-only (`readOnlyHint: true`). Same data as the chassis fake; an unknown term gives `definition: null`, not an error.
- `acronym_expand(acronym)`: read-only (`readOnlyHint: true`), added for PoC-6. Same data as the chassis fake (`ACRONYMS`); an unknown acronym gives `expansion: null`. It is not a write tool and is in no write-mode set.
- `note_write(text, idempotency_key=None)`: write tool (`readOnlyHint: false`). One note per key; a repeated key returns the first result and does not execute again. No key is a tool error whose text starts `idempotency_key_required`. `FakeMcpState.executions` counts real writes.
- `unlisted_probe()`: returns `PROBE_MARKER`. It is on no real allow-list; a test that sees the marker means the allow-list failed (H08).

## The idempotency key

A client sends it as `_meta.idempotency_key` of the `tools/call` request. If there is no `_meta` key, the optional `idempotency_key` argument is used (the fallback of plan section 2.7, for a gateway that drops `_meta`). It is not read from an HTTP header. `_meta` wins when both are set.

## `--allow`

`--allow TOKEN=tool,tool` (repeatable) imitates a gateway's per-key list. The token is the `Authorization: Bearer` value. Tools outside the list are not listed, and a call to one fails as `unknown tool: <name>`. `--allow tool,tool` with no token covers any caller. With any `--allow`, a caller with no matching entry sees no tools. Without it, everyone sees all four.

## Run and bind

`--host` defaults to `127.0.0.1`; the image passes `0.0.0.0`. `--port` defaults to 8082. `GET /health` answers `{"status": "ok"}`. `--expose-calls` serves `GET /calls` (executions and the calls received) for offline tests; keep it off on kind.

## Rules

- Never imports `chassis` or `chassis_contracts`; `make lint` (import-linter) enforces it.
- No secret anywhere. The image runs as a non-root user (uid 10005; the uid table in `deploy/README.md`), with `/app` owned by root.
- Tests start with the package name (`test_fake_mcp_*`), run in process over an ASGI transport, and open no socket. A client factory must take `**kwargs` (FastMCP passes `follow_redirects` and others).
- `pyproject.toml` and `uv.lock` belong to T01 in PoC-5: ask the orchestrator for a dependency change.
