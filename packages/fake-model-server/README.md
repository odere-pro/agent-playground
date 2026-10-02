# fake-model-server

```bash
make fake-model-server                      # port 8081, scripts/example.yaml
uv run fake-model-server --script my.yaml --port 8081
```

Script format and endpoints: [CLAUDE.md](CLAUDE.md). Every chat request body is recorded in `app.state.calls`.

A scripted tool loop ends: the rule that calls a tool matches the last user message, and only while the last message is not a tool result. After the tool answers, a rule with `after_tool: true` replies (its `match`, when set, is tested against the tool result), or `default_reply` does. See [scripts/example.yaml](scripts/example.yaml).
