# chassis

The service chassis: it serves one workload's `handle` behind the public interfaces and holds every credential. It exists so that every service gets the same wire contract, budget, and security, and no service holds a key ([ADR-001](../../docs/planning/adr/001-chassis-delivery-model.md)). Run its tests with `uv run pytest packages/chassis`, and serve it offline with `uv run chassis serve --config packages/chassis/configs/fake.yaml`.

The layout and rules are in [CLAUDE.md](CLAUDE.md). Its `Spec:` line names the contract in force. Today that is [contract v6](../../docs/contracts/contract-v6.md), a draft with PoC-7's additive changes, and [v5](../../docs/contracts/contract-v5.md) and earlier for the rest. Per-module detail is in [the chassis reference](../../docs/guides/chassis-reference.md).

```bash
uv run pytest packages/chassis   # unit tests and contract bindings
make schemas                     # regenerate schemas/*.json from the models
```

## Endpoints

`chassis serve` opens two listeners, three in the `remote` lane.

- Public port (`--port 8080`): native `POST /v1/run`; the OpenAI interface; the Anthropic interface (`POST /v1/messages`); `/v1/mcp`, the agent as one MCP tool; `GET /manifest`, `GET /health`, `GET /ready`, `GET /openapi.json`. `spec.interfaces` switches the OpenAI, Anthropic, and MCP interfaces off; native is always on.
- Proxy port (`127.0.0.1:8090`, localhost only): the model proxy, which workloads call for models, and `/mcp`, the tools for workloads. The model proxy has two routes: the OpenAI chat route and the Anthropic Messages route (`POST /v1/messages`, since contract v5).
- Remote proxy port (`remote` lane only): `--remote-proxy-host <pod IP>` and `--remote-proxy-port` (suggested: 8091). It serves the same model proxy routes and `/mcp` to a workload in another pod. Each call needs the bearer token and a run in flight (`server/remote_auth.py`).

The OpenAI and Anthropic interfaces (public port) each share a path with a model proxy route (proxy port). Never write those paths bare: name the interface and the port.
