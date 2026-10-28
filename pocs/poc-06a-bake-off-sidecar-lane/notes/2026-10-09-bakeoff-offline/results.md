# Bake-off results

Tasks: smoke, simplifier, lookup. Repeat: 10. Model: fake model server (scripted). Generated: 2026-10-09T22:38:06Z.

| Engine | Lane | Task | Pass | Tool calls | p50 ms | p95 ms | TTFT p50 ms | In tok | Out tok | Prompt bytes per call | Request body keys |
| ------ | ---- | ---- | ---- | ---------- | ------ | ------ | ----------- | ------ | ------- | --------------------- | ----------------- |
| echo-python | inprocess | smoke | 10/10 | - | 57.2 | 80.1 | 57.0 | 10 | 5 | 134 | max_tokens,messages,metadata,model,stream,stream_options,temperature,tools |
| echo-python | inprocess | simplifier | 10/10 | - | 61.4 | 96.4 | 60.1 | 10 | 5 | 187 | max_tokens,messages,metadata,model,stream,stream_options,temperature,tools |
| echo-python | inprocess | lookup | 10/10 | 10/10 | 146.4 | 203.4 | 144.3 | 30 | 15 | 152/497/796 | max_tokens,messages,metadata,model,stream,stream_options,temperature,tools |
| echo-python | sidecar | smoke | 10/10 | - | 66.9 | 77.8 | 65.7 | 10 | 5 | 134 | max_tokens,messages,metadata,model,stream,stream_options,temperature,tools |
| echo-python | sidecar | simplifier | 10/10 | - | 87.0 | 101.1 | 69.7 | 10 | 5 | 187 | max_tokens,messages,metadata,model,stream,stream_options,temperature,tools |
| echo-python | sidecar | lookup | 10/10 | 10/10 | 150.9 | 176.5 | 135.1 | 30 | 15 | 152/497/796 | max_tokens,messages,metadata,model,stream,stream_options,temperature,tools |
| echo-python | remote | smoke | 10/10 | - | 62.3 | 160.5 | 59.4 | 10 | 5 | 134 | max_tokens,messages,metadata,model,stream,stream_options,temperature,tools |
| echo-python | remote | simplifier | 10/10 | - | 65.4 | 73.5 | 62.0 | 10 | 5 | 187 | max_tokens,messages,metadata,model,stream,stream_options,temperature,tools |
| echo-python | remote | lookup | 10/10 | 10/10 | 148.0 | 168.5 | 133.7 | 30 | 15 | 152/497/796 | max_tokens,messages,metadata,model,stream,stream_options,temperature,tools |
| echo-pydanticai | inprocess | smoke | 10/10 | - | 42.1 | 48.5 | 41.6 | 10 | 5 | 134 | max_tokens,messages,metadata,model,stream,stream_options,temperature,tools |
| echo-pydanticai | inprocess | simplifier | 10/10 | - | 58.8 | 65.6 | 56.9 | 10 | 5 | 187 | max_tokens,messages,metadata,model,stream,stream_options,temperature,tools |
| echo-pydanticai | inprocess | lookup | 10/10 | 10/10 | 101.0 | 265.7 | 95.7 | 30 | 15 | 152/494/790 | max_tokens,messages,metadata,model,stream,stream_options,temperature,tools |
| echo-pydanticai | sidecar | smoke | 10/10 | - | 42.9 | 51.9 | 40.9 | 10 | 5 | 134 | max_tokens,messages,metadata,model,stream,stream_options,temperature,tools |
| echo-pydanticai | sidecar | simplifier | 10/10 | - | 54.5 | 67.5 | 37.7 | 10 | 5 | 187 | max_tokens,messages,metadata,model,stream,stream_options,temperature,tools |
| echo-pydanticai | sidecar | lookup | 10/10 | 10/10 | 108.5 | 127.3 | 82.3 | 30 | 15 | 152/494/790 | max_tokens,messages,metadata,model,stream,stream_options,temperature,tools |
| echo-pydanticai | remote | smoke | 10/10 | - | 45.4 | 55.9 | 41.0 | 10 | 5 | 134 | max_tokens,messages,metadata,model,stream,stream_options,temperature,tools |
| echo-pydanticai | remote | simplifier | 10/10 | - | 53.8 | 67.7 | 35.6 | 10 | 5 | 187 | max_tokens,messages,metadata,model,stream,stream_options,temperature,tools |
| echo-pydanticai | remote | lookup | 10/10 | 10/10 | 106.6 | 129.4 | 87.7 | 30 | 15 | 152/494/790 | max_tokens,messages,metadata,model,stream,stream_options,temperature,tools |
| echo-langgraph | inprocess | smoke | 10/10 | - | 43.9 | 57.5 | 44.5 | 10 | 5 | 134 | max_tokens,messages,metadata,model,stream,stream_options,temperature,tools |
| echo-langgraph | inprocess | simplifier | 10/10 | - | 57.8 | 175.5 | 54.4 | 10 | 5 | 187 | max_tokens,messages,metadata,model,stream,stream_options,temperature,tools |
| echo-langgraph | inprocess | lookup | 10/10 | 10/10 | 130.4 | 182.1 | 129.5 | 30 | 15 | 152/497/796 | max_tokens,messages,metadata,model,stream,stream_options,temperature,tools |
| echo-langgraph | sidecar | smoke | 10/10 | - | 45.2 | 54.3 | 46.9 | 10 | 5 | 134 | max_tokens,messages,metadata,model,stream,stream_options,temperature,tools |
| echo-langgraph | sidecar | simplifier | 10/10 | - | 59.6 | 66.9 | 52.2 | 10 | 5 | 187 | max_tokens,messages,metadata,model,stream,stream_options,temperature,tools |
| echo-langgraph | sidecar | lookup | 10/10 | 10/10 | 137.6 | 153.3 | 125.0 | 30 | 15 | 152/497/796 | max_tokens,messages,metadata,model,stream,stream_options,temperature,tools |
| echo-langgraph | remote | smoke | 10/10 | - | 53.4 | 63.3 | 61.6 | 10 | 5 | 134 | max_tokens,messages,metadata,model,stream,stream_options,temperature,tools |
| echo-langgraph | remote | simplifier | 10/10 | - | 67.7 | 84.8 | 58.6 | 10 | 5 | 187 | max_tokens,messages,metadata,model,stream,stream_options,temperature,tools |
| echo-langgraph | remote | lookup | 10/10 | 10/10 | 161.5 | 195.5 | 150.2 | 30 | 15 | 152/497/796 | max_tokens,messages,metadata,model,stream,stream_options,temperature,tools |
| echo-openai-agents | inprocess | smoke | 10/10 | - | 35.1 | 51.9 | 35.9 | 10 | 5 | 134 | max_tokens,messages,metadata,model,stream,stream_options,temperature,tools |
| echo-openai-agents | inprocess | simplifier | 10/10 | - | 45.1 | 61.7 | 43.2 | 10 | 5 | 187 | max_tokens,messages,metadata,model,stream,stream_options,temperature,tools |
| echo-openai-agents | inprocess | lookup | 10/10 | 10/10 | 95.3 | 224.4 | 98.8 | 30 | 15 | 152/497/796 | max_tokens,messages,metadata,model,stream,stream_options,temperature,tools |
| echo-openai-agents | sidecar | smoke | 10/10 | - | 47.9 | 63.4 | 40.2 | 10 | 5 | 134 | max_tokens,messages,metadata,model,stream,stream_options,temperature,tools |
| echo-openai-agents | sidecar | simplifier | 10/10 | - | 70.8 | 77.4 | 49.2 | 10 | 5 | 187 | max_tokens,messages,metadata,model,stream,stream_options,temperature,tools |
| echo-openai-agents | sidecar | lookup | 10/10 | 10/10 | 118.6 | 155.1 | 108.2 | 30 | 15 | 152/497/796 | max_tokens,messages,metadata,model,stream,stream_options,temperature,tools |
| echo-openai-agents | remote | smoke | 10/10 | - | 38.5 | 46.9 | 39.5 | 10 | 5 | 134 | max_tokens,messages,metadata,model,stream,stream_options,temperature,tools |
| echo-openai-agents | remote | simplifier | 10/10 | - | 62.2 | 142.9 | 48.0 | 10 | 5 | 187 | max_tokens,messages,metadata,model,stream,stream_options,temperature,tools |
| echo-openai-agents | remote | lookup | 10/10 | 10/10 | 102.4 | 128.2 | 89.8 | 30 | 15 | 152/497/796 | max_tokens,messages,metadata,model,stream,stream_options,temperature,tools |
| echo-typescript | sidecar | smoke | 10/10 | - | 29.5 | 34.5 | 27.0 | 10 | 5 | 134 | max_tokens,messages,metadata,model,stream,stream_options,temperature,tools |
| echo-typescript | sidecar | simplifier | 10/10 | - | 32.1 | 36.9 | 27.8 | 10 | 5 | 187 | max_tokens,messages,metadata,model,stream,stream_options,temperature,tools |
| echo-typescript | sidecar | lookup | 10/10 | 10/10 | 76.0 | 91.3 | 72.2 | 30 | 15 | 152/494/790 | max_tokens,messages,metadata,model,stream,stream_options,temperature,tools |
| echo-typescript | remote | smoke | 10/10 | - | 29.9 | 124.2 | 26.7 | 10 | 5 | 134 | max_tokens,messages,metadata,model,stream,stream_options,temperature,tools |
| echo-typescript | remote | simplifier | 10/10 | - | 30.0 | 33.6 | 28.1 | 10 | 5 | 187 | max_tokens,messages,metadata,model,stream,stream_options,temperature,tools |
| echo-typescript | remote | lookup | 10/10 | 10/10 | 66.6 | 89.9 | 62.1 | 30 | 15 | 152/494/790 | max_tokens,messages,metadata,model,stream,stream_options,temperature,tools |
| echo-smolagents | remote | smoke | 10/10 | - | 72.5 | 160.9 | 67.3 | 10 | 5 | 9235 | max_tokens,messages,metadata,model,temperature |
| echo-smolagents | remote | simplifier | 10/10 | - | 68.0 | 83.9 | 66.2 | 10 | 5 | 9288 | max_tokens,messages,metadata,model,temperature |
| echo-smolagents | remote | lookup | 10/10 | 10/10 | 143.6 | 154.8 | 139.2 | 30 | 15 | 9253/9738/10172 | max_tokens,messages,metadata,model,temperature |
| echo-claude-agent | remote | smoke | 10/10 | - | 651.5 | 1002.5 | 623.6 | 10 | 5 | 1658 | max_tokens,messages,metadata,model,stream,stream_options,temperature,tools |
| echo-claude-agent | remote | simplifier | 10/10 | - | 616.3 | 683.2 | 579.6 | 10 | 5 | 1711 | max_tokens,messages,metadata,model,stream,stream_options,temperature,tools |
| echo-claude-agent | remote | lookup | 10/10 | 10/10 | 708.4 | 796.1 | 722.8 | 30 | 15 | 1676/2112/2502 | max_tokens,messages,metadata,model,stream,stream_options,temperature,tools |

## Not run

| Engine | Lane | Status | Reason |
| --- | --- | --- | --- |
| kagent-adk | remote | SKIP | runs only on kind (poc06-kind.yml) |

## Mapping size

| Engine | Code lines | Files |
| ------ | ---------- | ----- |
| echo-python | 243 | handle.py, tools.py |
| echo-pydanticai | 181 | mapping.py, handle.py |
| echo-langgraph | 224 | mapping.py, handle.py, tools.py |
| echo-openai-agents | 295 | mapping.py, handle.py, tools.py |
| echo-typescript | 551 | a2a_server.ts, handle.ts, schema.ts, tools.ts |
| echo-smolagents | 380 | mapping.py, handle.py, tools.py, agent.py |
| echo-claude-agent | 168 | handle.py |
| kagent-adk | - | - |
