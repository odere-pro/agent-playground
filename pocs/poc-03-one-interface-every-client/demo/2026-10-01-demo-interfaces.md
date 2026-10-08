# PoC-3 demo: one agent, every client, four engines (sidecar lane, fake variant)

Recorded 2026-10-01T11:02:37Z by deploy/compose/demo-interfaces.sh.

Command, from the repo root, Docker 29.7.2, empty `.env`: `deploy/compose/demo-interfaces.sh`. Exit code 0, last line `result: every call ok`. Everything below "Summary" is the script's output as written, unedited.

## Summary (added by hand after the run)

What was shown:

- **Every client on every engine.** The four sidecar engines (echo-python, echo-pydanticai, echo-langgraph, echo-typescript) were each called through `127.0.0.1:8080` by the OpenAI Python SDK, the Anthropic Python SDK, and `fastmcp.Client` at `/v1/mcp`. That is 12 calls, and each printed `<client>: ok`. Each client got only a base URL, a placeholder key, and `model='echo'` (`clients.py`).
- **LiteLLM's MCP gateway in front of `/v1/mcp`.** On each engine, `fastmcp.Client('http://127.0.0.1:4000/mcp/')` listed the tool `chassis_agent-echo` and called it (4 calls, all `mcp: ok`). LiteLLM reached the chassis at `http://chassis:8080/v1/mcp` over the Compose network.
- **Every call shows up in the router.** After each of the 16 calls, at least one new `token_log status=ok route=big-default ... tags=agent:echo` line appeared, and the script fails any call that leaves none. Glossary calls on the three Python engines left two lines (the tool round, then the answer); the TypeScript `simplify` call left one. Gateway calls also left LiteLLM's own `route=MCP: list_tools` and `route=MCP: chassis_agent-echo` lines.
- **Streaming, OpenAI and Anthropic, `sidecar` lane.** Each streamed call printed each chunk or event as it arrived, with seconds since the call started: OpenAI `chunk` lines ending in `finish_reason=stop`, and Anthropic `message_start` ... `content_block_delta` ... `message_delta stop_reason=end_turn` ... `message_stop`.
- **`GET /manifest` on each engine.** It reported `"lane": "sidecar"` and the native, OpenAI, Anthropic, and MCP interfaces.
- **Ports and keys.** Only `127.0.0.1:8080` (chassis) and `127.0.0.1:4000` (LiteLLM) were published. `fake-model-server 8081/tcp` is the image's `EXPOSE` and is not published. No workload had a key-like variable.

What was not shown, or only in part:

- **Spacing between chunks.** The chunks arrived as separate frames, but within about 2 ms of each other (for example `+0.090s` to `+0.092s`). The fake model server sends its whole reply at once and has no delay setting. So the record shows a streamed protocol, not tokens arriving spread over time; that needs a real model, which was not run (this script has no `--local` option yet).
- **"Every call streams" does not hold for MCP.** The MCP tool returns one result (`result (one message: the MCP tool does not stream)`): the generated tool calls `/v1/run` with `stream` false. MCP progress notifications are not used.
- **Lanes.** Only the `sidecar` lane ran. The `inprocess` lane was not demoed in Compose.
- **MCP client.** The plan names Claude Code or Claude Desktop. The demo used `fastmcp.Client` over streamable HTTP; no desktop client was connected.
- **Real models and LiteLLM with auth on.** These were not run: there is no provider key, and none was invented. The gateway entry is only in `litellm/config.yaml` (fake). `config.local.yaml` has none. With auth on, the server needs a grant per key or team instead of `allow_all_keys`.
- **No key in any log.** The check had nothing to look for: `LITELLM_API_KEY` was empty.
- **An earlier run was discarded.** A first run at about 10:55Z was cut off after the python engine, because a second copy of the demo (a pytest run of the `network` test) started at the same time and took the stack down. All of the python calls in that run had passed. The record below is the clean second run.

## Workload python: start the stack with the workload-python profile (no manual steps)

$ docker compose -f docker-compose.yaml -f docker-compose.sidecar.yaml --profile python up -d --wait --quiet-pull
 Container poc02-fake-model-server-1 Healthy 
 Container poc02-litellm-1 Healthy 
 Container poc02-fake-model-server-1 Healthy 
 Container poc02-litellm-1 Healthy 
 Container poc02-workload-python-1 Healthy 
 Container poc02-chassis-1 Healthy 

## Published ports (only 127.0.0.1:8080 and 127.0.0.1:4000 expected)

$ docker compose -f docker-compose.yaml -f docker-compose.sidecar.yaml --profile python ps --format {{.Service}} {{.Ports}}
chassis 127.0.0.1:8080->8080/tcp
fake-model-server 8081/tcp
litellm 127.0.0.1:4000->4000/tcp
workload-python 
$ curl -s http://127.0.0.1:8080/ready
{"status":"ready"}
$ docker compose -f docker-compose.yaml -f docker-compose.sidecar.yaml exec -T chassis python -c import json,urllib.request; c=json.load(urllib.request.urlopen('http://127.0.0.1:9000/.well-known/agent-card.json', timeout=2)); print('engine:', c['name'], c.get('version'))
engine: echo-python 0.1.0

## Workload python: OpenAI Python SDK, streamed

$ uv run --no-sync python pocs/poc-03-one-interface-every-client/demo/clients.py http://127.0.0.1:8080 echo --client openai --text glossary: what does SLM mean? Say it in plain words.
### openai
OpenAI(base_url='http://127.0.0.1:8080/v1', api_key=<placeholder>)
chat.completions.create(model='echo', stream=True)
  + 1.219s chunk ''
  + 1.220s chunk 'From '
  + 1.220s chunk 'the '
  + 1.220s chunk 'glossary: '
  + 1.220s chunk 'SLM '
  + 1.220s chunk 'means '
  + 1.220s chunk 'small '
  + 1.220s chunk 'language '
  + 1.220s chunk 'model.'
  + 1.222s chunk '' finish_reason=stop
text: 'From the glossary: SLM means small language model.'
openai: ok

router (LiteLLM token_log), new lines since this call:
token_log status=ok route=big-default model=fake-model prompt_tokens=10 completion_tokens=5 total_tokens=15 cost_usd=0.000000 tags=agent:echo
token_log status=ok route=big-default model=fake-model prompt_tokens=10 completion_tokens=5 total_tokens=15 cost_usd=0.000000 tags=agent:echo

## Workload python: Anthropic Python SDK, streamed

$ uv run --no-sync python pocs/poc-03-one-interface-every-client/demo/clients.py http://127.0.0.1:8080 echo --client anthropic --text glossary: what does SLM mean? Say it in plain words.
### anthropic
Anthropic(base_url='http://127.0.0.1:8080', api_key=<placeholder>)
messages.stream(model='echo', max_tokens=256)
  + 0.171s event message_start
  + 0.175s event content_block_start
  + 0.175s event content_block_delta 'From '
  + 0.175s event content_block_delta 'the '
  + 0.175s event content_block_delta 'glossary: '
  + 0.175s event content_block_delta 'SLM '
  + 0.175s event content_block_delta 'means '
  + 0.175s event content_block_delta 'small '
  + 0.175s event content_block_delta 'language '
  + 0.175s event content_block_delta 'model.'
  + 0.183s event content_block_stop
  + 0.183s event message_delta stop_reason=end_turn
  + 0.184s event message_stop
text: 'From the glossary: SLM means small language model.'
anthropic: ok

router (LiteLLM token_log), new lines since this call:
token_log status=ok route=big-default model=fake-model prompt_tokens=10 completion_tokens=5 total_tokens=15 cost_usd=0.000000 tags=agent:echo
token_log status=ok route=big-default model=fake-model prompt_tokens=10 completion_tokens=5 total_tokens=15 cost_usd=0.000000 tags=agent:echo

## Workload python: MCP client, direct at /v1/mcp

$ uv run --no-sync python pocs/poc-03-one-interface-every-client/demo/clients.py http://127.0.0.1:8080 echo --client mcp --text glossary: what does SLM mean? Say it in plain words.
### mcp
fastmcp.Client('http://127.0.0.1:8080/v1/mcp')  # streamable HTTP
  tool 'echo' input properties ['agent', 'agent_version', 'budget', 'context_ref', 'idempotency_key', 'input', 'request_id', 'stream', 'trace_id']
call_tool('echo', {'input': {'text': ...}})
  + 0.166s result (one message: the MCP tool does not stream)
  {"request_id": "329183d005fc40e1ac382bd8fcbe4678", "trace_id": "b6cb8c22fb1e414ba7b7c4b677e25ffb", "idempotency_key": "eaeedcdc438645ec8c981db0c72c2c28", "agent": "echo", "agent_version": "0.0.1", "output": {"text": "From the glossary: SLM means small language model.", "tool_calls": [{"call_id": "call_b2c6b7f9", "name": "glossary_lookup", "arguments": {"term": "SLM"}, "result": {"term": "SLM", "definition": "A small language model: a model small enough to run cheaply on one GPU or a CPU."}}]}, "metrics": {"input_tokens": 20, "output_tokens": 10, "attempts": 1, "model_route": "big-default"}, "status": "ok", "versions": {"chassis": "0.1.0", "config": "37fe10aa8f0d", "prompt": "simplifier-v1", "model_route": "big-default"}, "context_ref": null}
mcp: ok

router (LiteLLM token_log), new lines since this call:
token_log status=ok route=big-default model=fake-model prompt_tokens=10 completion_tokens=5 total_tokens=15 cost_usd=0.000000 tags=agent:echo
token_log status=ok route=big-default model=fake-model prompt_tokens=10 completion_tokens=5 total_tokens=15 cost_usd=0.000000 tags=agent:echo

## Workload python: MCP client through LiteLLM's MCP gateway

$ uv run --no-sync python pocs/poc-03-one-interface-every-client/demo/clients.py http://127.0.0.1:8080 echo --client mcp --mcp-url http://127.0.0.1:4000/mcp/ --text glossary: what does SLM mean? Say it in plain words.
### mcp
fastmcp.Client('http://127.0.0.1:4000/mcp/')  # streamable HTTP
  tool 'chassis_agent-echo' input properties ['agent', 'agent_version', 'budget', 'context_ref', 'idempotency_key', 'input', 'request_id', 'stream', 'trace_id']
call_tool('chassis_agent-echo', {'input': {'text': ...}})
  + 0.262s result (one message: the MCP tool does not stream)
  {"request_id": "85da10ca30df44d0886ce2255af00a0f", "trace_id": "3bddf213e582465bb63e00c8d3741a73", "idempotency_key": "706a9e7f878e4d939b62c5b2b945cc04", "agent": "echo", "agent_version": "0.0.1", "output": {"text": "From the glossary: SLM means small language model.", "tool_calls": [{"call_id": "call_e8916560", "name": "glossary_lookup", "arguments": {"term": "SLM"}, "result": {"term": "SLM", "definition": "A small language model: a model small enough to run cheaply on one GPU or a CPU."}}]}, "metrics": {"input_tokens": 20, "output_tokens": 10, "attempts": 1, "model_route": "big-default"}, "status": "ok", "versions": {"chassis": "0.1.0", "config": "37fe10aa8f0d", "prompt": "simplifier-v1", "model_route": "big-default"}, "context_ref": null}
mcp: ok

router (LiteLLM token_log), new lines since this call:
token_log status=ok route=MCP: list_tools model=MCP: list_tools prompt_tokens=0 completion_tokens=0 total_tokens=0 cost_usd=0.000000 tags=
token_log status=ok route=big-default model=fake-model prompt_tokens=10 completion_tokens=5 total_tokens=15 cost_usd=0.000000 tags=agent:echo
token_log status=ok route=big-default model=fake-model prompt_tokens=10 completion_tokens=5 total_tokens=15 cost_usd=0.000000 tags=agent:echo
token_log status=ok route=MCP: chassis_agent-echo model=MCP: chassis_agent-echo prompt_tokens=0 completion_tokens=0 total_tokens=0 cost_usd=0.000000 tags=

## Workload python: GET /manifest

### manifest
GET http://127.0.0.1:8080/manifest
{
 "manifest_version": "0",
 "agent": {
  "name": "echo",
  "version": "0.0.1"
 },
 "versions": {
  "chassis": "0.1.0",
  "config": "37fe10aa8f0d",
  "prompt": "simplifier-v1",
  "model_route": "big-default"
 },
 "lane": "sidecar",
 "event_schema_versions": [
  "0"
 ],
 "interfaces": [
  {
   "name": "native",
   "method": "POST",
   "path": "/v1/run",
   "operation_id": "run",
   "streaming": true,
   "model": null
  },
  {
   "name": "openai",
   "method": "POST",
   "path": "/v1/chat/completions",
   "operation_id": "chat_completions",
   "streaming": true,
   "model": "echo"
  },
  {
   "name": "anthropic",
   "method": "POST",
   "path": "/v1/messages",
   "operation_id": "messages",
   "streaming": true,
   "model": "echo"
  },
  {
   "name": "mcp",
   "path": "/v1/mcp",
   "transport": "streamable-http",
   "streaming": false,
   "tools": [
    "echo"
   ]
  }
 ],
 "openapi": {
  "path": "/openapi.json",
  "version": "3.1.0",
  "sha256": "f0281fbc1dd8b704c86dbfd58118a19d8d520c929feb6ca53e35c266c6a2485f"
 }
}
manifest: ok

## Workload python: key-like variables in the workload container (names only; none expected)

key-like variables in the workload: none

## Workload pydanticai: swap workload-python for workload-pydanticai, then recreate the chassis next to it

$ docker compose -f docker-compose.yaml -f docker-compose.sidecar.yaml --profile python rm -sf workload-python
$ docker compose -f docker-compose.yaml -f docker-compose.sidecar.yaml --profile pydanticai up -d --wait --no-deps --force-recreate chassis workload-pydanticai
 Container poc02-workload-pydanticai-1 Healthy 
 Container poc02-chassis-1 Healthy 
$ curl -s http://127.0.0.1:8080/ready
{"status":"ready"}
$ docker compose -f docker-compose.yaml -f docker-compose.sidecar.yaml exec -T chassis python -c import json,urllib.request; c=json.load(urllib.request.urlopen('http://127.0.0.1:9000/.well-known/agent-card.json', timeout=2)); print('engine:', c['name'], c.get('version'))
engine: echo-pydanticai 0.1.0

## Workload pydanticai: OpenAI Python SDK, streamed

$ uv run --no-sync python pocs/poc-03-one-interface-every-client/demo/clients.py http://127.0.0.1:8080 echo --client openai --text glossary: what does SLM mean? Say it in plain words.
### openai
OpenAI(base_url='http://127.0.0.1:8080/v1', api_key=<placeholder>)
chat.completions.create(model='echo', stream=True)
  + 0.527s chunk ''
  + 0.527s chunk 'From '
  + 0.527s chunk 'the '
  + 0.527s chunk 'glossary: '
  + 0.527s chunk 'SLM '
  + 0.527s chunk 'means '
  + 0.527s chunk 'small '
  + 0.528s chunk 'language '
  + 0.528s chunk 'model.'
  + 0.531s chunk '' finish_reason=stop
text: 'From the glossary: SLM means small language model.'
openai: ok

router (LiteLLM token_log), new lines since this call:
token_log status=ok route=big-default model=fake-model prompt_tokens=10 completion_tokens=5 total_tokens=15 cost_usd=0.000000 tags=agent:echo
token_log status=ok route=big-default model=fake-model prompt_tokens=10 completion_tokens=5 total_tokens=15 cost_usd=0.000000 tags=agent:echo

## Workload pydanticai: Anthropic Python SDK, streamed

$ uv run --no-sync python pocs/poc-03-one-interface-every-client/demo/clients.py http://127.0.0.1:8080 echo --client anthropic --text glossary: what does SLM mean? Say it in plain words.
### anthropic
Anthropic(base_url='http://127.0.0.1:8080', api_key=<placeholder>)
messages.stream(model='echo', max_tokens=256)
  + 0.281s event message_start
  + 0.338s event content_block_start
  + 0.338s event content_block_delta 'From '
  + 0.338s event content_block_delta 'the '
  + 0.339s event content_block_delta 'glossary: '
  + 0.339s event content_block_delta 'SLM '
  + 0.339s event content_block_delta 'means '
  + 0.339s event content_block_delta 'small '
  + 0.339s event content_block_delta 'language '
  + 0.339s event content_block_delta 'model.'
  + 0.341s event content_block_stop
  + 0.341s event message_delta stop_reason=end_turn
  + 0.342s event message_stop
text: 'From the glossary: SLM means small language model.'
anthropic: ok

router (LiteLLM token_log), new lines since this call:
token_log status=ok route=big-default model=fake-model prompt_tokens=10 completion_tokens=5 total_tokens=15 cost_usd=0.000000 tags=agent:echo
token_log status=ok route=big-default model=fake-model prompt_tokens=10 completion_tokens=5 total_tokens=15 cost_usd=0.000000 tags=agent:echo

## Workload pydanticai: MCP client, direct at /v1/mcp

$ uv run --no-sync python pocs/poc-03-one-interface-every-client/demo/clients.py http://127.0.0.1:8080 echo --client mcp --text glossary: what does SLM mean? Say it in plain words.
### mcp
fastmcp.Client('http://127.0.0.1:8080/v1/mcp')  # streamable HTTP
  tool 'echo' input properties ['agent', 'agent_version', 'budget', 'context_ref', 'idempotency_key', 'input', 'request_id', 'stream', 'trace_id']
call_tool('echo', {'input': {'text': ...}})
  + 0.114s result (one message: the MCP tool does not stream)
  {"request_id": "6aa17150e42a483f8ed7e3477b0cb095", "trace_id": "57ee641f39114c04bc4d1654cc36fa9d", "idempotency_key": "a85d40191c02430f975511c578207941", "agent": "echo", "agent_version": "0.0.1", "output": {"text": "From the glossary: SLM means small language model.", "tool_calls": [{"call_id": "call_2ae9f6a6", "name": "glossary_lookup", "arguments": {"term": "SLM"}, "result": {"term": "SLM", "definition": "A small language model: a model small enough to run cheaply on one GPU or a CPU."}}]}, "metrics": {"input_tokens": 20, "output_tokens": 10, "attempts": 1, "model_route": "big-default"}, "status": "ok", "versions": {"chassis": "0.1.0", "config": "37fe10aa8f0d", "prompt": "simplifier-v1", "model_route": "big-default"}, "context_ref": null}
mcp: ok

router (LiteLLM token_log), new lines since this call:
token_log status=ok route=big-default model=fake-model prompt_tokens=10 completion_tokens=5 total_tokens=15 cost_usd=0.000000 tags=agent:echo
token_log status=ok route=big-default model=fake-model prompt_tokens=10 completion_tokens=5 total_tokens=15 cost_usd=0.000000 tags=agent:echo

## Workload pydanticai: MCP client through LiteLLM's MCP gateway

$ uv run --no-sync python pocs/poc-03-one-interface-every-client/demo/clients.py http://127.0.0.1:8080 echo --client mcp --mcp-url http://127.0.0.1:4000/mcp/ --text glossary: what does SLM mean? Say it in plain words.
### mcp
fastmcp.Client('http://127.0.0.1:4000/mcp/')  # streamable HTTP
  tool 'chassis_agent-echo' input properties ['agent', 'agent_version', 'budget', 'context_ref', 'idempotency_key', 'input', 'request_id', 'stream', 'trace_id']
call_tool('chassis_agent-echo', {'input': {'text': ...}})
  + 0.113s result (one message: the MCP tool does not stream)
  {"request_id": "45c3f2da774442cc8c392c164470394b", "trace_id": "202e2f3b6b5840ddac2d3b158a09462f", "idempotency_key": "ed4e8b2ebc3443529a327468abe25ccf", "agent": "echo", "agent_version": "0.0.1", "output": {"text": "From the glossary: SLM means small language model.", "tool_calls": [{"call_id": "call_e29ded39", "name": "glossary_lookup", "arguments": {"term": "SLM"}, "result": {"term": "SLM", "definition": "A small language model: a model small enough to run cheaply on one GPU or a CPU."}}]}, "metrics": {"input_tokens": 20, "output_tokens": 10, "attempts": 1, "model_route": "big-default"}, "status": "ok", "versions": {"chassis": "0.1.0", "config": "37fe10aa8f0d", "prompt": "simplifier-v1", "model_route": "big-default"}, "context_ref": null}
mcp: ok

router (LiteLLM token_log), new lines since this call:
token_log status=ok route=MCP: list_tools model=MCP: list_tools prompt_tokens=0 completion_tokens=0 total_tokens=0 cost_usd=0.000000 tags=
token_log status=ok route=big-default model=fake-model prompt_tokens=10 completion_tokens=5 total_tokens=15 cost_usd=0.000000 tags=agent:echo
token_log status=ok route=big-default model=fake-model prompt_tokens=10 completion_tokens=5 total_tokens=15 cost_usd=0.000000 tags=agent:echo
token_log status=ok route=MCP: chassis_agent-echo model=MCP: chassis_agent-echo prompt_tokens=0 completion_tokens=0 total_tokens=0 cost_usd=0.000000 tags=

## Workload pydanticai: GET /manifest

### manifest
GET http://127.0.0.1:8080/manifest
{
 "manifest_version": "0",
 "agent": {
  "name": "echo",
  "version": "0.0.1"
 },
 "versions": {
  "chassis": "0.1.0",
  "config": "37fe10aa8f0d",
  "prompt": "simplifier-v1",
  "model_route": "big-default"
 },
 "lane": "sidecar",
 "event_schema_versions": [
  "0"
 ],
 "interfaces": [
  {
   "name": "native",
   "method": "POST",
   "path": "/v1/run",
   "operation_id": "run",
   "streaming": true,
   "model": null
  },
  {
   "name": "openai",
   "method": "POST",
   "path": "/v1/chat/completions",
   "operation_id": "chat_completions",
   "streaming": true,
   "model": "echo"
  },
  {
   "name": "anthropic",
   "method": "POST",
   "path": "/v1/messages",
   "operation_id": "messages",
   "streaming": true,
   "model": "echo"
  },
  {
   "name": "mcp",
   "path": "/v1/mcp",
   "transport": "streamable-http",
   "streaming": false,
   "tools": [
    "echo"
   ]
  }
 ],
 "openapi": {
  "path": "/openapi.json",
  "version": "3.1.0",
  "sha256": "f0281fbc1dd8b704c86dbfd58118a19d8d520c929feb6ca53e35c266c6a2485f"
 }
}
manifest: ok

## Workload pydanticai: key-like variables in the workload container (names only; none expected)

key-like variables in the workload: none

## Workload langgraph: swap workload-pydanticai for workload-langgraph, then recreate the chassis next to it

$ docker compose -f docker-compose.yaml -f docker-compose.sidecar.yaml --profile pydanticai rm -sf workload-pydanticai
$ docker compose -f docker-compose.yaml -f docker-compose.sidecar.yaml --profile langgraph up -d --wait --no-deps --force-recreate chassis workload-langgraph
 Container poc02-chassis-1 Healthy 
 Container poc02-workload-langgraph-1 Healthy 
$ curl -s http://127.0.0.1:8080/ready
{"status":"ready"}
$ docker compose -f docker-compose.yaml -f docker-compose.sidecar.yaml exec -T chassis python -c import json,urllib.request; c=json.load(urllib.request.urlopen('http://127.0.0.1:9000/.well-known/agent-card.json', timeout=2)); print('engine:', c['name'], c.get('version'))
engine: echo-langgraph 0.1.0

## Workload langgraph: OpenAI Python SDK, streamed

$ uv run --no-sync python pocs/poc-03-one-interface-every-client/demo/clients.py http://127.0.0.1:8080 echo --client openai --text glossary: what does SLM mean? Say it in plain words.
### openai
OpenAI(base_url='http://127.0.0.1:8080/v1', api_key=<placeholder>)
chat.completions.create(model='echo', stream=True)
  + 0.288s chunk ''
  + 0.288s chunk 'From '
  + 0.288s chunk 'the '
  + 0.288s chunk 'glossary: '
  + 0.288s chunk 'SLM '
  + 0.288s chunk 'means '
  + 0.288s chunk 'small '
  + 0.288s chunk 'language '
  + 0.288s chunk 'model.'
  + 0.289s chunk '' finish_reason=stop
text: 'From the glossary: SLM means small language model.'
openai: ok

router (LiteLLM token_log), new lines since this call:
token_log status=ok route=big-default model=fake-model prompt_tokens=10 completion_tokens=5 total_tokens=15 cost_usd=0.000000 tags=agent:echo
token_log status=ok route=big-default model=fake-model prompt_tokens=10 completion_tokens=5 total_tokens=15 cost_usd=0.000000 tags=agent:echo

## Workload langgraph: Anthropic Python SDK, streamed

$ uv run --no-sync python pocs/poc-03-one-interface-every-client/demo/clients.py http://127.0.0.1:8080 echo --client anthropic --text glossary: what does SLM mean? Say it in plain words.
### anthropic
Anthropic(base_url='http://127.0.0.1:8080', api_key=<placeholder>)
messages.stream(model='echo', max_tokens=256)
  + 0.082s event message_start
  + 0.085s event content_block_start
  + 0.086s event content_block_delta 'From '
  + 0.086s event content_block_delta 'the '
  + 0.086s event content_block_delta 'glossary: '
  + 0.086s event content_block_delta 'SLM '
  + 0.086s event content_block_delta 'means '
  + 0.086s event content_block_delta 'small '
  + 0.086s event content_block_delta 'language '
  + 0.086s event content_block_delta 'model.'
  + 0.089s event content_block_stop
  + 0.089s event message_delta stop_reason=end_turn
  + 0.089s event message_stop
text: 'From the glossary: SLM means small language model.'
anthropic: ok

router (LiteLLM token_log), new lines since this call:
token_log status=ok route=big-default model=fake-model prompt_tokens=10 completion_tokens=5 total_tokens=15 cost_usd=0.000000 tags=agent:echo
token_log status=ok route=big-default model=fake-model prompt_tokens=10 completion_tokens=5 total_tokens=15 cost_usd=0.000000 tags=agent:echo

## Workload langgraph: MCP client, direct at /v1/mcp

$ uv run --no-sync python pocs/poc-03-one-interface-every-client/demo/clients.py http://127.0.0.1:8080 echo --client mcp --text glossary: what does SLM mean? Say it in plain words.
### mcp
fastmcp.Client('http://127.0.0.1:8080/v1/mcp')  # streamable HTTP
  tool 'echo' input properties ['agent', 'agent_version', 'budget', 'context_ref', 'idempotency_key', 'input', 'request_id', 'stream', 'trace_id']
call_tool('echo', {'input': {'text': ...}})
  + 0.095s result (one message: the MCP tool does not stream)
  {"request_id": "50f27a5129f644ac8629dc31a2b7744d", "trace_id": "c3dc0b72a3dd488eb27f57be4195749a", "idempotency_key": "09d3c79a0a80441eba2dc52dc1c19daa", "agent": "echo", "agent_version": "0.0.1", "output": {"text": "From the glossary: SLM means small language model.", "tool_calls": [{"call_id": "call_2d8376ba", "name": "glossary_lookup", "arguments": {"term": "SLM"}, "result": {"term": "SLM", "definition": "A small language model: a model small enough to run cheaply on one GPU or a CPU."}}]}, "metrics": {"input_tokens": 20, "output_tokens": 10, "attempts": 1, "model_route": "big-default"}, "status": "ok", "versions": {"chassis": "0.1.0", "config": "37fe10aa8f0d", "prompt": "simplifier-v1", "model_route": "big-default"}, "context_ref": null}
mcp: ok

router (LiteLLM token_log), new lines since this call:
token_log status=ok route=big-default model=fake-model prompt_tokens=10 completion_tokens=5 total_tokens=15 cost_usd=0.000000 tags=agent:echo
token_log status=ok route=big-default model=fake-model prompt_tokens=10 completion_tokens=5 total_tokens=15 cost_usd=0.000000 tags=agent:echo

## Workload langgraph: MCP client through LiteLLM's MCP gateway

$ uv run --no-sync python pocs/poc-03-one-interface-every-client/demo/clients.py http://127.0.0.1:8080 echo --client mcp --mcp-url http://127.0.0.1:4000/mcp/ --text glossary: what does SLM mean? Say it in plain words.
### mcp
fastmcp.Client('http://127.0.0.1:4000/mcp/')  # streamable HTTP
  tool 'chassis_agent-echo' input properties ['agent', 'agent_version', 'budget', 'context_ref', 'idempotency_key', 'input', 'request_id', 'stream', 'trace_id']
call_tool('chassis_agent-echo', {'input': {'text': ...}})
  + 0.100s result (one message: the MCP tool does not stream)
  {"request_id": "53edcfe3a461431394afd037ea8612ea", "trace_id": "657ff15729ac40188a5507201b4d3b7c", "idempotency_key": "7d71bbaf59c64f6aaa70396ed35a6691", "agent": "echo", "agent_version": "0.0.1", "output": {"text": "From the glossary: SLM means small language model.", "tool_calls": [{"call_id": "call_5aaaede9", "name": "glossary_lookup", "arguments": {"term": "SLM"}, "result": {"term": "SLM", "definition": "A small language model: a model small enough to run cheaply on one GPU or a CPU."}}]}, "metrics": {"input_tokens": 20, "output_tokens": 10, "attempts": 1, "model_route": "big-default"}, "status": "ok", "versions": {"chassis": "0.1.0", "config": "37fe10aa8f0d", "prompt": "simplifier-v1", "model_route": "big-default"}, "context_ref": null}
mcp: ok

router (LiteLLM token_log), new lines since this call:
token_log status=ok route=MCP: list_tools model=MCP: list_tools prompt_tokens=0 completion_tokens=0 total_tokens=0 cost_usd=0.000000 tags=
token_log status=ok route=big-default model=fake-model prompt_tokens=10 completion_tokens=5 total_tokens=15 cost_usd=0.000000 tags=agent:echo
token_log status=ok route=big-default model=fake-model prompt_tokens=10 completion_tokens=5 total_tokens=15 cost_usd=0.000000 tags=agent:echo
token_log status=ok route=MCP: chassis_agent-echo model=MCP: chassis_agent-echo prompt_tokens=0 completion_tokens=0 total_tokens=0 cost_usd=0.000000 tags=

## Workload langgraph: GET /manifest

### manifest
GET http://127.0.0.1:8080/manifest
{
 "manifest_version": "0",
 "agent": {
  "name": "echo",
  "version": "0.0.1"
 },
 "versions": {
  "chassis": "0.1.0",
  "config": "37fe10aa8f0d",
  "prompt": "simplifier-v1",
  "model_route": "big-default"
 },
 "lane": "sidecar",
 "event_schema_versions": [
  "0"
 ],
 "interfaces": [
  {
   "name": "native",
   "method": "POST",
   "path": "/v1/run",
   "operation_id": "run",
   "streaming": true,
   "model": null
  },
  {
   "name": "openai",
   "method": "POST",
   "path": "/v1/chat/completions",
   "operation_id": "chat_completions",
   "streaming": true,
   "model": "echo"
  },
  {
   "name": "anthropic",
   "method": "POST",
   "path": "/v1/messages",
   "operation_id": "messages",
   "streaming": true,
   "model": "echo"
  },
  {
   "name": "mcp",
   "path": "/v1/mcp",
   "transport": "streamable-http",
   "streaming": false,
   "tools": [
    "echo"
   ]
  }
 ],
 "openapi": {
  "path": "/openapi.json",
  "version": "3.1.0",
  "sha256": "f0281fbc1dd8b704c86dbfd58118a19d8d520c929feb6ca53e35c266c6a2485f"
 }
}
manifest: ok

## Workload langgraph: key-like variables in the workload container (names only; none expected)

key-like variables in the workload: none

## Workload typescript: swap workload-langgraph for workload-typescript, then recreate the chassis next to it

$ docker compose -f docker-compose.yaml -f docker-compose.sidecar.yaml --profile langgraph rm -sf workload-langgraph
$ docker compose -f docker-compose.yaml -f docker-compose.sidecar.yaml --profile typescript up -d --wait --no-deps --force-recreate chassis workload-typescript
 Container poc02-chassis-1 Healthy 
 Container poc02-workload-typescript-1 Healthy 
$ curl -s http://127.0.0.1:8080/ready
{"status":"ready"}
$ docker compose -f docker-compose.yaml -f docker-compose.sidecar.yaml exec -T chassis python -c import json,urllib.request; c=json.load(urllib.request.urlopen('http://127.0.0.1:9000/.well-known/agent-card.json', timeout=2)); print('engine:', c['name'], c.get('version'))
engine: echo-typescript 0.1.0

## Workload typescript: OpenAI Python SDK, streamed

$ uv run --no-sync python pocs/poc-03-one-interface-every-client/demo/clients.py http://127.0.0.1:8080 echo --client openai --text simplify: the quick brown fox jumps over the lazy dog.
### openai
OpenAI(base_url='http://127.0.0.1:8080/v1', api_key=<placeholder>)
chat.completions.create(model='echo', stream=True)
  + 0.090s chunk ''
  + 0.090s chunk 'Plain '
  + 0.090s chunk 'words. '
  + 0.090s chunk 'Short '
  + 0.090s chunk 'sentences. '
  + 0.090s chunk 'Same '
  + 0.090s chunk 'facts.'
  + 0.092s chunk '' finish_reason=stop
text: 'Plain words. Short sentences. Same facts.'
openai: ok

router (LiteLLM token_log), new lines since this call:
token_log status=ok route=big-default model=fake-model prompt_tokens=42 completion_tokens=9 total_tokens=51 cost_usd=0.000000 tags=agent:echo

## Workload typescript: Anthropic Python SDK, streamed

$ uv run --no-sync python pocs/poc-03-one-interface-every-client/demo/clients.py http://127.0.0.1:8080 echo --client anthropic --text simplify: the quick brown fox jumps over the lazy dog.
### anthropic
Anthropic(base_url='http://127.0.0.1:8080', api_key=<placeholder>)
messages.stream(model='echo', max_tokens=256)
  + 0.043s event message_start
  + 0.046s event content_block_start
  + 0.046s event content_block_delta 'Plain '
  + 0.046s event content_block_delta 'words. '
  + 0.047s event content_block_delta 'Short '
  + 0.047s event content_block_delta 'sentences. '
  + 0.047s event content_block_delta 'Same '
  + 0.047s event content_block_delta 'facts.'
  + 0.049s event content_block_stop
  + 0.049s event message_delta stop_reason=end_turn
  + 0.049s event message_stop
text: 'Plain words. Short sentences. Same facts.'
anthropic: ok

router (LiteLLM token_log), new lines since this call:
token_log status=ok route=big-default model=fake-model prompt_tokens=42 completion_tokens=9 total_tokens=51 cost_usd=0.000000 tags=agent:echo

## Workload typescript: MCP client, direct at /v1/mcp

$ uv run --no-sync python pocs/poc-03-one-interface-every-client/demo/clients.py http://127.0.0.1:8080 echo --client mcp --text simplify: the quick brown fox jumps over the lazy dog.
### mcp
fastmcp.Client('http://127.0.0.1:8080/v1/mcp')  # streamable HTTP
  tool 'echo' input properties ['agent', 'agent_version', 'budget', 'context_ref', 'idempotency_key', 'input', 'request_id', 'stream', 'trace_id']
call_tool('echo', {'input': {'text': ...}})
  + 0.108s result (one message: the MCP tool does not stream)
  {"request_id": "3b8697689fd1413dab9b15806a7df42a", "trace_id": "481dc804ff5b4295bb271c87315ce53e", "idempotency_key": "6bb5b0f7462341879056eebd0da64421", "agent": "echo", "agent_version": "0.0.1", "output": {"text": "Plain words. Short sentences. Same facts."}, "metrics": {"input_tokens": 42, "output_tokens": 9, "attempts": 1, "model_route": "big-default"}, "status": "ok", "versions": {"chassis": "0.1.0", "config": "37fe10aa8f0d", "prompt": "simplifier-v1", "model_route": "big-default"}, "context_ref": null}
mcp: ok

router (LiteLLM token_log), new lines since this call:
token_log status=ok route=big-default model=fake-model prompt_tokens=42 completion_tokens=9 total_tokens=51 cost_usd=0.000000 tags=agent:echo

## Workload typescript: MCP client through LiteLLM's MCP gateway

$ uv run --no-sync python pocs/poc-03-one-interface-every-client/demo/clients.py http://127.0.0.1:8080 echo --client mcp --mcp-url http://127.0.0.1:4000/mcp/ --text simplify: the quick brown fox jumps over the lazy dog.
### mcp
fastmcp.Client('http://127.0.0.1:4000/mcp/')  # streamable HTTP
  tool 'chassis_agent-echo' input properties ['agent', 'agent_version', 'budget', 'context_ref', 'idempotency_key', 'input', 'request_id', 'stream', 'trace_id']
call_tool('chassis_agent-echo', {'input': {'text': ...}})
  + 0.123s result (one message: the MCP tool does not stream)
  {"request_id": "ab8324327e294d948a9299f6ad119e49", "trace_id": "07a3c0ac2c1546638592faf40158355e", "idempotency_key": "b1ea8d09747e4cf4afc6e6f4e3887b8e", "agent": "echo", "agent_version": "0.0.1", "output": {"text": "Plain words. Short sentences. Same facts."}, "metrics": {"input_tokens": 42, "output_tokens": 9, "attempts": 1, "model_route": "big-default"}, "status": "ok", "versions": {"chassis": "0.1.0", "config": "37fe10aa8f0d", "prompt": "simplifier-v1", "model_route": "big-default"}, "context_ref": null}
mcp: ok

router (LiteLLM token_log), new lines since this call:
token_log status=ok route=MCP: list_tools model=MCP: list_tools prompt_tokens=0 completion_tokens=0 total_tokens=0 cost_usd=0.000000 tags=
token_log status=ok route=big-default model=fake-model prompt_tokens=42 completion_tokens=9 total_tokens=51 cost_usd=0.000000 tags=agent:echo
token_log status=ok route=MCP: chassis_agent-echo model=MCP: chassis_agent-echo prompt_tokens=0 completion_tokens=0 total_tokens=0 cost_usd=0.000000 tags=

## Workload typescript: GET /manifest

### manifest
GET http://127.0.0.1:8080/manifest
{
 "manifest_version": "0",
 "agent": {
  "name": "echo",
  "version": "0.0.1"
 },
 "versions": {
  "chassis": "0.1.0",
  "config": "37fe10aa8f0d",
  "prompt": "simplifier-v1",
  "model_route": "big-default"
 },
 "lane": "sidecar",
 "event_schema_versions": [
  "0"
 ],
 "interfaces": [
  {
   "name": "native",
   "method": "POST",
   "path": "/v1/run",
   "operation_id": "run",
   "streaming": true,
   "model": null
  },
  {
   "name": "openai",
   "method": "POST",
   "path": "/v1/chat/completions",
   "operation_id": "chat_completions",
   "streaming": true,
   "model": "echo"
  },
  {
   "name": "anthropic",
   "method": "POST",
   "path": "/v1/messages",
   "operation_id": "messages",
   "streaming": true,
   "model": "echo"
  },
  {
   "name": "mcp",
   "path": "/v1/mcp",
   "transport": "streamable-http",
   "streaming": false,
   "tools": [
    "echo"
   ]
  }
 ],
 "openapi": {
  "path": "/openapi.json",
  "version": "3.1.0",
  "sha256": "f0281fbc1dd8b704c86dbfd58118a19d8d520c929feb6ca53e35c266c6a2485f"
 }
}
manifest: ok

## Workload typescript: key-like variables in the workload container (names only; none expected)

key-like variables in the workload: none

## No key in any log

LITELLM_API_KEY is empty (shell and /Users/oleksandrderechei/git/agent-orchestration/deploy/compose/.env): no chassis key to look for

## Stop

 Network poc02 Removed 

result: every call ok
