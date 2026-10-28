# What the Claude Agent SDK CLI sends to a model endpoint, 2026-10-09

## Why

Plan task T-CAPTURE for PoC-6b (the remote lane, exit criterion 4: the engine passes the contract, load, and hostile suites in its lane; scope item "router compatibility per engine"). The chassis model proxy serves only `POST /v1/chat/completions`. The Claude Agent SDK speaks the Anthropic Messages API, so the chassis needs a `/v1/messages` route. This note records the real traffic that route has to accept. It does not move a README box yet; it is input to the route design.

## Setup

- Commit: `bd329e4` (worktree `poc06/capture`), plus the files in `notes/capture/`.
- Machine: Linux 6.18.44-fc-v80 x86_64 (Firecracker VM), 4 CPUs. No Docker used.
- SDK: `claude-agent-sdk==0.2.165` in a scratch venv outside the repo (`/tmp/poc06-capture/venv`, Python 3.13.16, uv 0.11.32). Also installed: `uvicorn`, `starlette`, `mcp==1.30.0` (the capture and MCP servers; `mcp` 2.x renamed `FastMCP`, so the venv pins `mcp<2`).
- CLI: the wheel bundles it. `_cli_version.py` says `2.1.294`; `claude --version` prints `2.1.294 (Claude Code)`.
  - Path: `<venv>/lib/python3.13/site-packages/claude_agent_sdk/_bundled/claude`.
  - One dynamically linked ELF x86-64, 242 MB (`du -sh`: 242M; the whole `claude_agent_sdk` dir is 243M, the venv 283M). Needs only libc, libm, librt, libdl, libpthread (`ldd` shows none missing).
  - **No Node needed.** The binary embeds its runtime; it reports `x-stainless-runtime: node`, `v26.3.0` in headers, but no `node` process exists. No external `claude` and no npm install were needed. The SDK prefers the bundled binary and only falls back to `which claude`. The driver still passes `cli_path` explicitly, so the session's `/opt/node22/bin/claude` was never used.
- Child environment: the SDK merges `os.environ` into the CLI environment (`ClaudeAgentOptions.env` is applied over it; nothing is dropped except `CLAUDECODE`). So the allow-list is built by `env -i` in `run.sh` before Python starts, and `driver.py` aborts on any other `ANTHROPIC_*`, `CLAUDE*`, `*_TOKEN`, or `*_KEY` name. `ANTHROPIC_AUTH_TOKEN` is the literal `dummy-capture-token`.
- Dead proxy: `HTTP_PROXY` and `HTTPS_PROXY` are `http://127.0.0.1:9`, `NO_PROXY=127.0.0.1,localhost`. Port 9 has a tripwire listener (`capture_server.py tripwire`) that logs the first request line and closes the connection, so the proxy still fails every call but a call to any other host leaves a trace.

## Commands

```
cd pocs/poc-06b-bake-off-remote-lane/notes/capture
bash ./run.sh plain "simplify: Hello." /tmp/poc06-capture/cap-a                       # run (a)
bash ./run.sh tool  "Use the glossary_lookup tool to look up SLM, then answer." /tmp/poc06-capture/cap-b   # run (b)
bash ./run.sh shell "Write a file with Bash, then Write and Read it." /tmp/poc06-capture/cap-c             # run (c)
bash ./run.sh tool-nohdr "Use the glossary_lookup tool to look up SLM, then answer." /tmp/poc06-capture/cap-bn  # (b) without MCP headers
RUN_TIMEOUT=40 bash ./run.sh control "simplify: Hello." /tmp/poc06-capture/cap-ctl   # control: base URL is an outside host
```

`run.sh` starts the capture server (`/v1/messages`, scripted model), the MCP server (`glossary_lookup`), and the tripwire, then runs `driver.py` under `env -i PATH=/usr/bin:/bin HOME=<fresh> ANTHROPIC_BASE_URL=http://127.0.0.1:<port> ANTHROPIC_AUTH_TOKEN=dummy-capture-token ANTHROPIC_MODEL=big-default ANTHROPIC_SMALL_FAST_MODEL=big-default ANTHROPIC_CUSTOM_HEADERS="traceparent: 00-0af7651916cd43dd8448eb211c80319c-b7ad6b7169203331-01" DISABLE_TELEMETRY=1 CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1 DISABLE_AUTOUPDATER=1 DISABLE_ERROR_REPORTING=1 HTTPS_PROXY=... HTTP_PROXY=... NO_PROXY=...`. The driver passes `--debug-to-stderr` to the CLI and keeps its stderr.

## Output (tails)

Driver exit codes and request counts:

```
== plain   driver exit code: 0   cap-a/capture.jsonl: 1 line
== tool    driver exit code: 0   cap-b/capture.jsonl: 2 lines   cap-b/mcp.jsonl: 8 lines
== shell   driver exit code: 0   cap-c/capture.jsonl: 4 lines
7 model calls in a+b+c; 7 carry the traceparent; models {'big-default'};
paths {('POST', '/v1/messages', (('beta', 'true'),))}; tripwire lines on port 9: 0
```

Variable names the CLI process received (read from `/proc/<pid>/environ`, names only; identical in runs a, b, c):

```
ANTHROPIC_AUTH_TOKEN ANTHROPIC_BASE_URL ANTHROPIC_CUSTOM_HEADERS ANTHROPIC_MODEL
ANTHROPIC_SMALL_FAST_MODEL CLAUDE_AGENT_SDK_VERSION CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC
CLAUDE_CODE_ENTRYPOINT CLAUDE_CODE_SDK_READS_SESSION_STATE DISABLE_AUTOUPDATER
DISABLE_ERROR_REPORTING DISABLE_TELEMETRY HOME HTTPS_PROXY HTTP_PROXY LC_CTYPE NO_PROXY PATH PWD
```

Not set by me: `CLAUDE_AGENT_SDK_VERSION`, `CLAUDE_CODE_ENTRYPOINT`, `CLAUDE_CODE_SDK_READS_SESSION_STATE`, `PWD` (the SDK) and `LC_CTYPE` (the CLI runtime). No session variable leaked.

Control (outside host as base URL; the call must reach the tripwire):

```
driver exit code: 124 (timeout after retries)
{"kind": "tripwire", "first_line": "POST http://example.test:8080/v1/messages?beta=true HTTP/1.1"}   (the same line, 7 tripwire hits in all: one per retry)
```

Process snapshot during run (c), `ps -eo pid,ppid,rss,args`, trimmed:

```
2582 2578 rss=240008kB .../claude_agent_sdk/_bundled/claude --output-format stream-json --verbose --system-prompt  --allowedTools Bash,Read,Write --max-turns 6 --debug-to-stderr --input-format stream-json
2621 2582 rss=3504kB /bin/bash -c source <HOME>/.claude/shell-snapshots/snapshot-bash-....sh ...
2622 2621 rss=1944kB sleep 3
```

## Reading

### Endpoints the CLI called (runs a, b, c; 7 calls)

| Method | Path | Query | Header names | Top-level body fields | Stream | Calls |
| ------ | ---- | ----- | ------------ | --------------------- | ------ | ----- |
| POST | `/v1/messages` | `beta=true` | accept, authorization, content-type, user-agent, x-claude-code-session-id, x-stainless-{arch,lang,os,package-version,retry-count,runtime,runtime-version,timeout}, anthropic-beta, anthropic-dangerous-direct-browser-access, anthropic-version, traceparent, x-app, connection, host, accept-encoding, content-length | `model, messages, system, tools, metadata, max_tokens, thinking, context_management, output_config, stream`; the first call of each run also has `safeguards` | yes, always (`stream: true`) | a: 1, b: 2, c: 4 |

No other path was called: no `count_tokens`, no `/v1/models`, no GET, no HEAD, no call to a second (small, fast) model. Every body had `model: "big-default"`, which is `ANTHROPIC_MODEL` verbatim. `x-api-key` was never sent; auth is `authorization: Bearer <ANTHROPIC_AUTH_TOKEN>` (value redacted in the log). The extra run `tool-nohdr` made 2 more calls to the same endpoint. Calls per run grow with the tool loop: one per model turn, each resending the whole history.

### Header values to handle

- `anthropic-version: 2023-06-01`
- `anthropic-beta: claude-code-20250219,interleaved-thinking-2025-05-14,thinking-token-count-2026-05-13,context-management-2025-06-27,prompt-caching-scope-2026-01-05,mid-conversation-system-2026-04-07,mid-conversation-tool-changes-2026-07-01,effort-2025-11-24,dangerous-tool-use-2026-09-03,thinking-display-updates-2026-08-18,afk-mode-2026-01-31` (identical on all 7 calls)
- `user-agent: claude-cli/2.1.294 (external, sdk-py, agent-sdk/0.2.165)`; `anthropic-dangerous-direct-browser-access: true`; `x-app: cli`; `accept: application/json` even though the body asks for a stream; `accept-encoding: gzip, deflate, br, zstd`.
- `x-claude-code-session-id: <uuid>` is stable across the calls of one run. It can be a session correlation key.

### Body fields a `/v1/messages` route must map, drop, or refuse

| Field | What the CLI sends | Route decision to make |
| ----- | ------------------ | ---------------------- |
| `system` | List of 2 text blocks. Block 1 is a billing marker `x-anthropic-billing-header: cc_version=...; cc_entrypoint=sdk-py;`; block 2 is the agent line with `cache_control: {"type":"ephemeral"}`. | Accept a list of blocks. Join the texts into one chat `system` message. Drop `cache_control`. Consider dropping block 1 (it changes per run, and breaks provider prefix caching). |
| `messages` | `role` `user`, `assistant`, and also **`role: "system"` entries inside `messages`** (beta `mid-conversation-system`): an `# Environment` block (about 8 KB, text blocks or a plain string) and a `<total_tokens>N tokens left</total_tokens>` budget note after each tool result. Content is a block list (`text`, `tool_use`, `tool_result`) or a string. The first user turn has two text blocks (a `<system-reminder>` attribution note, then the prompt). | Accept `system` role in `messages` (strict Anthropic rejects it). Map it to a chat `system` message in place, or fold it into the user turn. Map `tool_use` to `tool_calls`, and `tool_result` to a `tool` message by `tool_use_id`. |
| `tools` | 20 built-in tools with default tools (24 with the MCP tool). Each has only `name`, `description`, `input_schema` (JSON Schema 2020-12 for built-ins). The MCP tool is named `mcp__glossary__glossary_lookup`. No `cache_control`, no `type`, no `defer_loading`. | Map to chat `tools[].function`. Pass `input_schema` as `parameters`. The built-in tool schemas carry `$schema`; some models or providers may refuse it. Total body size is 57-61 KB per call. |
| `tool_choice` | Not sent. | None to map. Default is auto. |
| `stop_sequences`, `temperature`, `top_p`, `top_k` | Not sent. | None to map. |
| `max_tokens` | `32000` | Required in Messages API; map it to `max_tokens`. May exceed a small model's context; clamp or refuse by policy. |
| `thinking` | `{"type":"adaptive","display":"updates"}` | Not a chat-completions concept. Drop, or refuse for non-reasoning models. The reply never has to carry thinking blocks. |
| `output_config` | `{"effort":"high"}` | Drop (or map to a reasoning effort). |
| `context_management` | `{"edits":[{"type":"clear_thinking_20251015","keep":"all"}]}` | Drop. Provider-only beta. |
| `metadata` | `{"user_id": "<JSON string with device_id, account_uuid, session_id>"}` | Drop, or log `session_id`. The string holds a per-device id; do not forward it to a provider. |
| `safeguards` | First call of each run only: `[{"type":"dangerous_tool_use","classifier_context":{...}}]` with the cwd, HOME path, rule roots, and trusted directories. It comes from the auto-permission mode classifier. | Drop. Do not forward (it leaks paths). The CLI logs `[server-classifier] ... server_no_result` and continues locally, so a route that ignores it is fine. |
| `cache_control` | In `system[1]` and in the last `system`-role message block, `{"type":"ephemeral"}`. 2 per call. | Drop (do not refuse), or map to provider caching where one exists. |
| `stream` | Always `true`. | The route must stream SSE. A non-streaming answer to a `stream: true` request was not tested with the CLI. |

Reply side: the capture server answered with `message_start`, `ping`, `content_block_start`, `content_block_delta` (`text_delta`, `input_json_delta`), `content_block_stop`, `message_delta` (with `stop_reason` `end_turn` or `tool_use`), and `message_stop`. The CLI accepted this and finished every run. The `usage` in `message_start` and `message_delta` is read (the CLI reports cost and token totals from it). It did not need `cache_*` usage fields or thinking blocks. Non-streaming JSON and `/v1/messages/count_tokens` were checked only with a direct self-test, since the CLI never asked for them.

### Count tokens and background calls

None happened. No `count_tokens` call, no call with a different model (so `ANTHROPIC_SMALL_FAST_MODEL` was unused), no title or summary call. The runs were short (1 to 4 turns). Long sessions may add compaction or title calls; not tested. `ANTHROPIC_SMALL_FAST_MODEL` is set anyway, so any such call would also name `big-default`.

### traceparent

- Model calls: the header is on 7 of 7 calls, taken from `ANTHROPIC_CUSTOM_HEADERS` (format `Name: value`, one header per line). It is sent as is, the same value on every call, so the CLI does not create child spans.
- MCP calls: **not** inherited. Run `tool-nohdr` (no per-server headers) shows `traceparent` on 0 of 7 MCP requests. With per-server `headers`, it is on every MCP request.
- The SDK can also inject `TRACEPARENT` into the CLI environment from an active OpenTelemetry span (source: `subprocess_cli.py`). Not exercised here.

### MCP header support

Yes. `McpHttpServerConfig` and `McpSSEServerConfig` have `headers: dict[str, str]` (`types.py`). With `{"type":"http","url":...,"headers":{"traceparent":...,"Authorization":"Bearer dummy"}}`, every MCP request carried the `traceparent` and `Authorization: Bearer ...`. Observed MCP calls: `server/discover` (a probe on protocol 2026-07-28), `initialize` (2025-11-25), `notifications/initialized`, `tools/list`, `prompts/list`, `resources/list`, `tools/call`, then a `GET /mcp` for the server stream. A stateless streamable-HTTP server worked. The CLI also exposes `ListMcpResourcesTool`, `ReadMcpResourceTool`, and `ReadMcpResourceDirTool` when an MCP server is present. The `tools/call` body has `_meta` with `claudecode/toolUseId` and `progressToken`.

### HOME writes, spawned processes, files

- Under `HOME` (fresh dir, per run): `.claude.json` (719 B), `.claude/backups/.claude.json.backup.<ms>` (84 B), `.claude/projects/<cwd-slug>/<session>.jsonl` (the transcript, 100-110 KB, holds prompts and tool results), and empty dirs `.claude/session-env/<session>`, `.claude/sessions`, `.claude/shell-snapshots` (a snapshot file exists during a Bash call and is deleted after). With an MCP server: `.cache/claude-cli-nodejs/<cwd-slug>/mcp-logs-<server>/<ts>.jsonl` (3 KB). Writes are atomic (`.claude.json.tmp.<pid>.<hex>` then rename), so the volume must allow rename in the same directory.
- Outside HOME and the cwd: none found (`find -newer` over /tmp, /root, /var, /home, /etc, /opt, /usr/local, /run; hits were other sessions' files, none mentioned the run dirs).
- Processes: one CLI process (about 175-240 MB RSS) as the SDK's child. No Node, no helper daemon. Under Bash, `/bin/bash -c source <snapshot> ...` and the command (`sleep 3`). The MCP server ran outside, as a separate process I started.
- Shell and file tools (run c): `Bash`, `Write`, `Read` worked with `allowed_tools`. Files landed in the session `cwd` (`cwd=<work dir>`): `bash-out.txt` ("from-bash") and `write-out.txt` ("from-write"), both owner root, mode 644. Paths were absolute in the scripted model reply; a relative path resolves against `cwd`.
- **The Bash tool's child gets credentials.** Variable names seen in the `bash` and `sleep` processes include `ANTHROPIC_AUTH_TOKEN`, `ANTHROPIC_BASE_URL`, `ANTHROPIC_CUSTOM_HEADERS`, `CLAUDE_CODE_MESSAGING_TOKEN`, and `CLAUDE_CODE_MESSAGING_SOCKET`. A model-written shell command can read the model credential. In the remote lane the credential given to the CLI must be a scoped, short-lived chassis token, never a provider key (ADR-001 hard requirement 1).

### What the remote-lane image needs

- Node: no. Bash: yes (`/bin/bash`, `sleep`, coreutils for the Bash tool). glibc: yes (the binary is dynamically linked to glibc, so Alpine/musl will not work as is).
- Size: the CLI binary is 242 MB on disk; the SDK wheel install is 243 MB; plan about 175-240 MB RSS per CLI process.
- Writable paths under a read-only root: `HOME` (set it to a scratch volume; about 100-150 KB per short session, grows with the transcript), the session `cwd` (the tools write there), and `/tmp` for Bash. Nothing else was written.
- Egress: with the credential and base URL set, the CLI contacted only the base URL (and the MCP server URLs given). See below.
- Set `DISABLE_AUTOUPDATER=1`, `DISABLE_TELEMETRY=1`, `CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1`, `DISABLE_ERROR_REPORTING=1`. With them on, the CLI made no other outside call (see below). Runs without them were not made, so the note says nothing about the defaults.

### Attempted outside calls

None. All 7 model calls (and 2 more in `tool-nohdr`) reached the capture server. The tripwire on the dead proxy port 9 logged 0 connections in runs a, b, c. Stderr of the CLI has "proxy" only in debug lines (`Cleared proxy agent cache`, the MCP environment dump, a ToolSearch notice). The control run shows the tripwire works: with an outside base URL the CLI sent `POST http://example.test:8080/v1/messages?beta=true` to the proxy port, and the tripwire logged it.

Other findings from the debug log: `ToolSearch` is disabled for a non-first-party base URL (`ENABLE_TOOL_SEARCH` would turn it on, if the proxy forwards `tool_reference` blocks). Fast mode is unavailable in the Agent SDK.

## Limits and open points

- The controls cover 3 short scripted tasks. Real models add thinking blocks, parallel `tool_use` blocks, and long histories; none were tested.
- Error handling was not tested (what the CLI does on a 4xx/5xx from the route, which retries it makes).
- Compaction, title, and small-model calls need a longer session to show up.
- The `safeguards` and `<total_tokens>` fields look version-specific (CLI 2.1.294). Pin the CLI through the SDK version and rerun this capture on each bump.
- The run dirs and raw logs stay in `/tmp/poc06-capture/`; only the scripts are committed (`notes/capture/`), so rerun them to regenerate the logs.
