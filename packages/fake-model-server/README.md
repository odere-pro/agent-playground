# fake-model-server

```bash
make fake-model-server                      # port 8081, scripts/example.yaml
uv run fake-model-server --script my.yaml --port 8081
```

Script format and endpoints: [CLAUDE.md](CLAUDE.md). Every chat request body is recorded in `app.state.calls`.

A scripted tool loop ends: the rule that calls a tool matches the last user message, and only while the last message is not a tool result. After the tool answers, a rule with `after_tool: true` replies (its `match`, when set, is tested against the tool result), or `default_reply` does. See [scripts/example.yaml](scripts/example.yaml).

A scripted tool name that the request's `tools` list does not hold is answered by the ONE offered name that is the scripted name with a prefix (`mcp__chassis__glossary_lookup`, `fake_tools-glossary_lookup`; separators `__` and `-`). An exact match, no match, or two candidates keep the scripted name. So one script serves clients that prefix tool names, such as the Claude CLI.

[scripts/bakeoff.yaml](scripts/bakeoff.yaml) is the PoC-6 script: smoke, the simplifier, and a lookup that calls `glossary_lookup`, then `acronym_expand`, then answers. The answer rule sits before the acronym rule, because the first match wins.
