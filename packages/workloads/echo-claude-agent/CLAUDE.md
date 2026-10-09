# packages/workloads/echo-claude-agent

The simplifier as a Claude Agent SDK agent (PoC-6, 6b), shell and file tools on. `packages/workloads/CLAUDE.md` still applies.

- One public thing: `echo_claude_agent.handle`, the wire form (dicts in, event dicts out, `schema_version: "0"`). Served by `workload-a2a serve --handle echo_claude_agent:handle`.
- One test hook: `echo_claude_agent.handle.query_fn` (default: the SDK's `query`). Tests feed SDK message objects through it; no CLI starts. It replaced the skeleton's `transport` hooks.
- The SDK mapping lives in `mapping.py` only. Error codes: `http_<status>`, `tool_loop_exceeded`, `timeout`, `model_error`, `cli_error`, `env_not_clean`.
- Never import `chassis`; import-linter enforces it. Copy constants from echo-python, do not import it.
- No model key. Never read an `*_API_KEY` variable. `CHASSIS_API_TOKEN` is the per-remote chassis token. Model and MCP URLs point at the chassis.
- The SDK merges `os.environ` into the CLI. `handle` refuses to start (`env_not_clean`) on an unlisted `ANTHROPIC_*` or `CLAUDE_*` name. Keep the `OVERRIDDEN` list the set of names the run sets itself. Never print a value of such a variable.
- Use only the CLI bundled in the SDK wheel. A test that starts the real CLI runs it in a child with `env -i` and an allow-list, and is marked `network`.
- Lane `remote`, trust `untrusted`. Image uid 10002, glibc base, read-only root, `HOME` under `/tmp`.
- A failure is one `error` event, never an exception out of `handle`.
