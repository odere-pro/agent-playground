# PoC-2 measurements (2026-10-01)

Exit criterion: event mapping size (lines of code), token overhead against plain Python, the local hop's extra latency (p50 and p95), the overhead per streamed delta, and the time to first token are recorded.

Produced by, from the repo root:

```bash
uv run python pocs/poc-02-two-engines-one-contract/demo/measure.py --runs 200 --warmup 10 --out notes/2026-10-01-measurements.md
```

200 measured runs per series after 10 warm-up runs; the whole script took 36 s.

## Environment

- Python 3.12.13 (CPython)
- Machine: macOS-26.6.2-arm64-arm-64bit, arm64, 14 CPUs
- Packages: a2a-sdk 1.2.0, pydantic-ai-slim 2.52.0, langgraph 1.2.12, langchain-openai 1.6.7, fastmcp 4.0.10, mcp 2.2.0, openai 3.22.1, httpx 0.28.1, uvicorn 0.54.0
- Load average (1, 5, 15 min) at start: 26.2, 22.1, 17.2; at end: 21.1, 21.4, 17.2

## Caveats

Every number below is from a fake model, Unix socket, one laptop. Read them as orders of magnitude, not as a budget.

- Docker was not running and the offline gate blocks TCP, so nothing here goes over TCP or between containers. The `sidecar` lane is the real `SidecarConnector` and the real `workload_a2a` server, over a Unix socket, with uvicorn in the same process and the same event loop as the client. A real sidecar is another process in another container: it adds a process hop and loopback TCP, and it removes the contention of one event loop.
- The `inprocess` lane is the chassis's in-memory A2A (`httpx.ASGITransport`). That transport runs the whole app call before it returns the body, so in this lane every event, the first `delta` too, arrives at the end of the run.
- The model is the fake model server, reached through each workload's test transport hook (`httpx.ASGITransport` or `httpx2.ASGITransport`), with no chassis model proxy in the path. It answers at once, so a time here is the stack's own overhead, with no model time in it. The tool endpoint is a FastMCP stub of the chassis's `glossary_lookup`, over the same kind of hook; every engine lists the tools on every run.
- Token counts are scripted by the fake server, not real. The token overhead is a byte-count proxy; see that section.
- The laptop was not idle: other work shared the CPUs (load average above). The same handle can differ between two sections of one run; compare rows within a table, not across tables.
- p50 and p95 are over the measured runs of one series. A difference of two p95s is not the p95 of the difference.

## Event mapping size

Lines per engine: the code that turns the framework's output into chassis events, and its tool plumbing. `code` is total minus blank lines, comment lines, and docstring lines (Python) or `//` and `/* */` lines (TypeScript).

| Engine | Files | Total lines | Code lines |
| ------ | ----- | ----------: | ---------: |
| plain Python (echo-python) | `handle.py`, `tools.py` | 322 | 237 |
| PydanticAI (echo-pydanticai) | `mapping.py`, `handle.py` | 251 | 173 |
| LangGraph (echo-langgraph) | `mapping.py`, `handle.py`, `tools.py` | 330 | 221 |
| TypeScript (echo-typescript) | `a2a_server.ts`, `handle.ts`, `schema.ts` | 387 | 316 |
| shared runtime (chassis mapping + workload_a2a server) | `mapping.py`, `server.py` | 663 | 483 |

| File | Total | Blank | Comment or docstring | Code |
| ---- | ----: | ----: | -------------------: | ---: |
| `packages/workloads/echo-python/src/echo_python/handle.py` | 186 | 26 | 16 | 144 |
| `packages/workloads/echo-python/src/echo_python/tools.py` | 136 | 28 | 15 | 93 |
| `packages/workloads/echo-pydanticai/src/echo_pydanticai/mapping.py` | 133 | 20 | 18 | 95 |
| `packages/workloads/echo-pydanticai/src/echo_pydanticai/handle.py` | 118 | 18 | 22 | 78 |
| `packages/workloads/echo-langgraph/src/echo_langgraph/mapping.py` | 126 | 21 | 15 | 90 |
| `packages/workloads/echo-langgraph/src/echo_langgraph/handle.py` | 118 | 19 | 23 | 76 |
| `packages/workloads/echo-langgraph/src/echo_langgraph/tools.py` | 86 | 15 | 16 | 55 |
| `packages/workloads/echo-typescript/src/a2a_server.ts` | 208 | 20 | 22 | 166 |
| `packages/workloads/echo-typescript/src/handle.ts` | 125 | 7 | 12 | 106 |
| `packages/workloads/echo-typescript/src/schema.ts` | 54 | 5 | 5 | 44 |
| `packages/chassis/src/chassis/adapters/a2a/mapping.py` | 229 | 32 | 49 | 148 |
| `packages/workload-a2a/src/workload_a2a/server.py` | 434 | 50 | 49 | 335 |

What it means: each engine's own mapping is 173 to 316 code lines, in the workload; the shared runtime (483 code lines) is written once and every Python workload reuses it. `packages/workload-a2a/src/workload_a2a/mapping.py` is a copy of the chassis mapping (a test diffs them) and is not counted again. Plain Python's count includes its hand-written tool loop (`tools.py`), which the frameworks do for it; LangGraph's `tools.py` is a stand-in for `langchain-mcp-adapters` and goes away when that works with `mcp` 2.

## Token overhead against plain Python

The same request (`glossary: what is SLM?`), which calls `glossary_lookup` once, through each engine's `handle`. What each engine sent to the model, from the fake server's record of every request body (`app.state.calls`), re-serialized as compact JSON with one serializer so formatting does not count. `sent` is `messages` plus `tools` bytes over all calls.

| Engine | Model calls | Messages (per call) | `messages` bytes (per call) | `tools` bytes (per call) | Sent bytes | Against plain Python | Scripted usage in/out | MCP HTTP requests |
| ------ | ----------: | ------------------- | --------------------------- | ------------------------ | ---------: | -------------------: | --------------------- | ----------------: |
| plain Python (echo-python) | 2 | 2, 4 | 140, 428 | 220, 220 | 1008 | baseline | 20 / 10 | 7 |
| PydanticAI (echo-pydanticai) | 2 | 2, 4 | 140, 425 | 234, 234 | 1033 | +25 B (+2%) | 20 / 10 | 3 |
| LangGraph (echo-langgraph) | 2 | 2, 4 | 140, 425 | 220, 220 | 1005 | -3 B (-0%) | 20 / 10 | 7 |

Request body keys each engine sent (first call):

- plain Python (echo-python): `messages`, `model`, `stream`, `stream_options`, `tools`
- PydanticAI (echo-pydanticai): `messages`, `model`, `stream`, `stream_options`, `tool_choice`, `tools`
- LangGraph (echo-langgraph): `messages`, `model`, `stream`, `stream_options`, `tools`

What it means: for the same work the frameworks sent PydanticAI +25, LangGraph -3 bytes against plain Python's `messages` and `tools`, from the same number of model calls and messages. suggested: bytes are the offline proxy for tokens (roughly 4 bytes per token for English and JSON). The scripted usage is the same for every engine by construction, so it says nothing about overhead. Real token counts need the `local` Compose variant with a tokenizer-backed router (LiteLLM logs them per call); that run is not done here.

## The local hop's extra latency

`echo_python:handle` on `simplify: the quick brown fox`, wall time from `run()` (first event requested) to `end`. The same request each run through three paths, interleaved: the workload's `handle` called directly (no lane), the `inprocess` lane, and the `sidecar` lane over a Unix socket. The model is the fake server behind the workload's transport hook in every path, so the paths differ only by the lane. Wall time is the latency; CPU time is the process's CPU time over the same span, which a busy machine does not inflate (client and server share the process).

| Path | Wall p50 ms | Wall p95 ms | CPU p50 ms | CPU p95 ms |
| ---- | ----------: | ----------: | ---------: | ---------: |
| direct handle | 6.65 | 35.09 | 5.79 | 11.07 |
| inprocess | 11.20 | 42.45 | 9.66 | 17.91 |
| sidecar | 13.17 | 55.84 | 11.77 | 21.28 |
| extra: `sidecar` minus `inprocess` | 1.96 | 13.39 | 2.11 | 3.37 |
| extra: `sidecar` minus direct `handle` | 6.52 | 20.75 | 5.98 | 10.21 |

What it means: going over the socket instead of in memory costs 1.96 ms at p50 and 13.39 ms at p95 here (2.11 ms of CPU at p50); the whole A2A path over the socket costs 6.52 ms at p50 over calling `handle` directly. The socket hop is HTTP over a Unix socket, server-sent events, and a second uvicorn task. Loopback TCP between two containers will add to it.

## Overhead per streamed delta

A handle that yields `start`, N one-character `delta`s, and `end`, with no model call, N = 1, 10, 100. Wall time from `run()` to `end` per N, then the per-delta overhead (time for N minus time for 1) / (N - 1), taken per run (the N values run back to back in each run) and then p50 and p95 over runs.

| Path | N=1 p50 ms | N=1 p95 ms | N=10 p50 ms | N=10 p95 ms | N=100 p50 ms | N=100 p95 ms | Per delta p50 µs | Per delta p95 µs | Per delta CPU p50 µs | Per delta CPU p95 µs |
| ---- | -----: | -----: | -----: | -----: | -----: | -----: | -----: | -----: | -----: | -----: |
| direct handle | 0.01 | 0.01 | 0.01 | 0.01 | 0.02 | 0.03 | 0.1 | 0.2 | 0.1 | 0.2 |
| inprocess | 3.04 | 4.23 | 5.28 | 7.69 | 25.00 | 36.94 | 221.6 | 335.1 | 187.7 | 250.8 |
| sidecar | 3.09 | 4.53 | 6.12 | 8.35 | 34.26 | 48.16 | 315.6 | 445.1 | 291.4 | 397.4 |

The per-delta columns use N = 100 against N = 1. What it means: each `delta` is one A2A `TaskArtifactUpdateEvent` with the chassis event in its metadata, validated on the server and parsed on the client; this is its cost in each lane. At the p50 cost, a 500-token answer streamed one token per `delta` adds about 111 ms `inprocess` and 158 ms `sidecar` of wall time (94 and 146 ms of CPU) (suggested: 500 tokens as a typical answer). A workload that batches a few tokens per `delta` divides that.

## Time to first token

`simplify: the quick brown fox`, time from the call to the first `delta`, and to the last event.

Over the lanes, `echo_python:handle` (the same runs as the latency section):

| Path | First delta p50 ms | First delta p95 ms | End p50 ms | End p95 ms |
| ---- | -----------------: | -----------------: | ---------: | ---------: |
| direct handle | 6.62 | 35.04 | 6.65 | 35.09 |
| inprocess | 10.99 | 42.23 | 11.20 | 42.45 |
| sidecar | 10.53 | 45.92 | 13.17 | 55.84 |

Per engine, in process, each engine's own `handle` called directly (no lane), so the framework's own overhead before the first delta is visible. Each run lists the MCP tools (the stub) and makes one model call.

| Engine | First delta p50 ms | First delta p95 ms | End p50 ms | End p95 ms |
| ------ | -----------------: | -----------------: | ---------: | ---------: |
| plain Python (echo-python) | 4.20 | 4.97 | 4.22 | 4.99 |
| PydanticAI (echo-pydanticai) | 5.54 | 6.55 | 7.87 | 9.33 |
| LangGraph (echo-langgraph) | 7.05 | 7.66 | 7.65 | 8.34 |

What it means: with a model that answers at once, the time to the first `delta` is all stack: the MCP tool listing, building the framework's client or graph, and the first chunk's mapping. Against plain Python at p50: PydanticAI 1.34 ms, LangGraph 2.85 ms. In the `inprocess` lane the first delta lands with `end` (see caveats); only the `sidecar` lane streams it early. A real model's first-token time adds to all of these.
