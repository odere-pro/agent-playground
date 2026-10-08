# echo-python

The plain-Python simplifier: the first workload behind the chassis (PoC-1). `handle(input, ctx)` takes the input text, asks a model to rewrite it in plain words, and streams the answer back as chassis events.

## Shape

- `src/echo_python/handle.py`: `handle(input: dict, ctx: dict) -> AsyncIterator[dict]`. Events follow `packages/chassis/schemas/events.v0.json`: `start`, `delta` per chunk, `metrics`, `end`; `error` on an HTTP or connection failure.
- The model call is OpenAI-compatible HTTP (`POST {CHASSIS_MODEL_URL}/chat/completions`, `stream: true`). `CHASSIS_MODEL_URL` defaults to `http://127.0.0.1:8080/v1`, the chassis's model proxy. `ctx.model_route` is the `model`; `big-default` when unset.
- Prompt `simplifier-v1`: the system prompt is fixed; the input text is its own user message.
- No key here and no `Authorization` header. The chassis holds the credential.
- Dependencies: `httpx` only. Never `chassis`.

## Run

The chassis loads it: `spec.engine.connector: inprocess`, `spec.engine.handle: echo_python:handle` (`packages/chassis/configs/fake.yaml`).

## Test

`uv run pytest packages/workloads/echo-python`. The tests drive `handle` against the fake model server over an ASGI transport, through the module's `transport` hook. No socket, no key.
