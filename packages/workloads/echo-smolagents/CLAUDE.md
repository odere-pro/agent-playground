# packages/workloads/echo-smolagents

The simplifier as a smolagents `CodeAgent` (PoC-6, 6b). `packages/workloads/CLAUDE.md` still applies. The README has the design, the router-compatibility list, and the untrusted-lane rationale.

- One public thing: `echo_smolagents.handle`, the wire form (dicts in, event dicts out, `schema_version: "0"`). Served by `workload-a2a serve --handle echo_smolagents:handle`.
- Never import `chassis`; import-linter enforces it. Copy constants from echo-python, do not import it.
- No model key. Never read an `*_API_KEY` variable. Model and MCP URLs point at the chassis. `CHASSIS_API_TOKEN` (remote lane) is the bearer on both, only when set and not empty.
- Lane `remote`, trust `untrusted`: the model's Python runs in this process. Do not add authorized imports, a file or network tool, or a different executor without `platform-security`.
- Hooks: `handle.transport` (sync `httpx2.BaseTransport`, the model) and `tools.transport` (async, MCP). Tests serve the stubs on Unix sockets and set them.
- `max_steps` is 4. Running out is `tool_loop_exceeded`; a failed MCP call is `tool_error`. A failure is one `error` event, never an exception out of `handle`.
- `ChassisModel` cuts the reply at the stop sequences itself, because the chassis model proxy drops `stop`. Keep it. If you add a body key, update the README table and `test_the_chat_body_has_exactly_these_keys`.
- `tools.py` is a stand-in for `ToolCollection.from_mcp` (mcpadapt 0.1.20 does not import against `mcp` 2). Delete it when a compatible release is locked.
- The intermediate code is not streamed; only the final answer is a `delta`.
- New tests: unique basenames (`test_echo_smolagents_*.py`). The fake-model script is `tests/scripts/smolagents.yaml`: first match wins, and observations are the next user message.
