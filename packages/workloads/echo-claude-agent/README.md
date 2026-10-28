# echo-claude-agent

The simplifier as a Claude Agent SDK agent (`claude-agent-sdk==0.2.165`), shell and file tools on. Lane `remote`, trust `untrusted`. PoC-6 (6b), exit criteria 1 and 4: an engine behind `handle`, tested in its lane.

## Why `untrusted` and `remote`

The model can run `Bash` and write files with `Write`. A workload that runs shell or writes files itself is `untrusted` (ADR-001 item 5), so it gets no sidecar. It runs as a remote in a gVisor pod with egress denied, and the chassis reaches it over A2A. The remote sandbox is the boundary: the SDK runs with `permission_mode="dontAsk"`, so nothing prompts, and only the allowed tools (`Bash`, `Read`, `Write`, and the chassis MCP tools) run.

## Shape

- `src/echo_claude_agent/handle.py`: `handle(input, ctx)`, the wire form. One SDK `query()` per run, which is one `claude` CLI process. The options and the per-run env are built here.
- `src/echo_claude_agent/mapping.py`: SDK messages to chassis events. Its own file so its size can be counted.
- `handle.query_fn` is the test hook. It is the SDK's `query` by default. **It replaces the skeleton's two `transport` hooks** (`handle.transport`, `tools.transport`) and `tools.py` is gone: the CLI is the model client and the MCP client, so this process makes no HTTP request of its own to hook.

## Events

`start`, `delta`s, `tool_call`s, `metrics`, `end`; or `start` then one `error`. `handle` never raises.

| SDK | Event |
| --- | ----- |
| `StreamEvent` text delta (partial messages are on) | `delta`. The `TextBlock` that follows is skipped, so no text is sent twice |
| `TextBlock` with no partials before it | `delta` |
| `ToolUseBlock` plus its `ToolResultBlock` | one `tool_call{call_id, name, arguments, result}`. `mcp__chassis__glossary_lookup` is `glossary_lookup`; `Bash` stays `Bash`. `result` is the tool's JSON object, else `{"text": ...}`, else `{"error": ...}` |
| `ResultMessage` | `metrics{input_tokens, output_tokens, latency_ms, model_route, attempt}`, then `end{status: ok}` |

Error codes (all suggested names except the `http_` form, which matches the other workloads):

| Code | When |
| ---- | ---- |
| `http_<status>` | The CLI saw that status from the model route (`ResultMessage.api_error_status`). Retryable at 5xx and 429 |
| `tool_loop_exceeded` | `error_max_turns`: more than 4 turns (3 tool rounds and the answer) |
| `timeout` | `ctx.budget.timeout_ms` passed (suggested default 30 s). The SDK generator is closed, which stops the CLI |
| `cli_error` | The CLI would not start, or died (`CLINotFoundError`, `ProcessError`, bad JSON from the CLI) |
| `model_error` | Any other failed run |
| `env_not_clean` | The environment guard fired. No CLI was started |

## What a run builds

Per run, in `ClaudeAgentOptions.env` (applied over `os.environ`):

- `ANTHROPIC_BASE_URL`: `CHASSIS_MODEL_URL` (default `http://127.0.0.1:8090/v1`) without its trailing `/v1`. The CLI adds `/v1/messages`.
- `ANTHROPIC_AUTH_TOKEN`: `CHASSIS_API_TOKEN` when set and not empty, else a placeholder (the loopback proxy ignores it).
- `ANTHROPIC_MODEL` and `ANTHROPIC_SMALL_FAST_MODEL`: `ctx.model_route` (default `big-default`).
- `ANTHROPIC_CUSTOM_HEADERS`: `traceparent: <ctx.traceparent>`; empty without one. Always set, so an inherited value cannot get through.
- `DISABLE_TELEMETRY`, `CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC`, `DISABLE_AUTOUPDATER`, `DISABLE_ERROR_REPORTING`: `1`.
- `HOME`: a fresh directory under `CLAUDE_AGENT_HOME_BASE` (default `/tmp`). `cwd` is `HOME/work`. Both are removed after the run, once the CLI has exited.

Other options: the system prompt is the fixed `simplifier-v1` text and **replaces** the Claude Code prompt (it is not appended). The input text is the prompt, a separate user turn. `tools=["Bash","Read","Write"]`, `allowed_tools` adds `mcp__chassis`. `strict_mcp_config` and `setting_sources=[]` stop the CLI from loading any other MCP or settings file. `max_turns=4`.

MCP: one `http` server named `chassis` at `CHASSIS_TOOL_URL` (default `http://127.0.0.1:8090/mcp`). The CLI does not pass its own headers to MCP (capture: 0 of 7 requests), so the server config carries `traceparent` and `Authorization: Bearer <CHASSIS_API_TOKEN>`, when set. The config goes to the CLI as `--mcp-config` on its command line.

### The environment guard

The SDK merges `os.environ` into the CLI's environment. So before it starts, `handle` looks at the names in `os.environ`. An `ANTHROPIC_*` or `CLAUDE_*` name that the run does not override (the list is `OVERRIDDEN` in `handle.py`) gives `start` then `error{code: "env_not_clean"}` with the names only, and no CLI. The check covers a provider key in the pod and a live Claude Code session's variables. The e2e test starts `handle` with `env -i` and an allow-list for the same reason. Never run this workload with a `claude` from `PATH`: the SDK uses the CLI bundled in its wheel.

## What it writes and spawns (from the capture, `pocs/poc-06b-bake-off-remote-lane/notes/2026-10-09-claude-cli-capture.md`)

- One `claude` process per run, a 242 MB glibc ELF, 175 to 240 MB resident. No Node, no helper daemon. Under the `Bash` tool: `/bin/bash -c ...` and the command.
- Under `HOME`: `.claude.json` (written by tmp file and rename, so the volume must rename in one directory), `.claude/backups/`, `.claude/projects/<cwd>/<session>.jsonl` (the transcript, 100 KB and up, **holds the prompt and every tool result**), `.claude/shell-snapshots/`, `.cache/claude-cli-nodejs/.../mcp-logs-chassis/`. The tools write into `cwd`.
- Nothing outside `HOME`, `cwd`, and `/tmp` (for `Bash`). In the pod: a read-only root, with `/tmp` an emptyDir (admission rule T4 forbids only volume claim templates).
- Egress: only the model base URL and the MCP URL, with the four disable flags on. Without the flags the capture says nothing.

## Security note

The `Bash` tool's child processes inherit `ANTHROPIC_AUTH_TOKEN` (and `ANTHROPIC_BASE_URL`, `ANTHROPIC_CUSTOM_HEADERS`). A command the model writes can read them. `ps` shows the MCP bearer token too, since it is in `--mcp-config`. In the remote lane that token is the per-remote chassis token (`CHASSIS_API_TOKEN`). It works only while a run is in flight (`RequireRun`), on the chassis's proxies, with egress denied. That is the blast radius (ADR-005, contract v5 A.12). No provider key ever reaches this pod, and the guard refuses to start if one is in the environment. The unit tests set a fixture token and check that it is not in an `error` message.

## Router compatibility

The CLI calls one path: `POST /v1/messages?beta=true`, always `stream: true`, auth `Authorization: Bearer`. It never called `count_tokens`, `/v1/models`, or a second model. Fields (capture, CLI 2.1.294) and what the chassis route does (contract v5 draft, part A):

| Sent | Route |
| ---- | ----- |
| `model` (= `ctx.model_route`) | maps to the route |
| `messages`, including `role: "system"` entries and `tool_use`/`tool_result` blocks | maps (system role in place) |
| `system` (billing block plus agent line) | joined into one system message; the billing block is dropped |
| `tools` (Bash, Read, Write, the MCP tools; with `tools=[...]` the 20 built-ins are not offered) | maps to chat tools |
| `max_tokens: 32000` | maps. A call in a run is clamped to the run's budget, which is why `traceparent` matters; an uncorrelated call would be refused 429 (cap 20000) |
| `stream: true` | SSE answer |
| `thinking`, `output_config`, `context_management`, `cache_control`, `safeguards` | dropped and counted |
| `metadata` | dropped, silent (it holds a device id) |
| headers `anthropic-version`, `anthropic-beta`, `x-stainless-*`, `user-agent` | not read |
| `traceparent` (from `ANTHROPIC_CUSTOM_HEADERS`, fixed at process start) | the run key. One CLI per run is required, which is why a run never reuses a CLI |

Not yet seen with the real route: the CLI's reaction to a 4xx or 5xx (retries, `x-should-retry`), long sessions (compaction, title, `count_tokens` calls), and a small model's context window with the 24-tool list.

## Hidden state

- The transcript and `.claude.json` under `HOME`: removed with it. If `CLAUDE_AGENT_HOME_BASE` is not private, another process could read them in flight.
- `ANTHROPIC_CUSTOM_HEADERS` is read at process start, so the `traceparent` is the run's, not the call's.
- The CLI keeps a session id (`x-claude-code-session-id`) that is stable for one run and not used by the chassis.
- No state survives between runs. Session resume and `continue_conversation` are off.

## License and maturity

`claude-agent-sdk` is MIT and classified Alpha (PyPI `Development Status :: 3 - Alpha`); it is pinned to 0.2.165. The CLI inside the wheel is Anthropic's Claude Code, a closed binary: its use falls under Anthropic's terms, not the MIT license (not verified here; a `platform-security` or legal check is open). Its behavior is version-specific (the `safeguards` field, the `<total_tokens>` note), so rerun the capture on each SDK bump.

## Run

```bash
uv run workload-a2a serve --handle echo_claude_agent:handle --port 9000
docker build -f packages/workloads/echo-claude-agent/Dockerfile -t echo-claude-agent .   # from the repo root
```

The image is glibc (`python:3.12-slim`; Alpine does not work), has `/bin/bash` and coreutils, runs as uid 10002 with a read-only root, and `HOME` and `CLAUDE_AGENT_HOME_BASE` are `/tmp`. In the pod `/tmp` is an emptyDir.

## Test

```bash
scripts/check_offline.sh packages/workloads/echo-claude-agent -q          # the gate: 27 tests, no CLI
uv run pytest -m network packages/workloads/echo-claude-agent -q -rs      # the real CLI end to end
```

`tests/test_echo_claude_agent_handle.py` feeds SDK message objects built with the SDK's own types through `handle`: the three tasks, the options and env, every error code, the guard, and the event schema. `tests/test_echo_claude_agent_e2e.py` (`network`) runs the real bundled CLI in a child started with `env -i` against a chassis model proxy with `/v1/messages` over the fake model (`bakeoff.yaml`) and a two-tool MCP stub. It skips until the chassis route exists.
