# packages/workloads/echo-smolagents

Skeleton for PoC-6 (6b): the simplifier as a smolagents (code agent) agent. `packages/workloads/CLAUDE.md` still applies.

- One public thing: `echo_smolagents.handle`, the wire form (dicts in, event dicts out, `schema_version: "0"`). Served by `workload-a2a serve --handle echo_smolagents:handle`.
- Today it yields `start` and `error{code: "not_implemented"}`. Keep the signature and the two test hooks (`handle.transport`, `tools.transport`) when you fill it in.
- Never import `chassis`; import-linter enforces it. Copy constants from echo-python, do not import it.
- No model key. Never read an `*_API_KEY` variable. Model and MCP URLs point at the chassis.
- Lane `remote`, trust `untrusted`. Image uid 10002.
- A failure is one `error` event, never an exception out of `handle`.
