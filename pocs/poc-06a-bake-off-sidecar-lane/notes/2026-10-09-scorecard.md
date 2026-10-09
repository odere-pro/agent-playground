# PoC-6a offline bake-off scorecard (2026-10-09)

## Why

PoC-6 part A, task T-SCORE: the 12 bake-off criteria of `docs/planning/poc/000-plan.md#bake-off-criteria`, one table per criterion, one row per engine, for the 8 engines. Everything measured here is **offline**: the fake model server, processes on localhost, no key, no hosted model, no SLM. Read the numbers as orders of magnitude and as the shape of each framework's overhead, not as a budget. The record test is `tests/test_poc06a_scorecard_records.py`. This note is input to ADR-006, not the decision.

What is not measured here, and where it comes from:

| Item | Source |
| ---- | ------ |
| Real tokens per call, real latency, real TTFT, hosted model quality | The Mac run (`make poc06-mac`), hosted model through the chassis |
| The SLM (vLLM, llama.cpp) behavior of every engine | The Mac run, PoC-6c |
| kagent-adk numbers (image size, RSS, latency, cold start, streaming, tokens) | kind, from CI (`poc06-kind.yml`). The cells say "not measured: kind, from CI" |
| kind pod memory of every engine | CI (`poc06-kind.yml`) |
| Days of mapping effort | Not measured: a person's time is not an offline number. Code lines stand in |

## Setup

- Commit: `60746b5` (branch `poc06/score`), plus the files this note lists. `git status --short` before the run was empty. The run files were added after it.
- Machine: Linux 6.18.44-fc-v80 x86_64 (Firecracker VM), 4 CPUs (Intel Xeon @ 2.80GHz), 16 GB RAM. No Docker, no kind.
- Python 3.12.3, uv 0.11.32, Node v22.22.0. Packages from `uv.lock`: pydantic-ai-slim 2.52.0, langgraph 1.2.12, langchain-openai 1.6.7, openai-agents 0.23.1, smolagents 1.26.0, claude-agent-sdk 0.2.165 (CLI 2.1.294 bundled), mcp 2.2.0, openai 3.22.1, a2a-sdk 1.2.0. Node: @a2a-js/sdk 1.3.0, @modelcontextprotocol/sdk 1.32.1.
- The stack per cell: fake model server (a thread of the kit) <- chassis (`profile local`, litellm adapter at the fake) <- workload on loopback. Lane per row: `sidecar` for trusted engines, `remote` for untrusted ones (the trust rule). Each lane in `results.md`.
- Repeat 10 per task. A repetition is one complete and one streaming `/v1/run`. p95 over 10 samples is near the maximum.
- Load was not logged. The PyPI metadata lookups (criteria 10 to 12) ran while the later cells of the bakeoff run were going, a few small network calls; they may add noise to the last engines' rows. Each section ran once; compare rows inside a table, not across tables.

## Commands

The run (exit 0). Files `results.json` and `results.md` are in `2026-10-09-bakeoff-offline/`:

```
uv run python -m bakeoff run --tasks smoke,simplifier,lookup --repeat 10 --out pocs/poc-06a-bake-off-sidecar-lane/notes/2026-10-09-bakeoff-offline/
```

The extra measurements (RSS, cold start, streaming deltas, router keys), exit 0. Output `2026-10-09-bakeoff-offline/extra.json`:

```
uv run python pocs/poc-06a-bake-off-sidecar-lane/demo/measure_extra.py --out pocs/poc-06a-bake-off-sidecar-lane/notes/2026-10-09-bakeoff-offline
```

What `measure_extra.py` does: RSS is the sum of `VmRSS` over the workload's process group, read from `/proc`: idle (stack ready, no request), after one warm-up run, the peak sampled every 20 ms during 10 complete runs of each of the 3 tasks, and after them. Cold start is a fresh workload process alone (sidecar argv, no token) from just before spawn to the first HTTP 200 on `/.well-known/agent-card.json`, polled every 5 ms, 5 samples. Deltas per answer come from the streamed `/v1/run` (SSE), 10 streams per task. Router keys come from running each Python `handle` in the script's process against a second, recording fake model.

Package metadata:

```
uv run pip index versions openinference-instrumentation-<name>     # names in criterion 10
echo "<pkg>[temporal]==<ver>" | uv pip compile - --python-version 3.12      # criterion 11
echo "<pkg>" | uv pip compile - --python-version 3.12 --exclude-newer 2025-10-09T00:00:00Z   # criterion 12
uv run python -c "importlib.metadata ..."                           # licenses
```

## Output tails

The run log (the `x10` progress lines are trimmed; the order is the run order):

```
(progress lines '  <engine> <lane>: <task> x10' trimmed; warning about UV_NATIVE_TLS trimmed)
echo-python inprocess
echo-python sidecar
echo-python remote
echo-pydanticai inprocess
echo-pydanticai sidecar
echo-pydanticai remote
echo-langgraph inprocess
echo-langgraph sidecar
echo-langgraph remote
echo-openai-agents inprocess
echo-openai-agents sidecar
echo-openai-agents remote
echo-typescript sidecar
echo-typescript remote
echo-smolagents remote
echo-claude-agent remote
kagent-adk remote
wrote pocs/poc-06a-bake-off-sidecar-lane/notes/2026-10-09-bakeoff-offline/results.json and pocs/poc-06a-bake-off-sidecar-lane/notes/2026-10-09-bakeoff-offline/results.md
exit 0
```

Pass columns of `results.md` (all rows; the Pass columns are repetitions that passed one complete and one streaming run):

*Table caption: offline, fake model, localhost processes; not a hosted-model or SLM number.*

| Engine | Lane | smoke | simplifier | lookup | lookup tool calls |
| --- | --- | --- | --- | --- | --- |
| echo-python | inprocess | 10/10 | 10/10 | 10/10 | 10/10 |
| echo-python | sidecar | 10/10 | 10/10 | 10/10 | 10/10 |
| echo-python | remote | 10/10 | 10/10 | 10/10 | 10/10 |
| echo-pydanticai | inprocess | 10/10 | 10/10 | 10/10 | 10/10 |
| echo-pydanticai | sidecar | 10/10 | 10/10 | 10/10 | 10/10 |
| echo-pydanticai | remote | 10/10 | 10/10 | 10/10 | 10/10 |
| echo-langgraph | inprocess | 10/10 | 10/10 | 10/10 | 10/10 |
| echo-langgraph | sidecar | 10/10 | 10/10 | 10/10 | 10/10 |
| echo-langgraph | remote | 10/10 | 10/10 | 10/10 | 10/10 |
| echo-openai-agents | inprocess | 10/10 | 10/10 | 10/10 | 10/10 |
| echo-openai-agents | sidecar | 10/10 | 10/10 | 10/10 | 10/10 |
| echo-openai-agents | remote | 10/10 | 10/10 | 10/10 | 10/10 |
| echo-typescript | sidecar | 10/10 | 10/10 | 10/10 | 10/10 |
| echo-typescript | remote | 10/10 | 10/10 | 10/10 | 10/10 |
| echo-smolagents | remote | 10/10 | 10/10 | 10/10 | 10/10 |
| echo-claude-agent | remote | 10/10 | 10/10 | 10/10 | 10/10 |
| kagent-adk | remote | SKIP | SKIP | SKIP | runs only on kind (poc06-kind.yml) |

All 16 cells that can run here pass 30 of 30. The 17th, `kagent-adk`, is SKIP by design: it runs only on kind.

The extra run (JSON lines trimmed to the engine, the lane, and the RSS fields; the rest is in `extra.json`):

```
{"engine": "echo-python", "lane": "sidecar", "rss_idle_kb": 89056, "rss_warm_kb": 93496, "rss_peak_kb": 95740, "rss_after_kb": 95700, "cold_start_ms": [1375.3657429997475, 1395.316985000136, 1537.7406899997368, 1320.0064290003866, 1323.2967990006728]}
{"engine": "echo-pydanticai", "lane": "sidecar", "rss_idle_kb": 134080, "rss_warm_kb": 140220, "rss_peak_kb": 142780, "rss_after_kb": 142660, "cold_start_ms": [2864.948798000114, 3632.462654999472, 2803.2728119997046, 2717.592494999735, 2752.771882000161]}
{"engine": "echo-langgraph", "lane": "sidecar", "rss_idle_kb": 135740, "rss_warm_kb": 140124, "rss_peak_kb": 141816, "rss_after_kb": 141852, "cold_start_ms": [2327.389365000272, 2480.1518410004064, 2282.183895000344, 2517.332245000034, 2438.0818150002597]}
{"engine": "echo-openai-agents", "lane": "sidecar", "rss_idle_kb": 120852, "rss_warm_kb": 132128, "rss_peak_kb": 133816, "rss_after_kb": 133612, "cold_start_ms": [2415.9874980005043, 2226.1809060000814, 2398.4443649997047, 2590.6454880005185, 2456.0549529996933]}
{"engine": "echo-typescript", "lane": "sidecar", "rss_idle_kb": 96140, "rss_warm_kb": 98868, "rss_peak_kb": 117732, "rss_after_kb": 117732, "cold_start_ms": [418.40778199912165, 418.35639200053265, 394.03234200017323, 390.5792929999734, 392.5370529996144]}
{"engine": "echo-smolagents", "lane": "remote", "rss_idle_kb": 121156, "rss_warm_kb": 126032, "rss_peak_kb": 131608, "rss_after_kb": 131584, "cold_start_ms": [2101.3537879998694, 2137.7627640003993, 2040.1378730002762, 2199.5283149999523, 1988.779663999594]}
{"engine": "echo-claude-agent", "lane": "remote", "rss_idle_kb": 90716, "rss_warm_kb": 322488, "rss_peak_kb": 335836, "rss_after_kb": 330900, "cold_start_ms": [1385.5045569998765, 1354.1933639999115, 1461.38518799944, 1430.014869999468, 1351.6732460002459]}
SKIP kagent-adk: runs only on kind (poc06-kind.yml)
exit 0
```

## Criterion 1: event mapping effort

Code lines (source lines minus blanks, comments, and docstrings) over the engine's own mapping files, from `bakeoff`'s `lines`. Offline count; days are not measured.

*Table caption: offline, fake model, localhost processes; not a hosted-model or SLM number.*

| Engine | Mapping files | Code lines (bakeoff `lines`) | Against echo-python |
| --- | --- | ---: | ---: |
| echo-python | handle.py, tools.py | 243 | baseline |
| echo-pydanticai | mapping.py, handle.py | 181 | -62 (-26%) |
| echo-langgraph | mapping.py, handle.py, tools.py | 224 | -19 (-8%) |
| echo-openai-agents | mapping.py, handle.py, tools.py | 295 | +52 (+21%) |
| echo-typescript | a2a_server.ts, handle.ts, schema.ts, tools.ts | 551 | +308 (+127%) |
| echo-smolagents | mapping.py, handle.py, tools.py, agent.py | 380 | +137 (+56%) |
| echo-claude-agent | handle.py (bakeoff list) | 168 | -75 (-31%) |
| echo-claude-agent (corrected) | handle.py, mapping.py (counted by hand, same counter) | 310 | +67 (+28%) |
| kagent-adk | none in the repo | not measured: the plain-A2A reader is chassis-side (contract v5 part B), not built at this commit | n/a |

Reading: pydantic-ai needs the least code, the TypeScript agent the most (it also carries its own A2A server, which Python engines get from `workload-a2a`). **A bakeoff defect:** `registry.py` lists `handle.py` and `tools.py` for echo-claude-agent, but `tools.py` does not exist and `mapping.py` is not listed. So `bakeoff` reports 168 lines, which is `handle.py` alone. The honest count is `handle.py` (168) plus `mapping.py` (142) = 310. I did not edit the package; the fix is two names in `registry.py`.

## Criterion 2: streaming fidelity

Streamed `POST /v1/run` through the chassis, 10 streams per task, fake model, localhost. TTFT is the time to the first `delta` frame. The fake model emits 13 chunks for the simplifier answer, so an engine that forwards each chunk shows 13 deltas. The mean gap is the time from the first to the last delta divided by the deltas minus one: the overhead per streamed delta, with no model time in it.

*Table caption: offline, fake model, localhost processes; not a hosted-model or SLM number.*

| Engine | Lane | Deltas per simplifier answer (min/med/max) | TTFT p50 ms, streamed simplifier | TTFT vs baseline | Mean gap between deltas ms | Event types on the stream (lookup) |
| --- | --- | --- | ---: | ---: | ---: | --- |
| echo-python | sidecar | 13/13/13 | 54.5 | baseline | 0.49 | delta, end, metrics, start, tool_call |
| echo-pydanticai | sidecar | 13/13/13 | 37.5 | -17.0 ms | 1.30 | delta, end, metrics, start, tool_call |
| echo-langgraph | sidecar | 13/13/13 | 56.5 | +2.0 ms | 0.84 | delta, end, metrics, start, tool_call |
| echo-openai-agents | sidecar | 13/13/13 | 43.8 | -10.7 ms | 0.78 | delta, end, metrics, start, tool_call |
| echo-typescript | sidecar | 13/13/13 | 29.4 | -25.1 ms | 0.35 | delta, end, metrics, start, tool_call |
| echo-smolagents | remote | 1/1/1 | 58.8 | +4.3 ms | n/a (one delta) | delta, end, metrics, start, tool_call |
| echo-claude-agent | remote | 13/13/13 | 548.1 | +493.6 ms | 1.11 | delta, end, metrics, start, tool_call |
| kagent-adk | remote | not measured: kind, from CI. Probe: text parts only (about 7 artifact chunks, the last repeats the text) | not measured: kind, from CI | n/a | n/a | text parts; no `tool_call`, no `start` |

- Streamed events: `delta`, `tool_call`, `metrics`, `start`, `end` for every engine with a stream, `tool_call` after the tool ran (not before).
- echo-smolagents gives **one** final `delta`: the answer is a string inside `final_answer(...)`, so the user sees nothing until the run is done (README, "Streaming fidelity").
- echo-claude-agent streams partial messages (13 deltas, the same as the Chat Completions engines). Its TTFT is about 0.5 s above the baseline: each run starts one `claude` CLI process (a 242 MB binary).
- kagent-adk (plain-A2A mode, from the probe note `pocs/poc-06b-bake-off-remote-lane/notes/2026-10-09-kagent-probe.md`): text parts only, no `tool_call` event, no `start`; the last artifact event repeats the whole text and must not become a delta. Not measured here: kind, from CI.
- TTFT differences of a few tens of ms between the Chat Completions engines are within the spread I saw. The only large difference is Claude.

## Criterion 3: tool support

Lookup task, 10 repetitions: the two tools called in order with schema-valid arguments, from `results.md` ("lookup tool calls" in the pass table above).

| Engine | Mechanism | Lookup tool calls ok | Visible to the chassis |
| ------ | --------- | -------------------- | ---------------------- |
| echo-python | Native OpenAI tool calls over MCP, loop by hand | 10/10 | yes, `tool_call` events |
| echo-pydanticai | Native tool calls, `MCPToolset` | 10/10 | yes |
| echo-langgraph | Native tool calls, `tools_condition` loop, stand-in MCP tools (the locked `langchain-mcp-adapters` does not import against `mcp` 2) | 10/10 | yes |
| echo-openai-agents | Native tool calls, `MCPServerStreamableHttp` subclass | 10/10 | yes |
| echo-typescript | Native tool calls over MCP, loop by hand | 10/10 | yes |
| echo-smolagents | **Code actions**: the model writes Python that calls the tool as a function; no OpenAI `tools` are sent. Stand-in MCP tools (the locked `mcpadapt` does not import against `mcp` 2) | 10/10 | yes, the tool wrapper emits `tool_call` |
| echo-claude-agent | Native tool use (Anthropic Messages), MCP server in the CLI; built-in `Bash`, `Read`, `Write` are also on | 10/10 | yes. Built-in tool names stay (`Bash`) |
| kagent-adk | ADK tools inside the agent | not measured: kind, from CI | **no**: tools and results are not visible to the chassis (contract v5 B.8) |

Guided-decoding JSON is not used by any engine here.

## Criterion 4: model agnostic

Offline claim: each engine reaches the model only through the chassis model proxy, and the proxy is the only holder of the key. Every cell above ran with the proxy in the path and the litellm adapter pointed at the fake. The hosted-model and SLM claims are not measured here.

| Engine | Wire format to the proxy | Passed through the proxy offline | Hosted model and SLM |
| ------ | ------------------------ | -------------------------------- | -------------------- |
| echo-python, echo-pydanticai, echo-langgraph, echo-openai-agents, echo-typescript | Chat Completions (`POST /v1/chat/completions`) | yes (30/30 per cell) | not measured: Mac run |
| echo-smolagents | Chat Completions, no `tools`, code-format replies | yes (30/30) | not measured: Mac run. Needs a model that follows the `<code>` format; an SLM may not |
| echo-claude-agent | **Anthropic Messages** (`POST /v1/messages` on the proxy; contract v5 A), mapped onto the same model port, so any LiteLLM route works behind it | yes (30/30) | not measured: Mac run. Claude Code's 24-tool prompt on a small context window is unseen (README) |
| kagent-adk | OpenAI-compatible `baseUrl`, `apiKeyPassthrough` (probe) | not measured: kind, from CI. The probe showed the bearer and `traceparent` reach a fake model from the Python runtime alone, not through the controller and Substrate | not measured: Mac run |

## Criterion 5: token overhead

Offline proxy: the UTF-8 bytes of the `messages` JSON the fake model received per call, first repetition, through the chassis. The tools list is not counted. **Real tokens come from the Mac run.** The fake model's `In tok` and `Out tok` are scripted (10/5 for smoke and simplifier, 30/15 for lookup, identical for every engine) and say nothing about cost.

*Table caption: offline, fake model, localhost processes; not a hosted-model or SLM number.*

| Engine | Lane | Model calls (lookup) | Prompt bytes per call, lookup | Lookup total bytes | Against echo-python | simplifier bytes | smoke bytes |
| --- | --- | ---: | --- | ---: | ---: | ---: | ---: |
| echo-python | sidecar | 3 | 152/497/796 | 1445 | baseline | 187 | 134 |
| echo-pydanticai | sidecar | 3 | 152/494/790 | 1436 | -9 B (-1%) | 187 | 134 |
| echo-langgraph | sidecar | 3 | 152/497/796 | 1445 | +0 B (+0%) | 187 | 134 |
| echo-openai-agents | sidecar | 3 | 152/497/796 | 1445 | +0 B (+0%) | 187 | 134 |
| echo-typescript | sidecar | 3 | 152/494/790 | 1436 | -9 B (-1%) | 187 | 134 |
| echo-smolagents | remote | 3 | 9253/9738/10172 | 29163 | +27718 B (+1918%) | 9288 | 9235 |
| echo-claude-agent | remote | 3 | 1676/2112/2502 | 6290 | +4845 B (+335%) | 1711 | 1658 |
| kagent-adk | remote | not measured: kind, from CI | not measured: kind, from CI | not measured: kind, from CI | n/a | not measured: kind, from CI | not measured: kind, from CI |

Reading: the Chat Completions engines (python, pydantic-ai, langgraph, openai-agents, typescript) send the same prompt to within 9 bytes. The framework adds nothing to the messages. smolagents adds about 9 KB to every call (its code-agent system prompt), 20 times the baseline over a lookup. Claude adds about 1.5 KB per call (system block and an environment block), 4.4 times the baseline. In tokens that is a rough 4 bytes per token, an assumption, not a measurement.

## Criterion 6: latency

p50 and p95 of the complete `/v1/run` in milliseconds, wall time, 10 repetitions, fake model with no model delay, so the numbers are the stack's own time. Cold start: a workload process alone, spawn to the agent card (5 samples; Python includes imports and uvicorn).

*Table caption: offline, fake model, localhost processes; not a hosted-model or SLM number.*

| Engine | Lane | smoke p50 / p95 | simplifier p50 / p95 | lookup p50 / p95 | Cold start ms, spawn to card (min / median / max of 5) |
| --- | --- | --- | --- | --- | --- |
| echo-python | sidecar | 66.9 / 77.8 | 87.0 / 101.1 | 150.9 / 176.5 | 1320 / 1375 / 1538 |
| echo-pydanticai | sidecar | 42.9 / 51.9 | 54.5 / 67.5 | 108.5 / 127.3 | 2718 / 2803 / 3632 |
| echo-langgraph | sidecar | 45.2 / 54.3 | 59.6 / 66.9 | 137.6 / 153.3 | 2282 / 2438 / 2517 |
| echo-openai-agents | sidecar | 47.9 / 63.4 | 70.8 / 77.4 | 118.6 / 155.1 | 2226 / 2416 / 2591 |
| echo-typescript | sidecar | 29.5 / 34.5 | 32.1 / 36.9 | 76.0 / 91.3 | 391 / 394 / 418 |
| echo-smolagents | remote | 72.5 / 160.9 | 68.0 / 83.9 | 143.6 / 154.8 | 1989 / 2101 / 2200 |
| echo-claude-agent | remote | 651.5 / 1002.5 | 616.3 / 683.2 | 708.4 / 796.1 | 1352 / 1386 / 1461 |
| kagent-adk | remote | not measured: kind, from CI. Probe: 0.64 s whole stream with OTel exporters off, 16.8 s with defaults (fake model, local) | same | same | not measured: kind, from CI |

- The TypeScript agent is the fastest in every task and starts in about 0.4 s. The Python workloads start in 1.3 to 3.6 s; pydantic-ai is the slowest (2.7 to 3.6 s) and echo-python and the Claude agent the quickest (about 1.4 s).
- echo-python is slower than the framework engines at the same lane (smoke p50 67 ms vs 43 to 48). I re-ran echo-python alone in the sidecar lane to rule out run order (it ran first): smoke p50 56.0 ms, simplifier 65.6, lookup 131.2 (command: `uv run python -m bakeoff run --engine echo-python --lane sidecar --tasks smoke,simplifier,lookup --repeat 10`, output not committed). Still slower than pydantic-ai's 41.7, 68.2, 105.5 (same kind of rerun). PoC-2 counted 7 MCP HTTP requests per run for echo-python against 3 for PydanticAI (`pocs/poc-02-two-engines-one-contract/notes/2026-10-01-measurements.md`), which is a likely cause, but I did not re-count them here.
- echo-claude-agent adds about 0.55 s per run: one CLI process per run. smolagents is near the baseline (the model call is scripted; real latency will add the model's time to every engine, and smolagents makes the same number of calls).
- kagent-adk: not measured: kind, from CI. From the probe: 0.64 s for a whole stream with the OTel exporters off, 16.8 s with the default OTel settings (not root-caused).

## Criterion 7: footprint

Image size is from CI, not measured here: run 37997346774 on commit `772b9a6` (ubuntu-latest, `docker image ls`, uncompressed). The chassis image is 271 MB. RSS is the workload process tree on this VM (no Docker, so no container overhead). For echo-claude-agent the tree includes the `claude` CLI process (the capture note saw 175 to 240 MB for the CLI alone).

*Table caption: offline, fake model, localhost processes; not a hosted-model or SLM number.*

| Engine | Image MB (CI run 37997346774, `772b9a6`) | RSS idle MB | RSS after warm-up MB | RSS peak during 30 runs MB | RSS after 10 runs of each task MB |
| --- | ---: | ---: | ---: | ---: | ---: |
| echo-python | 181 | 87.0 | 91.3 | 93.5 | 93.5 |
| echo-pydanticai | 255 | 130.9 | 136.9 | 139.4 | 139.3 |
| echo-langgraph | 252 | 132.6 | 136.8 | 138.5 | 138.5 |
| echo-openai-agents | 209 | 118.0 | 129.0 | 130.7 | 130.5 |
| echo-typescript | 252 | 93.9 | 96.6 | 115.0 | 115.0 |
| echo-smolagents | 256 | 118.3 | 123.1 | 128.5 | 128.5 |
| echo-claude-agent | 434 | 88.6 | 314.9 | 328.0 | 323.1 |
| kagent-adk | not measured: kind, from CI | not measured: kind, from CI | not measured: kind, from CI | not measured: kind, from CI | not measured: kind, from CI |

- Memory per replica (kind): not measured: kind, from CI.
- The idle RSS of echo-claude-agent is small (the CLI is not started yet); the CLI process adds about 230 MB when a run starts, and that tree stayed at about 323 MB after the runs.
- kagent-adk: image and RSS not measured: kind, from CI. The probe note lists chart requests (controller 128Mi request, 512Mi limit; UI 256Mi; PostgreSQL and Substrate on top). Those are configuration, not measurements.

## Criterion 8: statelessness

| Engine | Hidden state | Can it move out |
| ------ | ------------ | --------------- |
| echo-python | None between runs. One module counter (`tools.list_failures`). Each MCP operation opens its own session | n/a |
| echo-pydanticai | New `Agent` per run, no message history passed. README has no hidden-state section; this is from `handle.py` | n/a |
| echo-langgraph | Graph compiled without a checkpointer (`graph.compile()`), so no thread state. From `handle.py`; no README section | A checkpointer is opt-in |
| echo-openai-agents | None kept. SDK globals exist (tracing provider, default client); the workload turns tracing off and passes an explicit client. No `Session`. `test_two_runs_share_no_state` | Sessions are opt-in |
| echo-typescript | A2A task store prunes finished tasks 3 s after they end (`PruningTaskStore`) | n/a |
| echo-smolagents | New `CodeAgent`, executor, and monitor per run. A daemon worker thread finishes its step after an early stop. Nothing written to disk (tested) | n/a |
| echo-claude-agent | A per-run `HOME` with the transcript (holds the prompt and every tool result), `.claude.json`, and caches; removed after the run. Session resume off | Writable `HOME` and `/tmp` needed; it is state on disk for the length of a run |
| kagent-adk | **Stateful**: sessions, tasks, and events live in PostgreSQL behind the controller; the ADK runtime keeps its own task store (in memory in the probe) | No: the chassis cannot clear it. `context_id: omit` limits reuse (contract v5 B.8) |

## Criterion 9: lane under the trust rule

The trust rule (ADR-001 item 8, PoC-5): a workload that runs model-chosen code, shell commands, or file writes is `untrusted` and goes to `remote`. Otherwise `sidecar`.

| Engine | Lane | Why |
| ------ | ---- | --- |
| echo-python, echo-pydanticai, echo-langgraph, echo-openai-agents | `sidecar` (also `inprocess`) | Model calls and MCP tool calls only; no subprocess, no file write |
| echo-typescript | `sidecar` | Same; Node process, no inprocess lane (the chassis loads Python handles only) |
| echo-smolagents | `remote` | The model's Python runs in the workload process (`LocalPythonExecutor`, an AST interpreter with deny lists, not a boundary). It writes nothing to disk |
| echo-claude-agent | `remote` | The model can run `Bash` and write files; one CLI process per run; writes under `HOME`. Child processes of `Bash` inherit the per-remote token |
| kagent-adk | `remote` | Third-party runtime with its own services (controller, PostgreSQL, Substrate); not ours to put in a sidecar |

The matrix is also enforced: `bakeoff` and `spec.trust` refuse an untrusted engine in any other lane (every untrusted row above ran in `remote` only).

## Criterion 10: observability hooks

Checked with `uv run pip index versions <name>` on 2026-10-09 (UTC), and the installed package metadata for native OpenTelemetry. "Found" means the package exists on PyPI; I did not install or run any instrumentor.

| Engine | OpenInference instrumentor on PyPI | Native OpenTelemetry |
| ------ | ---------------------------------- | -------------------- |
| echo-python | `openinference-instrumentation-httpx`: none found. `openinference-instrumentation-openai` 0.1.65 exists but wraps the OpenAI SDK, which echo-python does not use | None (hand-written httpx). Trace context goes by the `traceparent` header |
| echo-pydanticai | `openinference-instrumentation-pydantic-ai` 0.1.30 (`...-pydanticai`: none found) | Yes: `pydantic-ai-slim` requires `opentelemetry-api` and has a `logfire` extra (metadata) |
| echo-langgraph | `openinference-instrumentation-langgraph`: none found. `openinference-instrumentation-langchain` 0.1.79 exists; that it covers LangGraph runs is from docs, not verified | None in the package metadata |
| echo-openai-agents | `openinference-instrumentation-openai-agents` 2.5.3 | The SDK has its own tracing with a processor interface (not OTel); the workload turns it off |
| echo-typescript | not checked on PyPI. npm: `@arizeai/openinference-instrumentation-openai` 4.4.1 and `@arizeai/openinference-instrumentation-langchain` 4.1.4 exist; none for a hand-written fetch loop | `@opentelemetry/api` 1.9.1 exists; the agent does not use it |
| echo-smolagents | `openinference-instrumentation-smolagents` 0.1.43 | Optional extra `telemetry` (`opentelemetry-sdk`, `opentelemetry-exporter-otlp`; metadata) |
| echo-claude-agent | `openinference-instrumentation-claude-agent-sdk` 0.1.21 | Optional extra `otel` (`opentelemetry-api`; metadata). The CLI process is a binary |
| kagent-adk | `openinference-instrumentation-google-adk` 1.0.4 (ADK underneath) | Yes: the probe saw OTel exporters and a 16.8 s flush delay with defaults |

## Criterion 11: durability

"Verified" means the package extra resolved with `uv pip compile` (resolving only, no install).

| Engine | Temporal integration | Status |
| ------ | -------------------- | ------ |
| echo-python | None (no framework). A Temporal activity could wrap `handle` | n/a |
| echo-pydanticai | `pydantic-ai-slim[temporal]==2.52.0` resolves `temporalio==1.34.0`; extras `temporal` and `dbos` in the installed metadata | verified (resolves; not run) |
| echo-langgraph | The plan lists LangGraph as supported. The installed `langgraph` 1.2.12 has no `temporal` extra | from docs, not verified |
| echo-openai-agents | `openai-agents[temporal]==0.23.1` resolves `temporalio==1.33.0` | verified (resolves; not run) |
| echo-typescript | Temporal's TypeScript SDK is separate; no agent-level integration known | from docs, not verified |
| echo-smolagents | No `temporal` extra in the installed metadata | none found; docs not checked |
| echo-claude-agent | No `temporal` extra in the installed metadata | none found; docs not checked |
| kagent-adk | Not Temporal: sessions and tasks in PostgreSQL, actors in Substrate (alpha) | from the probe note, not verified |

## Criterion 12: license and maturity

Licenses from `importlib.metadata` on the installed packages. Releases in the last year: versions on PyPI newer than the version `uv pip compile --exclude-newer 2025-10-09T00:00:00Z` picked, from the `pip index versions` list (final releases only; the cutoff version may be held back by Python limits, so the count is approximate). Breaking changes in the last year are not measured.

| Engine | Package | License | Releases in the last year | Latest |
| ------ | ------- | ------- | ------------------------: | ------ |
| echo-python | `httpx`, `mcp` | `mcp` 2.2.0: MIT; `httpx` not checked | not measured: PyPI release dates are not in `pip index versions`; `httpx` and `mcp` are libraries, not an agent framework | n/a |
| echo-pydanticai | `pydantic-ai-slim` | MIT | 192 (1.0.16 then, 2.52.0 installed, 2.55.0 latest) | 2.55.0 |
| echo-langgraph | `langgraph`, `langchain-openai` | MIT, MIT | 37 (0.6.8 then, 1.2.12 installed) | 1.2.14 |
| echo-openai-agents | `openai-agents` | MIT | 87 (0.3.3 then) | 0.23.1 |
| echo-typescript | `@a2a-js/sdk`, `@modelcontextprotocol/sdk` | Apache-2.0, MIT (package.json) | not measured: npm, `npm view` not run for release dates | 1.3.0, 1.32.1 |
| echo-smolagents | `smolagents` | Apache-2.0 (the metadata field is empty; the `LICENSE` file in the dist-info is Apache 2.0) | 4 (1.22.0 then) | 1.26.0 |
| echo-claude-agent | `claude-agent-sdk` | MIT (metadata). **The bundled `claude` CLI binary's own terms: not verified** | 109 (0.1.1 then) | 0.2.165 |
| kagent-adk | `kagent-adk` (kagent) | not verified (not installed here) | not measured: kind, from CI. The probe saw no release tags on `main` and an alpha API (`v1alpha3`) | 0.10.3 on PyPI (the probe ran 0.4.0 from the repo) |

Reading: `pydantic-ai-slim` (192 releases) and `claude-agent-sdk` (109) and `openai-agents` (87) move fast; a pinned lock file and the offline suites are the guard. smolagents ships about once a quarter.

## Router compatibility

The chassis model proxy keeps `model`, `messages`, `temperature`, `max_tokens`, `tools`, and `stream`, and ignores the other keys of `/v1/chat/completions`. "Forwarded" is what the fake model received through the chassis (from `bakeoff`); `temperature`, `max_tokens`, and `metadata` there are added by the chassis. "Sent" is what the workload put on the wire, measured by running its `handle` here against a recording fake (Python engines), or taken from source (TypeScript, Claude).

*Table caption: offline, fake model, localhost processes; not a hosted-model or SLM number.*

| Engine | Keys the chassis forwarded to the model (bakeoff, first call) | Keys the workload sent | Dropped by the proxy | Provider-only features needed |
| --- | --- | --- | --- | --- |
| echo-python | max_tokens,messages,metadata,model,stream,stream_options,temperature,tools | messages, model, stream, stream_options, tools (measured in-process, lookup task, 3 calls) | stream_options | none |
| echo-pydanticai | max_tokens,messages,metadata,model,stream,stream_options,temperature,tools | messages, model, stream, stream_options, tool_choice, tools (measured in-process, lookup task, 3 calls) | stream_options, tool_choice | none. `tool_choice` is ignored; a model that must be forced to call a tool is not forced |
| echo-langgraph | max_tokens,messages,metadata,model,stream,stream_options,temperature,tools | messages, model, stream, stream_options, tools (measured in-process, lookup task, 3 calls) | stream_options | none |
| echo-openai-agents | max_tokens,messages,metadata,model,stream,stream_options,temperature,tools | messages, model, stream, tools (measured in-process, lookup task, 3 calls) | none | none for the defaults. Setting `tool_choice`, `parallel_tool_calls`, `top_p`, `reasoning_effort` would be dropped silently |
| echo-typescript | max_tokens,messages,metadata,model,stream,stream_options,temperature,tools | model, messages, stream, stream_options, tools (source: `src/handle.ts` line 198; tools when MCP lists them) | stream_options (the proxy puts usage on the last chunk itself) | none |
| echo-smolagents | max_tokens,messages,metadata,model,temperature | messages, model, stop (measured in-process, lookup task, 3 calls) | stop | none. `stop` is dropped; the workload cuts the reply at the stop strings itself. Cost: tokens past `</code>` are billed and discarded |
| echo-claude-agent | max_tokens,messages,metadata,model,stream,stream_options,temperature,tools | Messages body: model, messages, system, tools, max_tokens, stream, plus thinking, output_config, context_management, cache_control, safeguards, metadata (capture note, CLI 2.1.294; not re-captured here) | thinking, output_config, context_management, cache_control, safeguards, metadata (dropped and counted by the `/v1/messages` route) | none required; the route needs the Messages mapping (contract v5 A). Prompt caching and thinking are lost |
| kagent-adk | not run (SKIP) | not measured: kind, from CI (probe: OpenAI-compatible `base_url`, `apiKeyPassthrough`) | not measured: kind, from CI | not measured: kind, from CI |

Reading: nothing an engine needs is lost, with two things to watch. smolagents depends on its own cut at the stop strings because `stop` is dropped. pydantic-ai sends `tool_choice`, which is ignored. No engine needs a provider-only feature in this setup.

## Controls

- A pass means the check on the output passed, for both the complete and the streaming run. The checks are in `packages/bakeoff/src/bakeoff/tasks.py`. I ran no failing control for the checks here: whether a wrong answer fails is not shown by this note.
- Record test mutations, each confirmed to fail and then restored: the CI run number changed (`test_image_sizes_cite_the_ci_run` failed); one cell of criterion 11 emptied (the durability case failed); one `passed` in `results.json` set to 9 (`test_results_json_has_every_engine_lane_and_task` failed).
- kagent-adk's SKIP is not a pass: its row has no numbers anywhere.
- RSS idle < warm for every engine: the sampling saw growth, so the reading works. For Claude the tree jumps by about 230 MB, which matches the CLI's size in the capture note.

## Reading the scorecard

Input for ADR-006, not the decision. All numbers offline, fake model.

- **Default candidate for the trusted lanes: the Chat Completions framework engines, and among them `echo-openai-agents` and `echo-pydanticai`.** All five trusted engines pass 30 of 30 in `sidecar`, send the same prompt bytes as plain Python (within 9 bytes), stream 13 deltas with `tool_call` events, and need no provider-only feature. `echo-pydanticai` has the smallest mapping (181 lines, 26% under plain Python) and an OpenInference instrumentor, native OpenTelemetry, and a Temporal extra that resolves. `echo-openai-agents` has the smallest image (209 MB against 252 to 256 MB) and a Temporal extra that resolves, and it sends no key the proxy drops, at 295 mapping lines. Both are MIT and release often (192 and 87 releases in a year), which costs lock-file churn.
- **echo-langgraph** is close on every offline number (224 lines, same prompt bytes) but has no instrumentor under its own name and an unverified Temporal path, and it needed a stand-in MCP client.
- **echo-python (baseline)** has the least weight (181 MB, 93 MB RSS, 1.4 s start) and no framework risk, and it is the slowest of the sidecar engines in this run (lookup p50 131 to 151 ms across two runs, against 76 to 138 for the others). Its 243 lines are all ours to maintain.
- **echo-typescript** is the fastest (lookup p50 76 ms, 0.4 s cold start, 115 MB RSS) and proves the contract is language-neutral, but has the largest code (551 lines, with its own A2A server) and an image the size of the framework ones (252 MB). The case for it is a TypeScript team, not a default.
- **The untrusted engines pay for what they add.** smolagents sends 20 times the prompt bytes (about 9 KB per call), streams one delta, and runs model code in-process: the lane is `remote` and the cost is tokens. Claude's agent costs about 0.5 s more per run, 4.4 times the prompt bytes, 434 MB of image, and about 320 MB of RSS, in return for the strongest tool set and partial-message streaming. Both need real-token numbers from the Mac run before they are compared on cost.
- **kagent-adk** has no number in this note. The probe says the wire works, tools are invisible, state is in PostgreSQL, and the full stack does not fit our kind budget as it is. That makes it the weakest on statelessness, tool visibility, and footprint; it stays the open "remote solution" question.

Open until the Mac and CI runs: real tokens, real latency, SLM behavior (smolagents code format, Claude's tool prompt on a small context), kind memory, and the kagent-adk cells.

## Caveats

- Fake model, localhost, one VM, one run per section. p95 of 10 is near the maximum; use p50.
- `bakeoff` lists the wrong mapping files for echo-claude-agent (criterion 1). `results.md` carries the 168 figure; the corrected 310 is in the table above.
- Release counts are approximate (see criterion 12). Instrumentors were looked up, not run.
- Image sizes are the CI run's, not this VM's.
