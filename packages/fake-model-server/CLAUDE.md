# packages/fake-model-server

A scripted OpenAI-compatible server. Frameworks call models over HTTP with their own clients, so a Python mock cannot intercept them; this is how every engine is tested offline. It stands where LiteLLM would, behind the chassis model proxy.

## Endpoints

`POST /v1/chat/completions` (complete, and `stream: true` as server-sent events with a final chunk carrying `usage`, then `data: [DONE]`), `GET /v1/models`, `GET /health`.

## Script format

```yaml
model: fake-model
rules:
  - match: "glossary"                  # substring of the last user message; first match wins
    tool_call: { name: glossary_lookup, arguments: { term: "SLM" } }
  - match: "fail"
    error: { status: 500, message: "scripted failure" }
  - match: "simplify"
    reply: "Plain words."
    usage: { prompt_tokens: 42, completion_tokens: 9 }
default_reply: "ok"                    # when no rule matches
```

## Rules

- No dependency on `chassis`. This package must stay usable from a workload's own tests.
- Keep the wire shape OpenAI-compatible; the point is that stock clients work unchanged.
- Tests use the httpx ASGI transport, no socket. `make fake-model-server` runs it on 8081 for manual use.
