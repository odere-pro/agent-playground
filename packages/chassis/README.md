# chassis

The service chassis package. See [CLAUDE.md](CLAUDE.md) for the layout and rules, [docs/contracts/contract-v0.md](../../docs/contracts/contract-v0.md) for the contract, and [ADR-001](../../docs/planning/adr/001-chassis-delivery-model.md) for how it runs next to a service.

```bash
uv run pytest packages/chassis   # unit tests and contract bindings
make schemas                     # regenerate schemas/*.json from the models
```
