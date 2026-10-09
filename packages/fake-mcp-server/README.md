# fake-mcp-server

A scripted MCP server with four harmless tools, for offline tests and the kind cluster. It has no auth of its own: on kind a NetworkPolicy lets only LiteLLM reach it.

```bash
uv run fake-mcp-server                               # 127.0.0.1:8082, MCP at /mcp/
uv run fake-mcp-server --allow tok=glossary_lookup   # tok sees and may call one tool
```

Tools, the idempotency key, and the `--allow` rules: [CLAUDE.md](CLAUDE.md). Offline tests start the app in process with `create_app(FakeMcpState(...))` and an ASGI transport, so no port opens.
