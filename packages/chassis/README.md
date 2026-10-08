# chassis

The service chassis package. See [CLAUDE.md](CLAUDE.md) for the layout and rules, [docs/contracts/contract-v3.md](../../docs/contracts/contract-v3.md) for the contract (with [v2](../../docs/contracts/contract-v2.md) and [v1](../../docs/contracts/contract-v1.md) for what v3 does not name), and [ADR-001](../../docs/planning/adr/001-chassis-delivery-model.md) for how it runs next to a service.

```bash
uv run pytest packages/chassis   # unit tests and contract bindings
make schemas                     # regenerate schemas/*.json from the models
```

## Endpoints

`chassis serve` opens two listeners.

- Public port (`--port 8080`): native `POST /v1/run`; the OpenAI interface; the Anthropic interface (`POST /v1/messages`); `/v1/mcp`, the agent as one MCP tool; `GET /manifest`, `GET /health`, `GET /ready`, `GET /openapi.json`. `spec.interfaces` switches the OpenAI, Anthropic, and MCP interfaces off; native is always on.
- Proxy port (`127.0.0.1:8090`, localhost only): the model proxy, which workloads call for models, and `/mcp`, the tools for workloads.

The OpenAI interface (public port) and the model proxy (proxy port) share a path. Never write it bare: name the interface and the port.
