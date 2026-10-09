# packages/fake-model-server

A scripted OpenAI-compatible server. Frameworks call models over HTTP with their own clients, so a Python mock cannot intercept them; this is how every engine is tested offline. It stands where LiteLLM would, behind the chassis model proxy.

## Endpoints

`POST /v1/chat/completions` (complete, and `stream: true` as server-sent events with a final chunk carrying `usage`, then `data: [DONE]`), `GET /v1/models`, `GET /health`. Chat request bodies are recorded in `app.state.calls`, in order, so a test can check the messages a client sent. It is a `deque` of the last `max_calls` (suggested: 1000; a `create_app` keyword) so load runs stay bounded; `app.state.calls_total` counts every call.

## Script format

```yaml
model: fake-model
rules:
  - match: "glossary"                  # substring of the last user message; first match wins
    tool_call: { name: glossary_lookup, arguments: { term: "SLM" } }
  - after_tool: true                   # only when the last message is a tool result
    match: "small language"            # optional; tested against that tool result
    reply: "From the glossary: SLM means small language model."
  - match: "fail"
    error: { status: 500, message: "scripted failure" }
  - match: "simplify"
    reply: "Plain words."
    usage: { prompt_tokens: 42, completion_tokens: 9 }
default_reply: "ok"                    # when no rule matches
```

A tool loop ends (suggested): a rule without `after_tool` matches only when the last message is not a `tool` message, so the rule that called the tool is not picked again on its result. An `after_tool: true` rule answers the result; when none matches, `default_reply` does. `chassis.fakes.ScriptedModel` follows the same rules (`ScriptRule.after_tool`).

Trailing turns (suggested): the Claude CLI adds a `system` note, and sometimes a `user` reminder, after a tool result. The last `tool` message is the trigger when only `system` or `user` messages follow it and none of those user messages matches any plain rule (a plain rule with no `match` matches every text). A user message that matches a plain rule is a new turn. `packages/fake-model-server/scripts/bakeoff-claude.yaml` is `bakeoff.yaml` with the `mcp__chassis__` tool-name prefix the CLI requires.

## Rules

- No dependency on `chassis`. This package must stay usable from a workload's own tests.
- Keep the wire shape OpenAI-compatible; the point is that stock clients work unchanged.
- Tests use the httpx ASGI transport, no socket. `make fake-model-server` runs it on 8081 for manual use.
