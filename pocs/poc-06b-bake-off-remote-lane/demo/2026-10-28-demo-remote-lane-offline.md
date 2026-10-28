# PoC-6b offline demo: the remote lane on the fake model

Where it ran: localhost on Linux 6.18.44-fc-v180, 4 CPUs. Processes on random ports; the model is the fake model server (`packages/fake-model-server/scripts/bakeoff.yaml`). No Docker, no kind, no key.
Commit: `7dc0774` (1 changed or untracked paths at the start). Date: 2026-10-28T11:26:36Z.
Script: `pocs/poc-06b-bake-off-remote-lane/demo/demo.sh`. The uv `UV_NATIVE_TLS` deprecation warning is dropped from the output.
Not here: kind (`kagent-adk`, the hostile and load suites; see `notes/2026-10-09-lanes-b-kind.md`) and hosted-model numbers (the Mac run, `make poc06-mac`).

## 1. The TypeScript agent: make ts-check

$ make ts-check

```
(558 earlier lines trimmed)
  ...
1..84
# tests 84
# suites 0
# pass 84
# fail 0
# cancelled 0
# skipped 0
# todo 0
# duration_ms 5141.014808

> echo-typescript@0.1.0 build
> tsc -p tsconfig.build.json

```

exit code: 0

## 2. The super simple task on every remote engine: bakeoff smoke --lane remote

$ uv run python -m bakeoff smoke --lane remote

```
PASS echo-python         remote
PASS echo-pydanticai     remote
PASS echo-langgraph      remote
PASS echo-openai-agents  remote
PASS echo-typescript     remote
PASS echo-smolagents     remote
PASS echo-claude-agent   remote
SKIP kagent-adk          remote      runs only on kind (poc06-kind.yml)
```

exit code: 0

## 3. The three tasks on the untrusted engines and the TypeScript agent: bakeoff run

$ uv run python -m bakeoff run --engine echo-smolagents,echo-claude-agent,echo-typescript --lane remote --tasks smoke,simplifier,lookup --repeat 3 --out /tmp/tmp.h2mfekhGNV/run

```
(1 earlier lines trimmed)
  echo-smolagents remote: smoke x3
  echo-smolagents remote: simplifier x3
  echo-smolagents remote: lookup x3
echo-claude-agent remote
  echo-claude-agent remote: smoke x3
  echo-claude-agent remote: simplifier x3
  echo-claude-agent remote: lookup x3
echo-typescript remote
  echo-typescript remote: smoke x3
  echo-typescript remote: simplifier x3
  echo-typescript remote: lookup x3
wrote /tmp/tmp.h2mfekhGNV/run/results.json and /tmp/tmp.h2mfekhGNV/run/results.md
```

exit code: 0

`results.md` of that run (offline, fake model; the numbers are the stack's own time):

# Bake-off results

Tasks: smoke, simplifier, lookup. Repeat: 3. Model: fake model server (scripted). Generated: 2026-10-28T11:28:12Z.

| Engine | Lane | Task | Pass | Tool calls | p50 ms | p95 ms | TTFT p50 ms | In tok | Out tok | Prompt bytes per call | Request body keys |
| ------ | ---- | ---- | ---- | ---------- | ------ | ------ | ----------- | ------ | ------- | --------------------- | ----------------- |
| echo-smolagents | remote | smoke | 3/3 | - | 64.1 | 180.8 | 51.9 | 10 | 5 | 9235 | max_tokens,messages,metadata,model,temperature |
| echo-smolagents | remote | simplifier | 3/3 | - | 53.0 | 68.9 | 53.2 | 10 | 5 | 9288 | max_tokens,messages,metadata,model,temperature |
| echo-smolagents | remote | lookup | 3/3 | 3/3 | 132.8 | 140.5 | 122.0 | 30 | 15 | 9253/9738/10172 | max_tokens,messages,metadata,model,temperature |
| echo-claude-agent | remote | smoke | 3/3 | - | 535.8 | 667.1 | 476.8 | 10 | 5 | 1659 | max_tokens,messages,metadata,model,stream,stream_options,temperature,tools |
| echo-claude-agent | remote | simplifier | 3/3 | - | 553.2 | 573.6 | 529.5 | 10 | 5 | 1712 | max_tokens,messages,metadata,model,stream,stream_options,temperature,tools |
| echo-claude-agent | remote | lookup | 3/3 | 3/3 | 581.8 | 583.1 | 543.5 | 30 | 15 | 1677/2113/2503 | max_tokens,messages,metadata,model,stream,stream_options,temperature,tools |
| echo-typescript | remote | smoke | 3/3 | - | 29.5 | 179.3 | 24.6 | 10 | 5 | 134 | max_tokens,messages,metadata,model,stream,stream_options,temperature,tools |
| echo-typescript | remote | simplifier | 3/3 | - | 30.7 | 31.4 | 23.2 | 10 | 5 | 187 | max_tokens,messages,metadata,model,stream,stream_options,temperature,tools |
| echo-typescript | remote | lookup | 3/3 | 3/3 | 63.4 | 67.8 | 50.9 | 30 | 15 | 152/494/790 | max_tokens,messages,metadata,model,stream,stream_options,temperature,tools |

## Mapping size

| Engine | Code lines | Files |
| ------ | ---------- | ----- |
| echo-smolagents | 380 | mapping.py, handle.py, tools.py, agent.py |
| echo-claude-agent | 310 | mapping.py, handle.py |
| echo-typescript | 551 | a2a_server.ts, handle.ts, schema.ts, tools.ts |

## 4. The PoC-6b scenario tests: make test-poc POC=06b

$ make test-poc POC=06b

```
(35 earlier lines trimmed)
pocs/poc-06b-bake-off-remote-lane/tests/test_poc06b_remote_engine_contract.py::TestRemoteEngineOverA2A::test_the_engine_answers_the_smoke_task[echo-smolagents]
  /home/user/agent-playground/.venv/lib/python3.12/site-packages/pytest_socket/__init__.py:138: UserWarning: A test tried to use socket.socket.
    raise SocketBlockedError()

-- Docs: https://docs.pytest.org/en/stable/how-to/capture-warnings.html
================= 236 passed, 86 skipped, 5 warnings in 15.75s =================
```

exit code: 0

## What this shows

- Criterion 1: `echo-smolagents` and `echo-claude-agent` run offline in the `remote` lane against the fake model server and pass the `EngineConnectorContract` over A2A (`tests/test_poc06b_remote_engine_contract.py`, in step 4).
- Criterion 2: the TypeScript agent runs the same tasks as a remote, and the plain-A2A mode passes the suite against a stub shaped like the kagent probe (`tests/test_poc06b_plain_a2a_contract.py`). The real `kagent-adk` is SKIP here by design: kind, from CI.
- Criterion 3: the benchmark kit runs the same three tasks on every remote engine through the same `/v1/run` call. The scorecard is `../poc-06a-bake-off-sidecar-lane/notes/2026-10-09-scorecard.md`.
- Criterion 8: the contract freeze is pinned by `packages/chassis/tests/test_contract_freeze.py`, not by this demo.
- Not shown: kind (criteria 2 and 4 on the cluster; the CI run table in `notes/2026-10-09-lanes-b-kind.md`) and hosted-model tokens (criterion 5, the Mac run).

Result: every step ok. Total time 115 s. Last command exit code: 0.
