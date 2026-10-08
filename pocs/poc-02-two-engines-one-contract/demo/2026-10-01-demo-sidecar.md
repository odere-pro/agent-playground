# PoC-2 demo: four engines in the sidecar lane (fake variant)

Recorded 2026-10-01T06:07:10Z by deploy/compose/demo-sidecar.sh.

## Workload python: start the stack with the workload-python profile (no manual steps)

$ docker compose -f docker-compose.yaml -f docker-compose.sidecar.yaml --profile python up -d --wait --quiet-pull
 Network poc02 Creating 
 Network poc02 Created 
 Container poc02-fake-model-server-1 Creating 
 Container poc02-fake-model-server-1 Created 
 Container poc02-litellm-1 Creating 
 Container poc02-litellm-1 Created 
 Container poc02-chassis-1 Creating 
 Container poc02-chassis-1 Created 
 Container poc02-workload-python-1 Creating 
 Container poc02-workload-python-1 Created 
 Container poc02-fake-model-server-1 Starting 
 Container poc02-fake-model-server-1 Started 
 Container poc02-fake-model-server-1 Waiting 
 Container poc02-fake-model-server-1 Healthy 
 Container poc02-litellm-1 Starting 
 Container poc02-litellm-1 Started 
 Container poc02-litellm-1 Waiting 
 Container poc02-litellm-1 Healthy 
 Container poc02-chassis-1 Starting 
 Container poc02-chassis-1 Started 
 Container poc02-workload-python-1 Starting 
 Container poc02-workload-python-1 Started 
 Container poc02-litellm-1 Waiting 
 Container poc02-chassis-1 Waiting 
 Container poc02-workload-python-1 Waiting 
 Container poc02-fake-model-server-1 Waiting 
 Container poc02-fake-model-server-1 Healthy 
 Container poc02-litellm-1 Healthy 
 Container poc02-workload-python-1 Healthy 
 Container poc02-chassis-1 Healthy 
$ curl -s http://127.0.0.1:8080/ready
{"status":"ready"}

## Workload python: the engine behind the chassis (its agent card, read inside the chassis)

$ docker compose -f docker-compose.yaml -f docker-compose.sidecar.yaml exec -T chassis python -c import json,urllib.request; c=json.load(urllib.request.urlopen('http://127.0.0.1:9000/.well-known/agent-card.json', timeout=2)); print(c['name'], c.get('version'))
echo-python 0.1.0
$ curl -s -m 2 http://127.0.0.1:9000/.well-known/agent-card.json   # from the host
not reachable from the host: the workload publishes no port

## Workload python: complete response

$ curl -s -X POST http://127.0.0.1:8080/v1/run -H content-type: application/json -d {"input": {"text": "glossary: what does SLM mean? Say it in plain words."}}
{"request_id":"c4167f590d0b41569a625f87dc9fecfb","trace_id":"f7b41364599549c9b3cc1f84cbd398a1","idempotency_key":"55e6afac2a414bddbd3852a2bb02ce56","agent":"echo","agent_version":"0.0.1","output":{"text":"From the glossary: SLM means small language model.","tool_calls":[{"call_id":"call_f3dc6eaf","name":"glossary_lookup","arguments":{"term":"SLM"},"result":{"term":"SLM","definition":"A small language model: a model small enough to run cheaply on one GPU or a CPU."}}]},"metrics":{"input_tokens":20,"output_tokens":10,"attempts":1,"model_route":"big-default"},"status":"ok","versions":{"chassis":"0.1.0","config":"37fe10aa8f0d","prompt":"simplifier-v1","model_route":"big-default"},"context_ref":null}
versions: {"chassis": "0.1.0", "config": "37fe10aa8f0d", "prompt": "simplifier-v1", "model_route": "big-default"}

## Workload python: streaming response (server-sent events)

$ curl -s -N -X POST http://127.0.0.1:8080/v1/run -H content-type: application/json -d {"input": {"text": "glossary: what does SLM mean? Say it in plain words."}, "stream": true}
event: start
data: {"schema_version":"0","type":"start","request_id":"977e649bc49e4f6a9c823909b5d6bb92"}

event: tool_call
data: {"schema_version":"0","type":"tool_call","call_id":"call_1e485145","name":"glossary_lookup","arguments":{"term":"SLM"},"result":{"term":"SLM","definition":"A small language model: a model small enough to run cheaply on one GPU or a CPU."}}

event: delta
data: {"schema_version":"0","type":"delta","text":"From "}

event: delta
data: {"schema_version":"0","type":"delta","text":"the "}

event: delta
data: {"schema_version":"0","type":"delta","text":"glossary: "}

event: delta
data: {"schema_version":"0","type":"delta","text":"SLM "}

event: delta
data: {"schema_version":"0","type":"delta","text":"means "}

event: delta
data: {"schema_version":"0","type":"delta","text":"small "}

event: delta
data: {"schema_version":"0","type":"delta","text":"language "}

event: delta
data: {"schema_version":"0","type":"delta","text":"model."}

event: metrics
data: {"schema_version":"0","type":"metrics","input_tokens":20,"output_tokens":10,"cost_usd":null,"model_route":"big-default","latency_ms":null,"attempt":1}

event: end
data: {"schema_version":"0","type":"end","status":"ok","output":null}

event: response
data: {"request_id":"977e649bc49e4f6a9c823909b5d6bb92","trace_id":"4445cc7a5c734e7496a5ee7342389405","idempotency_key":"14cada1703ce4eb7bc4a7b679c06a96e","agent":"echo","agent_version":"0.0.1","output":{"text":"From the glossary: SLM means small language model.","tool_calls":[{"call_id":"call_1e485145","name":"glossary_lookup","arguments":{"term":"SLM"},"result":{"term":"SLM","definition":"A small language model: a model small enough to run cheaply on one GPU or a CPU."}}]},"metrics":{"input_tokens":20,"output_tokens":10,"attempts":1,"model_route":"big-default"},"status":"ok","versions":{"chassis":"0.1.0","config":"37fe10aa8f0d","prompt":"simplifier-v1","model_route":"big-default"},"context_ref":null}


## Workload python: token counts in the router (LiteLLM), this engine's calls only

litellm-1  | token_log status=ok route=big-default model=fake-model prompt_tokens=10 completion_tokens=5 total_tokens=15 cost_usd=0.000000 tags=agent:echo
litellm-1  | token_log status=ok route=big-default model=fake-model prompt_tokens=10 completion_tokens=5 total_tokens=15 cost_usd=0.000000 tags=agent:echo
litellm-1  | token_log status=ok route=big-default model=fake-model prompt_tokens=10 completion_tokens=5 total_tokens=15 cost_usd=0.000000 tags=agent:echo
litellm-1  | token_log status=ok route=big-default model=fake-model prompt_tokens=10 completion_tokens=5 total_tokens=15 cost_usd=0.000000 tags=agent:echo

## Workload python: key-like variables in the workload container (names only; none expected)

$ docker compose exec workload-python env | cut -d= -f1 | grep -i -E 'key|token|secret' | grep -v -E '^GPG_KEY$'
key-like variables in the workload: none

## Workload pydanticai: swap workload-python for workload-pydanticai, then recreate the chassis next to it

$ docker compose -f docker-compose.yaml -f docker-compose.sidecar.yaml --profile python rm -sf workload-python
 Container poc02-workload-python-1 Stopping 
 Container poc02-workload-python-1 Stopped 
Going to remove poc02-workload-python-1
 Container poc02-workload-python-1 Removing 
 Container poc02-workload-python-1 Removed 
$ docker compose -f docker-compose.yaml -f docker-compose.sidecar.yaml --profile pydanticai up -d --wait --no-deps --force-recreate chassis workload-pydanticai
 Container poc02-chassis-1 Recreate 
 Container poc02-chassis-1 Recreated 
 Container poc02-workload-pydanticai-1 Creating 
 Container poc02-workload-pydanticai-1 Created 
 Container poc02-chassis-1 Starting 
 Container poc02-chassis-1 Started 
 Container poc02-workload-pydanticai-1 Starting 
 Container poc02-workload-pydanticai-1 Started 
 Container poc02-chassis-1 Waiting 
 Container poc02-workload-pydanticai-1 Waiting 
 Container poc02-chassis-1 Healthy 
 Container poc02-workload-pydanticai-1 Healthy 
$ curl -s http://127.0.0.1:8080/ready
{"status":"ready"}

## Workload pydanticai: the engine behind the chassis (its agent card, read inside the chassis)

$ docker compose -f docker-compose.yaml -f docker-compose.sidecar.yaml exec -T chassis python -c import json,urllib.request; c=json.load(urllib.request.urlopen('http://127.0.0.1:9000/.well-known/agent-card.json', timeout=2)); print(c['name'], c.get('version'))
echo-pydanticai 0.1.0
$ curl -s -m 2 http://127.0.0.1:9000/.well-known/agent-card.json   # from the host
not reachable from the host: the workload publishes no port

## Workload pydanticai: complete response

$ curl -s -X POST http://127.0.0.1:8080/v1/run -H content-type: application/json -d {"input": {"text": "glossary: what does SLM mean? Say it in plain words."}}
{"request_id":"8431d81ac1f44efbb135be05e76b04a8","trace_id":"d9c291bf555a4e91b58703771a561114","idempotency_key":"33fed59aada74b0981e9caffd02ffac5","agent":"echo","agent_version":"0.0.1","output":{"text":"From the glossary: SLM means small language model.","tool_calls":[{"call_id":"call_b681e728","name":"glossary_lookup","arguments":{"term":"SLM"},"result":{"term":"SLM","definition":"A small language model: a model small enough to run cheaply on one GPU or a CPU."}}]},"metrics":{"input_tokens":20,"output_tokens":10,"attempts":1,"model_route":"big-default"},"status":"ok","versions":{"chassis":"0.1.0","config":"37fe10aa8f0d","prompt":"simplifier-v1","model_route":"big-default"},"context_ref":null}
versions: {"chassis": "0.1.0", "config": "37fe10aa8f0d", "prompt": "simplifier-v1", "model_route": "big-default"}

## Workload pydanticai: streaming response (server-sent events)

$ curl -s -N -X POST http://127.0.0.1:8080/v1/run -H content-type: application/json -d {"input": {"text": "glossary: what does SLM mean? Say it in plain words."}, "stream": true}
event: start
data: {"schema_version":"0","type":"start","request_id":"1206487fe07346119c6d6d45f98f15c7"}

event: tool_call
data: {"schema_version":"0","type":"tool_call","call_id":"call_67d7876f","name":"glossary_lookup","arguments":{"term":"SLM"},"result":{"term":"SLM","definition":"A small language model: a model small enough to run cheaply on one GPU or a CPU."}}

event: delta
data: {"schema_version":"0","type":"delta","text":"From "}

event: delta
data: {"schema_version":"0","type":"delta","text":"the "}

event: delta
data: {"schema_version":"0","type":"delta","text":"glossary: "}

event: delta
data: {"schema_version":"0","type":"delta","text":"SLM "}

event: delta
data: {"schema_version":"0","type":"delta","text":"means "}

event: delta
data: {"schema_version":"0","type":"delta","text":"small "}

event: delta
data: {"schema_version":"0","type":"delta","text":"language "}

event: delta
data: {"schema_version":"0","type":"delta","text":"model."}

event: metrics
data: {"schema_version":"0","type":"metrics","input_tokens":20,"output_tokens":10,"cost_usd":null,"model_route":"big-default","latency_ms":null,"attempt":1}

event: end
data: {"schema_version":"0","type":"end","status":"ok","output":null}

event: response
data: {"request_id":"1206487fe07346119c6d6d45f98f15c7","trace_id":"d3c6bd29d44643ec94a7000382c63629","idempotency_key":"2aebf32abbb2447699f13be17857d092","agent":"echo","agent_version":"0.0.1","output":{"text":"From the glossary: SLM means small language model.","tool_calls":[{"call_id":"call_67d7876f","name":"glossary_lookup","arguments":{"term":"SLM"},"result":{"term":"SLM","definition":"A small language model: a model small enough to run cheaply on one GPU or a CPU."}}]},"metrics":{"input_tokens":20,"output_tokens":10,"attempts":1,"model_route":"big-default"},"status":"ok","versions":{"chassis":"0.1.0","config":"37fe10aa8f0d","prompt":"simplifier-v1","model_route":"big-default"},"context_ref":null}


## Workload pydanticai: token counts in the router (LiteLLM), this engine's calls only

litellm-1  | token_log status=ok route=big-default model=fake-model prompt_tokens=10 completion_tokens=5 total_tokens=15 cost_usd=0.000000 tags=agent:echo
litellm-1  | token_log status=ok route=big-default model=fake-model prompt_tokens=10 completion_tokens=5 total_tokens=15 cost_usd=0.000000 tags=agent:echo
litellm-1  | token_log status=ok route=big-default model=fake-model prompt_tokens=10 completion_tokens=5 total_tokens=15 cost_usd=0.000000 tags=agent:echo
litellm-1  | token_log status=ok route=big-default model=fake-model prompt_tokens=10 completion_tokens=5 total_tokens=15 cost_usd=0.000000 tags=agent:echo

## Workload pydanticai: key-like variables in the workload container (names only; none expected)

$ docker compose exec workload-pydanticai env | cut -d= -f1 | grep -i -E 'key|token|secret' | grep -v -E '^GPG_KEY$'
key-like variables in the workload: none

## Workload langgraph: swap workload-pydanticai for workload-langgraph, then recreate the chassis next to it

$ docker compose -f docker-compose.yaml -f docker-compose.sidecar.yaml --profile pydanticai rm -sf workload-pydanticai
 Container poc02-workload-pydanticai-1 Stopping 
 Container poc02-workload-pydanticai-1 Stopped 
Going to remove poc02-workload-pydanticai-1
 Container poc02-workload-pydanticai-1 Removing 
 Container poc02-workload-pydanticai-1 Removed 
$ docker compose -f docker-compose.yaml -f docker-compose.sidecar.yaml --profile langgraph up -d --wait --no-deps --force-recreate chassis workload-langgraph
 Container poc02-chassis-1 Recreate 
 Container poc02-chassis-1 Recreated 
 Container poc02-workload-langgraph-1 Creating 
 Container poc02-workload-langgraph-1 Created 
 Container poc02-chassis-1 Starting 
 Container poc02-chassis-1 Started 
 Container poc02-workload-langgraph-1 Starting 
 Container poc02-workload-langgraph-1 Started 
 Container poc02-chassis-1 Waiting 
 Container poc02-workload-langgraph-1 Waiting 
 Container poc02-workload-langgraph-1 Healthy 
 Container poc02-chassis-1 Healthy 
$ curl -s http://127.0.0.1:8080/ready
{"status":"ready"}

## Workload langgraph: the engine behind the chassis (its agent card, read inside the chassis)

$ docker compose -f docker-compose.yaml -f docker-compose.sidecar.yaml exec -T chassis python -c import json,urllib.request; c=json.load(urllib.request.urlopen('http://127.0.0.1:9000/.well-known/agent-card.json', timeout=2)); print(c['name'], c.get('version'))
echo-langgraph 0.1.0
$ curl -s -m 2 http://127.0.0.1:9000/.well-known/agent-card.json   # from the host
not reachable from the host: the workload publishes no port

## Workload langgraph: complete response

$ curl -s -X POST http://127.0.0.1:8080/v1/run -H content-type: application/json -d {"input": {"text": "glossary: what does SLM mean? Say it in plain words."}}
{"request_id":"b0ebb2574f1e428ca84b765daa0a57c3","trace_id":"a5bd5709eede4a47bf226d3ae38dfca0","idempotency_key":"4266ffd2c602459daf750228a0f89756","agent":"echo","agent_version":"0.0.1","output":{"text":"From the glossary: SLM means small language model.","tool_calls":[{"call_id":"call_689f8db2","name":"glossary_lookup","arguments":{"term":"SLM"},"result":{"term":"SLM","definition":"A small language model: a model small enough to run cheaply on one GPU or a CPU."}}]},"metrics":{"input_tokens":20,"output_tokens":10,"attempts":1,"model_route":"big-default"},"status":"ok","versions":{"chassis":"0.1.0","config":"37fe10aa8f0d","prompt":"simplifier-v1","model_route":"big-default"},"context_ref":null}
versions: {"chassis": "0.1.0", "config": "37fe10aa8f0d", "prompt": "simplifier-v1", "model_route": "big-default"}

## Workload langgraph: streaming response (server-sent events)

$ curl -s -N -X POST http://127.0.0.1:8080/v1/run -H content-type: application/json -d {"input": {"text": "glossary: what does SLM mean? Say it in plain words."}, "stream": true}
event: start
data: {"schema_version":"0","type":"start","request_id":"de497e63b2c04c5a858d0149f44c75d9"}

event: tool_call
data: {"schema_version":"0","type":"tool_call","call_id":"call_d3794b49","name":"glossary_lookup","arguments":{"term":"SLM"},"result":{"term":"SLM","definition":"A small language model: a model small enough to run cheaply on one GPU or a CPU."}}

event: delta
data: {"schema_version":"0","type":"delta","text":"From "}

event: delta
data: {"schema_version":"0","type":"delta","text":"the "}

event: delta
data: {"schema_version":"0","type":"delta","text":"glossary: "}

event: delta
data: {"schema_version":"0","type":"delta","text":"SLM "}

event: delta
data: {"schema_version":"0","type":"delta","text":"means "}

event: delta
data: {"schema_version":"0","type":"delta","text":"small "}

event: delta
data: {"schema_version":"0","type":"delta","text":"language "}

event: delta
data: {"schema_version":"0","type":"delta","text":"model."}

event: metrics
data: {"schema_version":"0","type":"metrics","input_tokens":20,"output_tokens":10,"cost_usd":null,"model_route":"big-default","latency_ms":null,"attempt":1}

event: end
data: {"schema_version":"0","type":"end","status":"ok","output":null}

event: response
data: {"request_id":"de497e63b2c04c5a858d0149f44c75d9","trace_id":"e233792aa073408abf02ad29a35c9fe3","idempotency_key":"d25d78857ec7439aa1f3d4185f7108f1","agent":"echo","agent_version":"0.0.1","output":{"text":"From the glossary: SLM means small language model.","tool_calls":[{"call_id":"call_d3794b49","name":"glossary_lookup","arguments":{"term":"SLM"},"result":{"term":"SLM","definition":"A small language model: a model small enough to run cheaply on one GPU or a CPU."}}]},"metrics":{"input_tokens":20,"output_tokens":10,"attempts":1,"model_route":"big-default"},"status":"ok","versions":{"chassis":"0.1.0","config":"37fe10aa8f0d","prompt":"simplifier-v1","model_route":"big-default"},"context_ref":null}


## Workload langgraph: token counts in the router (LiteLLM), this engine's calls only

litellm-1  | token_log status=ok route=big-default model=fake-model prompt_tokens=10 completion_tokens=5 total_tokens=15 cost_usd=0.000000 tags=agent:echo
litellm-1  | token_log status=ok route=big-default model=fake-model prompt_tokens=10 completion_tokens=5 total_tokens=15 cost_usd=0.000000 tags=agent:echo
litellm-1  | token_log status=ok route=big-default model=fake-model prompt_tokens=10 completion_tokens=5 total_tokens=15 cost_usd=0.000000 tags=agent:echo
litellm-1  | token_log status=ok route=big-default model=fake-model prompt_tokens=10 completion_tokens=5 total_tokens=15 cost_usd=0.000000 tags=agent:echo

## Workload langgraph: key-like variables in the workload container (names only; none expected)

$ docker compose exec workload-langgraph env | cut -d= -f1 | grep -i -E 'key|token|secret' | grep -v -E '^GPG_KEY$'
key-like variables in the workload: none

## Workload typescript: swap workload-langgraph for workload-typescript, then recreate the chassis next to it

$ docker compose -f docker-compose.yaml -f docker-compose.sidecar.yaml --profile langgraph rm -sf workload-langgraph
 Container poc02-workload-langgraph-1 Stopping 
 Container poc02-workload-langgraph-1 Stopped 
Going to remove poc02-workload-langgraph-1
 Container poc02-workload-langgraph-1 Removing 
 Container poc02-workload-langgraph-1 Removed 
$ docker compose -f docker-compose.yaml -f docker-compose.sidecar.yaml --profile typescript up -d --wait --no-deps --force-recreate chassis workload-typescript
 Container poc02-chassis-1 Recreate 
 Container poc02-chassis-1 Recreated 
 Container poc02-workload-typescript-1 Creating 
 Container poc02-workload-typescript-1 Created 
 Container poc02-chassis-1 Starting 
 Container poc02-chassis-1 Started 
 Container poc02-workload-typescript-1 Starting 
 Container poc02-workload-typescript-1 Started 
 Container poc02-workload-typescript-1 Waiting 
 Container poc02-chassis-1 Waiting 
 Container poc02-workload-typescript-1 Healthy 
 Container poc02-chassis-1 Healthy 
$ curl -s http://127.0.0.1:8080/ready
{"status":"ready"}

## Workload typescript: the engine behind the chassis (its agent card, read inside the chassis)

$ docker compose -f docker-compose.yaml -f docker-compose.sidecar.yaml exec -T chassis python -c import json,urllib.request; c=json.load(urllib.request.urlopen('http://127.0.0.1:9000/.well-known/agent-card.json', timeout=2)); print(c['name'], c.get('version'))
echo-typescript 0.1.0
$ curl -s -m 2 http://127.0.0.1:9000/.well-known/agent-card.json   # from the host
not reachable from the host: the workload publishes no port

## Workload typescript: complete response

$ curl -s -X POST http://127.0.0.1:8080/v1/run -H content-type: application/json -d {"input": {"text": "simplify: the quick brown fox jumps over the lazy dog."}}
{"request_id":"d9b2fbec8437438b81d0d16649e34fc7","trace_id":"4e44ede7095340c39899c2c85f94aa8e","idempotency_key":"f7d94fdb10fb46419078dd31ceb54841","agent":"echo","agent_version":"0.0.1","output":{"text":"Plain words. Short sentences. Same facts."},"metrics":{"input_tokens":42,"output_tokens":9,"attempts":1,"model_route":"big-default"},"status":"ok","versions":{"chassis":"0.1.0","config":"37fe10aa8f0d","prompt":"simplifier-v1","model_route":"big-default"},"context_ref":null}
versions: {"chassis": "0.1.0", "config": "37fe10aa8f0d", "prompt": "simplifier-v1", "model_route": "big-default"}

## Workload typescript: streaming response (server-sent events)

$ curl -s -N -X POST http://127.0.0.1:8080/v1/run -H content-type: application/json -d {"input": {"text": "simplify: the quick brown fox jumps over the lazy dog."}, "stream": true}
event: start
data: {"schema_version":"0","type":"start","request_id":"c22fc1ef40f94e8186adaa9b87f766de"}

event: delta
data: {"schema_version":"0","type":"delta","text":"Plain "}

event: delta
data: {"schema_version":"0","type":"delta","text":"words. "}

event: delta
data: {"schema_version":"0","type":"delta","text":"Short "}

event: delta
data: {"schema_version":"0","type":"delta","text":"sentences. "}

event: delta
data: {"schema_version":"0","type":"delta","text":"Same "}

event: delta
data: {"schema_version":"0","type":"delta","text":"facts."}

event: metrics
data: {"schema_version":"0","type":"metrics","input_tokens":42,"output_tokens":9,"cost_usd":null,"model_route":"big-default","latency_ms":null,"attempt":1}

event: end
data: {"schema_version":"0","type":"end","status":"ok","output":null}

event: response
data: {"request_id":"c22fc1ef40f94e8186adaa9b87f766de","trace_id":"eef5423a4b414f58bef82ccd8f4d0ac6","idempotency_key":"c9912864ed684b2b9075d296b135d980","agent":"echo","agent_version":"0.0.1","output":{"text":"Plain words. Short sentences. Same facts."},"metrics":{"input_tokens":42,"output_tokens":9,"attempts":1,"model_route":"big-default"},"status":"ok","versions":{"chassis":"0.1.0","config":"37fe10aa8f0d","prompt":"simplifier-v1","model_route":"big-default"},"context_ref":null}


## Workload typescript: token counts in the router (LiteLLM), this engine's calls only

litellm-1  | token_log status=ok route=big-default model=fake-model prompt_tokens=42 completion_tokens=9 total_tokens=51 cost_usd=0.000000 tags=agent:echo
litellm-1  | token_log status=ok route=big-default model=fake-model prompt_tokens=42 completion_tokens=9 total_tokens=51 cost_usd=0.000000 tags=agent:echo

## Workload typescript: key-like variables in the workload container (names only; none expected)

$ docker compose exec workload-typescript env | cut -d= -f1 | grep -i -E 'key|token|secret' | grep -v -E '^GPG_KEY$'
key-like variables in the workload: none

## No key in any log

LITELLM_API_KEY is empty (shell and /Users/oleksandrderechei/git/agent-orchestration/deploy/compose/.env): no chassis key to look for

## Stop

$ docker compose -f docker-compose.yaml -f docker-compose.sidecar.yaml --profile python --profile pydanticai --profile langgraph --profile typescript down
 Container poc02-workload-typescript-1 Stopping 
 Container poc02-workload-typescript-1 Stopped 
 Container poc02-workload-typescript-1 Removing 
 Container poc02-workload-typescript-1 Removed 
 Container poc02-chassis-1 Stopping 
 Container poc02-chassis-1 Stopped 
 Container poc02-chassis-1 Removing 
 Container poc02-chassis-1 Removed 
 Container poc02-litellm-1 Stopping 
 Container poc02-litellm-1 Stopped 
 Container poc02-litellm-1 Removing 
 Container poc02-litellm-1 Removed 
 Container poc02-fake-model-server-1 Stopping 
 Container poc02-fake-model-server-1 Stopped 
 Container poc02-fake-model-server-1 Removing 
 Container poc02-fake-model-server-1 Removed 
 Network poc02 Removing 
 Network poc02 Removed 
