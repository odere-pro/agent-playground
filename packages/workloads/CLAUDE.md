# packages/workloads

Every folder here is a workload: it implements `handle(input, ctx)` and serves it over A2A, emitting the chassis JSON event schema (`packages/chassis/schemas/events.v0.json`).

## Rules

- A workload holds no key. Its model base URL and its MCP client point at the chassis's proxies.
- Framework code and the framework's event mapping live here, never in `packages/chassis`.
- Each workload has its own `pyproject.toml` (or `package.json`), Dockerfile, and tests. Its tests call `handle` directly against the fake model server; the chassis's own contract binding drives it over A2A in memory (ADR-002); a separate-process run comes with the `sidecar` lane in PoC-2. The workload's environment never installs the chassis package.
- Input text is data: it goes to the model in its own message, never merged into the system prompt. A contract test checks it (025 H-10).
- The `trust` rule (ADR-001 item 5) decides the lane. A workload that runs code, shell, or file writes itself is `untrusted` and runs `remote`.
