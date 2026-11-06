# PoC-9: the three machine checks, 2026-11-05

## Why

The PoC-9 doc's "What is left" names three checks on the user's machine: `demo.sh --live`, a debugger attach in the `fake` profile, and the `local` profile with a running LiteLLM. None is an exit criterion. They back the scope item "Docker Compose profiles (`fake`, `local`) ... with a debugger attached to each", and they close items 3 and 4 of [the open items](2026-10-30-open-items.md).

## Setup

- Commit: `e46d05b` (`main`), clean. The debugger check found a bug; the fix is in the same change as this note.
- Machine: macOS 27.0.1, Apple M1 Pro, Docker Desktop 29.9.2, Docker VM memory 8319504384 bytes, Compose 5.6.0, uv 0.10.10.
- Chassis image: `agent-platform/chassis:poc09`, rebuilt from this checkout (`docker build -f packages/chassis/Dockerfile -t agent-platform/chassis:poc09 .`), id `sha256:0920459f64c5…`.
- The agent: `agentctl new demo-agent --kind transformer` (framework `pydanticai`, lane `sidecar`), Compose project `demo-agent`.

## 1. `demo.sh --live`

```bash
time bash pocs/poc-09-agent-mvp-template/demo/demo.sh --live
```

Output, exit 0. The build lines and the MCP tool's schema are trimmed.

```text
6f6a38f chore: scaffold demo-agent with agentctl
28 passed in 26.78s
Success: no issues found in 10 source files
1 passed in 20.47s
3 passed in 0.11s
4 passed in 24.90s
1 passed in 54.44s
--- break the logic: the eval must fail
make: *** [eval] Error 1
OK: the eval caught it
 Container demo-agent-debugpy-1 Exited
 Container demo-agent-workload-fake-1 Healthy
 Container demo-agent-chassis-fake-1 Healthy
{"request_id":"66c253f8…","agent":"demo-agent","agent_version":"0.1.0","output":{"text":"ok"},"metrics":{"input_tokens":10,"output_tokens":5,"attempts":1,"model_route":"fake-route"},"status":"ok",…}
{"id":"chatcmpl-f32230a4…","choices":[{"finish_reason":"stop","index":0,"message":{"content":"ok","role":"assistant"}}],"model":"demo-agent","object":"chat.completion",…}
event: message
data: {"jsonrpc":"2.0","id":2,"result":{"tools":[{…,"name":"demo_agent",…,"title":"Demo Agent"}]}}
 Volume demo-agent-debugpy-1.8.22 Removed
bash pocs/poc-09-agent-mvp-template/demo/demo.sh --live  68.39s user 38.49s system 32% cpu 5:30.97 total
```

Reading: from the host, the three interfaces answer: native `/v1/run` returns `status: ok`, the OpenAI interface returns a `chat.completion`, and MCP `tools/list` names the agent's one tool. The offline part matches [the 2026-10-30 record](../demo/2026-10-30-demo-agent-mvp-offline.md).

## 2. A debugger attach in the `fake` profile

A stdlib DAP client does what VS Code's `.vscode/launch.json` does: `initialize`, `attach` with the file's `pathMappings`, `setBreakpoints`, `configurationDone`. Then it sends one `/v1/run` and waits up to 20 s for a `stopped` event, reads the top frame, sends `continue`, and disconnects. The script is `dap_attach.py PORT LOCAL_ROOT REMOTE_ROOT LOCAL_FILE LINE`. It was kept out of the repo, so its steps are listed here in full.

```bash
cd demo-agent && docker compose -f deploy/compose/docker-compose.yaml --profile fake up -d --wait --build
python3 -I dap_attach.py 5678 $PLATFORM/packages/chassis/src /app/packages/chassis/src \
  $PLATFORM/packages/chassis/src/chassis/server/interfaces/serve.py 222
python3 -I dap_attach.py 5679 $AGENT/src /app/src $AGENT/src/demo_agent/handle.py 99
python3 -I dap_attach.py 5679 $AGENT/src/demo_agent /app/.venv/lib/python3.12/site-packages/demo_agent \
  $AGENT/src/demo_agent/handle.py 99
```

Output (paths shortened to `$PLATFORM` and `$AGENT`):

```text
== chassis, the launch.json mapping
setBreakpoints: [{"verified": true, … "line": 222}]
attached to 127.0.0.1:5678
stopped: reason=breakpoint frame=_request_or_refuse at $PLATFORM/packages/chassis/src/chassis/server/interfaces/serve.py:222
/v1/run status=ok after continue
disconnected cleanly
== workload, the old launch.json mapping (/app/src)
setBreakpoints: [{"verified": false, "id": 0, "message": "Breakpoint in file that does not exist.", … "line": 99}]
attached to 127.0.0.1:5679
NOT HIT: no stop in 20 s; /v1/run status=ok
== workload, the fixed mapping (rendered by agentctl after the fix)
setBreakpoints: [{"verified": true, "id": 2, … "line": 99}]
attached to 127.0.0.1:5679
stopped: reason=breakpoint frame=handle at $AGENT/src/demo_agent/handle.py:99
/v1/run status=ok after continue
disconnected cleanly
```

Reading:

- The chassis debugger works with the template's mapping as it was.
- **The workload mapping was wrong.** The image installs the agent `--no-editable` and copies only `/app/.venv`, so `/app/src` does not exist in the container. A breakpoint in `src/` never bound. The template now maps `src/<package>` to `/app/.venv/lib/python3.12/site-packages/<package>`. The compose README says why. `tests/test_poc09_template_ops.py::test_vscode_attaches_to_the_published_debug_ports` pins it to the Dockerfile's install mode.
- A second attach to the same port works after a clean `disconnect`. The workload's adapter log (`DEBUGPY_LOG_DIR=/tmp` in an override file) shows `Session[2] has ended`, and the chassis took two attaches in a row too. A client that drops the socket before the `disconnect` reply ends the adapter: it was left a zombie process. The port then refuses until the stack is recreated. VS Code waits for the reply.

## 3. The `local` profile with a running LiteLLM

LiteLLM is the platform's pinned image, `ghcr.io/berriai/litellm:v1.103.0@sha256:bd089afd…`, with auth on. Its master key is random per run, set in the shell only, and never written to a file. `big-default` routes to the local Ollama model `nemotron-3-nano:4b` through `http://host.docker.internal:11434/v1`. The LiteLLM config, in full:

```yaml
model_list:
  - model_name: big-default
    litellm_params:
      model: openai/nemotron-3-nano:4b
      api_base: http://host.docker.internal:11434/v1
      api_key: ollama   # Ollama has no auth; a non-empty placeholder
litellm_settings:
  turn_off_message_logging: true
  drop_params: true
general_settings:
  master_key: os.environ/LITELLM_MASTER_KEY
```

```bash
key="sk-drill-$(openssl rand -hex 16)"
docker run -d --name poc09-litellm -p 127.0.0.1:4000:4000 -e LITELLM_MASTER_KEY="$key" \
  -e LITELLM_LOCAL_MODEL_COST_MAP=True -v "$PWD/litellm.local.yaml:/etc/litellm/config.yaml:ro" \
  ghcr.io/berriai/litellm:v1.103.0@sha256:bd089afdcd35b894b14a93f9743cdc8b591f82da1a38dd43a010a7b0c9de5fd7 \
  --config /etc/litellm/config.yaml --port 4000
LITELLM_BASE_URL=http://host.docker.internal:4000/v1 LITELLM_API_KEY="$key" \
  docker compose -f deploy/compose/docker-compose.yaml --profile local up -d --wait --build
# then: the variable names on each container, `docker port`, one /v1/run and one /v1/chat/completions
```

Output, exit 0:

```text
litellm: live
 Container demo-agent-chassis-local-1 Healthy
 Container demo-agent-workload-local-1 Healthy
--- key placement (names only)
chassis-local: LITELLM_API_KEY GPG_KEY
workload-local: GPG_KEY
published: 8080/tcp -> 127.0.0.1:8080
--- calls
/v1/run http=200 9.9s status=ok route=big-default tokens_in=443 tokens_out=119
  output: {"text": "The committee reached a determination.  \nThe proposal is to be deferred."}
/v1/chat/completions http=200 4.8s content="The committee reached a determination.  \nThe proposal is to be deferred."
--- litellm saw (requests to big-default)
2
INFO:     172.17.0.1:56216 - "POST /v1/chat/completions HTTP/1.1" 200 OK
INFO:     172.17.0.1:60776 - "POST /v1/chat/completions HTTP/1.1" 200 OK
```

Reading: the `local` profile runs both containers against a real router with auth on, and a real model answers through both interfaces. Only `chassis-local` holds `LITELLM_API_KEY`. `GPG_KEY` is the Python base image's release-signing fingerprint, not a secret. The `local` profile publishes only the app port, and its debuggers are off, as the template's README says.

Not covered: an API provider behind LiteLLM. Ollama stands in for any OpenAI-compatible backend.
