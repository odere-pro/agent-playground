# packages/workloads/echo-python

The plain-Python simplifier workload. `packages/workloads/CLAUDE.md` still applies.

- One public thing: `echo_python.handle`, the wire form of the contract (dicts in, event dicts out, `schema_version: "0"`). The chassis loads it by dotted path from `spec.engine.handle`.
- Never import `chassis`. Dependencies: `httpx` only. `make lint` (import-linter) enforces it.
- Model calls go to `CHASSIS_MODEL_URL` (the chassis proxy). No key, no `Authorization` header, ever.
- The input text is a user message on its own. Do not put it in the system prompt; `tests/test_handle.py` checks it.
- `handle.transport` is a test-only hook so tests route the model call to an ASGI app. Do not read it in production paths beyond passing it to `httpx.AsyncClient`.
- Bump `PROMPT_VERSION` when the prompt changes; the chassis reports it in `versions.prompt` from config.
