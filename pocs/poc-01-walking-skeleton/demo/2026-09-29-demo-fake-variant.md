
## Start the stack (no manual steps)

$ docker compose -f docker-compose.yaml up -d --wait --quiet-pull
 Network poc01 Creating 
 Network poc01 Created 
 Container poc01-fake-model-server-1 Creating 
 Container poc01-fake-model-server-1 Created 
 Container poc01-litellm-1 Creating 
 Container poc01-litellm-1 Created 
 Container poc01-chassis-1 Creating 
 Container poc01-chassis-1 Created 
 Container poc01-fake-model-server-1 Starting 
 Container poc01-fake-model-server-1 Started 
 Container poc01-fake-model-server-1 Waiting 
 Container poc01-fake-model-server-1 Healthy 
 Container poc01-litellm-1 Starting 
 Container poc01-litellm-1 Started 
 Container poc01-litellm-1 Waiting 
 Container poc01-litellm-1 Healthy 
 Container poc01-chassis-1 Starting 
 Container poc01-chassis-1 Started 
 Container poc01-fake-model-server-1 Waiting 
 Container poc01-litellm-1 Waiting 
 Container poc01-chassis-1 Waiting 
 Container poc01-fake-model-server-1 Healthy 
 Container poc01-litellm-1 Healthy 
 Container poc01-chassis-1 Healthy 
$ curl -s http://127.0.0.1:8080/health
{"status":"ok"}
$ curl -s http://127.0.0.1:8080/ready
{"status":"ready"}

## Route big-default: complete response

$ curl -s -X POST http://127.0.0.1:8080/v1/run -H content-type: application/json -d {"input": {"text": "simplify: the quick brown fox jumps over the lazy dog"}}
{"request_id":"17eff47d850c43c1afd9da09862778dd","trace_id":"9a02547df1d44cc18e7f58c9ca5bd512","idempotency_key":"484d549f9afb4eb89667ba88f1ca68b7","agent":"echo","agent_version":"0.0.1","output":{"text":"Plain words. Short sentences. Same facts."},"metrics":{"input_tokens":42,"output_tokens":9,"attempts":1,"model_route":"big-default"},"status":"ok","versions":{"chassis":"0.1.0","config":"e7ccf5dc7361","prompt":"simplifier-v1","model_route":"big-default"},"context_ref":null}

## Route big-default: streaming response (server-sent events)

$ curl -s -N -X POST http://127.0.0.1:8080/v1/run -H content-type: application/json -d {"input": {"text": "simplify: the quick brown fox jumps over the lazy dog"}, "stream": true}
event: start
data: {"schema_version":"0","type":"start","request_id":"1584b91338d147e4a8cba14fbc95e679"}

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
data: {"request_id":"1584b91338d147e4a8cba14fbc95e679","trace_id":"84cf4a4291bf459c8e496a15ee66c3fe","idempotency_key":"30f17c33c7ee4f788760d8c54088768b","agent":"echo","agent_version":"0.0.1","output":{"text":"Plain words. Short sentences. Same facts."},"metrics":{"input_tokens":42,"output_tokens":9,"attempts":1,"model_route":"big-default"},"status":"ok","versions":{"chassis":"0.1.0","config":"e7ccf5dc7361","prompt":"simplifier-v1","model_route":"big-default"},"context_ref":null}


## Switch model.route to local-small: a config change, then a restart

$ docker compose -f docker-compose.yaml -f /var/folders/8k/hzkftcbx77b4yp9s01fszxp00000gn/T/tmp.mlbWRBaVoe/override.yaml up -d --wait chassis
 Container poc01-fake-model-server-1 Running 
 Container poc01-litellm-1 Running 
 Container poc01-chassis-1 Recreate 
 Container poc01-chassis-1 Recreated 
 Container poc01-fake-model-server-1 Waiting 
 Container poc01-fake-model-server-1 Healthy 
 Container poc01-litellm-1 Waiting 
 Container poc01-litellm-1 Healthy 
 Container poc01-chassis-1 Starting 
 Container poc01-chassis-1 Started 
 Container poc01-chassis-1 Waiting 
 Container poc01-fake-model-server-1 Waiting 
 Container poc01-litellm-1 Waiting 
 Container poc01-fake-model-server-1 Healthy 
 Container poc01-litellm-1 Healthy 
 Container poc01-chassis-1 Healthy 

## Route local-small: complete response

$ curl -s -X POST http://127.0.0.1:8080/v1/run -H content-type: application/json -d {"input": {"text": "simplify: the quick brown fox jumps over the lazy dog"}}
{"request_id":"e07a0d3ffe0245d7ba65efad32d21a6c","trace_id":"19ae22b367614c7cb042ff6c4e4ad0d8","idempotency_key":"4f7aa0f6f57f45e6b5b950342b394a7b","agent":"echo","agent_version":"0.0.1","output":{"text":"Plain words. Short sentences. Same facts."},"metrics":{"input_tokens":42,"output_tokens":9,"attempts":1,"model_route":"local-small"},"status":"ok","versions":{"chassis":"0.1.0","config":"8d410e2004f1","prompt":"simplifier-v1","model_route":"local-small"},"context_ref":null}

## Route local-small: streaming response (server-sent events)

$ curl -s -N -X POST http://127.0.0.1:8080/v1/run -H content-type: application/json -d {"input": {"text": "simplify: the quick brown fox jumps over the lazy dog"}, "stream": true}
event: start
data: {"schema_version":"0","type":"start","request_id":"c3ff4f6127c142eb9022fff74e41731d"}

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
data: {"schema_version":"0","type":"metrics","input_tokens":42,"output_tokens":9,"cost_usd":null,"model_route":"local-small","latency_ms":null,"attempt":1}

event: end
data: {"schema_version":"0","type":"end","status":"ok","output":null}

event: response
data: {"request_id":"c3ff4f6127c142eb9022fff74e41731d","trace_id":"edd1aad9c1764d72889e3c8e2aed76c5","idempotency_key":"147241888ca640e9908bf4b4a796de4b","agent":"echo","agent_version":"0.0.1","output":{"text":"Plain words. Short sentences. Same facts."},"metrics":{"input_tokens":42,"output_tokens":9,"attempts":1,"model_route":"local-small"},"status":"ok","versions":{"chassis":"0.1.0","config":"8d410e2004f1","prompt":"simplifier-v1","model_route":"local-small"},"context_ref":null}


## Token counts in the router (LiteLLM), one line per call, tagged agent:<name>

litellm-1  | token_log status=ok route=big-default model=fake-model prompt_tokens=42 completion_tokens=9 total_tokens=51 cost_usd=0.000000 tags=agent:echo
litellm-1  | token_log status=ok route=big-default model=fake-model prompt_tokens=42 completion_tokens=9 total_tokens=51 cost_usd=0.000000 tags=agent:echo
litellm-1  | token_log status=ok route=local-small model=fake-model prompt_tokens=42 completion_tokens=9 total_tokens=51 cost_usd=0.000000 tags=agent:echo
litellm-1  | token_log status=ok route=local-small model=fake-model prompt_tokens=42 completion_tokens=9 total_tokens=51 cost_usd=0.000000 tags=agent:echo

## No key in any log


## Stop

$ docker compose -f docker-compose.yaml -f /var/folders/8k/hzkftcbx77b4yp9s01fszxp00000gn/T/tmp.mlbWRBaVoe/override.yaml down
 Container poc01-chassis-1 Stopping 
 Container poc01-chassis-1 Stopped 
 Container poc01-chassis-1 Removing 
 Container poc01-chassis-1 Removed 
 Container poc01-litellm-1 Stopping 
 Container poc01-litellm-1 Stopped 
 Container poc01-litellm-1 Removing 
 Container poc01-litellm-1 Removed 
 Container poc01-fake-model-server-1 Stopping 
 Container poc01-fake-model-server-1 Stopped 
 Container poc01-fake-model-server-1 Removing 
 Container poc01-fake-model-server-1 Removed 
 Network poc01 Removing 
 Network poc01 Removed 
