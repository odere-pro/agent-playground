# PoC-6a offline demo: every sidecar engine and the untrusted engines on the fake model

Where it ran: localhost on Linux 6.18.44-fc-v114, 4 CPUs. Processes on random ports; the model is the fake model server (`packages/fake-model-server/scripts/bakeoff.yaml`). No Docker, no kind, no key.
Commit: `d8ea82d` (0 changed or untracked paths at the start). Date: 2026-10-10T00:03:43Z.
Script: `pocs/poc-06a-bake-off-sidecar-lane/demo/demo.sh`. The uv `UV_NATIVE_TLS` deprecation warning is dropped from the output.
Hosted-model numbers are not here: they come from the Mac run, `make poc06-mac`.

## 1. The TypeScript agent: make ts-check

$ make ts-check

```
(548 earlier lines trimmed)
  ...
1..84
# tests 84
# suites 0
# pass 84
# fail 0
# cancelled 0
# skipped 0
# todo 0
# duration_ms 4032.328611

> echo-typescript@0.1.0 build
> tsc -p tsconfig.build.json

```

exit code: 0

## 2. The super simple task on every framework: bakeoff smoke

$ uv run python -m bakeoff smoke

```
PASS echo-python         inprocess
PASS echo-python         sidecar
PASS echo-python         remote
PASS echo-pydanticai     inprocess
PASS echo-pydanticai     sidecar
PASS echo-pydanticai     remote
PASS echo-langgraph      inprocess
PASS echo-langgraph      sidecar
PASS echo-langgraph      remote
PASS echo-openai-agents  inprocess
PASS echo-openai-agents  sidecar
PASS echo-openai-agents  remote
PASS echo-typescript     sidecar
PASS echo-typescript     remote
PASS echo-smolagents     remote
PASS echo-claude-agent   remote
SKIP kagent-adk          remote      runs only on kind (poc06-kind.yml)
```

exit code: 0

## 3. A short run of the three tasks on four sidecar engines: bakeoff run

$ uv run python -m bakeoff run --engine echo-python,echo-pydanticai,echo-openai-agents,echo-typescript --lane sidecar --tasks smoke,simplifier,lookup --repeat 3 --out /tmp/tmp.kd2tydzrov/run

```
(5 earlier lines trimmed)
  echo-pydanticai sidecar: smoke x3
  echo-pydanticai sidecar: simplifier x3
  echo-pydanticai sidecar: lookup x3
echo-openai-agents sidecar
  echo-openai-agents sidecar: smoke x3
  echo-openai-agents sidecar: simplifier x3
  echo-openai-agents sidecar: lookup x3
echo-typescript sidecar
  echo-typescript sidecar: smoke x3
  echo-typescript sidecar: simplifier x3
  echo-typescript sidecar: lookup x3
wrote /tmp/tmp.kd2tydzrov/run/results.json and /tmp/tmp.kd2tydzrov/run/results.md
```

exit code: 0

`results.md` of that run (offline, fake model; the numbers are the stack's own time):

# Bake-off results

Tasks: smoke, simplifier, lookup. Repeat: 3. Model: fake model server (scripted). Generated: 2026-10-10T00:05:51Z.

| Engine | Lane | Task | Pass | Tool calls | p50 ms | p95 ms | TTFT p50 ms | In tok | Out tok | Prompt bytes per call | Request body keys |
| ------ | ---- | ---- | ---- | ---------- | ------ | ------ | ----------- | ------ | ------- | --------------------- | ----------------- |
| echo-python | sidecar | smoke | 3/3 | - | 54.0 | 60.8 | 48.4 | 10 | 5 | 134 | max_tokens,messages,metadata,model,stream,stream_options,temperature,tools |
| echo-python | sidecar | simplifier | 3/3 | - | 53.6 | 54.4 | 48.2 | 10 | 5 | 187 | max_tokens,messages,metadata,model,stream,stream_options,temperature,tools |
| echo-python | sidecar | lookup | 3/3 | 3/3 | 106.5 | 114.7 | 110.2 | 30 | 15 | 152/497/796 | max_tokens,messages,metadata,model,stream,stream_options,temperature,tools |
| echo-pydanticai | sidecar | smoke | 3/3 | - | 33.4 | 33.9 | 27.8 | 10 | 5 | 134 | max_tokens,messages,metadata,model,stream,stream_options,temperature,tools |
| echo-pydanticai | sidecar | simplifier | 3/3 | - | 56.4 | 172.2 | 27.1 | 10 | 5 | 187 | max_tokens,messages,metadata,model,stream,stream_options,temperature,tools |
| echo-pydanticai | sidecar | lookup | 3/3 | 3/3 | 77.0 | 77.3 | 58.6 | 30 | 15 | 152/494/790 | max_tokens,messages,metadata,model,stream,stream_options,temperature,tools |
| echo-openai-agents | sidecar | smoke | 3/3 | - | 29.6 | 30.1 | 24.5 | 10 | 5 | 134 | max_tokens,messages,metadata,model,stream,stream_options,temperature,tools |
| echo-openai-agents | sidecar | simplifier | 3/3 | - | 37.5 | 162.6 | 30.3 | 10 | 5 | 187 | max_tokens,messages,metadata,model,stream,stream_options,temperature,tools |
| echo-openai-agents | sidecar | lookup | 3/3 | 3/3 | 84.8 | 90.7 | 69.7 | 30 | 15 | 152/497/796 | max_tokens,messages,metadata,model,stream,stream_options,temperature,tools |
| echo-typescript | sidecar | smoke | 3/3 | - | 21.6 | 30.1 | 29.3 | 10 | 5 | 134 | max_tokens,messages,metadata,model,stream,stream_options,temperature,tools |
| echo-typescript | sidecar | simplifier | 3/3 | - | 26.1 | 28.5 | 20.8 | 10 | 5 | 187 | max_tokens,messages,metadata,model,stream,stream_options,temperature,tools |
| echo-typescript | sidecar | lookup | 3/3 | 3/3 | 52.7 | 66.0 | 47.0 | 30 | 15 | 152/494/790 | max_tokens,messages,metadata,model,stream,stream_options,temperature,tools |

## Mapping size

| Engine | Code lines | Files |
| ------ | ---------- | ----- |
| echo-python | 243 | handle.py, tools.py |
| echo-pydanticai | 181 | mapping.py, handle.py |
| echo-openai-agents | 295 | mapping.py, handle.py, tools.py |
| echo-typescript | 551 | a2a_server.ts, handle.ts, schema.ts, tools.ts |

## What this shows

- Criterion 1: every new Python workload (here `echo-openai-agents`) runs offline against the fake model server, in memory (`inprocess`) and on localhost (`sidecar`).
- Criterion 2 (the non-Python agent): `echo-typescript` passes the same smoke and tasks behind the generic sidecar connector.
- Criterion 3: the benchmark kit runs the same three tasks on every engine through the same `/v1/run` call. The scorecard is `notes/2026-10-09-scorecard.md`.
- The untrusted engines (`echo-smolagents`, `echo-claude-agent`) pass smoke in the `remote` lane; `kagent-adk` is SKIP here by design (kind, from CI).
- Not shown: hosted-model tokens and latency (criterion 5), the load and hostile suites (criterion 4), and anything on kind. Those wait on the Mac run and on `poc06-kind.yml`.

Result: every step ok. Total time 128 s. Last command exit code: 0.
