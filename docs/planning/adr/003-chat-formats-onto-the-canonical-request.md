# ADR-003: Chat formats onto the canonical request

- **Date:** 2026-10-01
- **Deciders:** Oleksandr (epic owner) and the delivery team
- **Format:** Michael Nygard's template: Status, Context, Decision, Consequences
- **Related:** [ADR-001](001-chassis-delivery-model.md), [PoC plan](../poc/000-plan.md#inbound-adapters-agnostic-to-inputs), [PoC-3](../poc/003-PoC-3-one-interface-every-client.md), [011 H-2](../issues/011-H-2-inbound-adapters.md), [051 H-13](../issues/051-H-13-openapi-mcp-tools.md), [contract v1](../../contracts/contract-v1.md), [contract v2](../../contracts/contract-v2.md), the PoC-3 notes `pocs/poc-03-one-interface-every-client/notes/2026-10-01-open.md` (the design) and `2026-10-01-format-gaps.md` (every gap, row by row)

## Status

Proposed, 2026-10-01. Built in PoC-3; waiting for the epic owner's acceptance.

## Context

PoC-3 puts four public interfaces in front of one pipeline: native `POST /v1/run`, the OpenAI interface (`POST /v1/chat/completions` on the public port), the Anthropic interface (`POST /v1/messages`), and MCP (`/v1/mcp`). Each maps to the one canonical `Request` and its answer maps back. Every workload receives what these mappings produce, in every lane, so the mapping is part of the contract, not an adapter detail.

Facts that shape the choice:

- **The canonical input is small.** A transformer agent takes `TaskInput {text, data}`. The chat formats carry much more: system prompts, multi-turn history, client tools, several answers (`n`), logprobs, media, structured output formats, sampling settings, and personal-data fields.
- **The workload must not learn which interface was used** (011 H-2). So every format must reach the same `Request` for the same logical request: `(text, system, history, max_tokens, stream)`.
- **`model` means something else in a chat format.** In OpenAI and Anthropic it names a model. Behind the chassis, the model is the agent's config (`spec.model.route`); the caller is calling an agent.
- **Contract v1 has a stopgap.** `/v1/run` refuses a second in-flight run on one trace id with 409, because the proxies key each call to its run by trace id. The SDKs retry 409 by default, and SDK clients never chose a trace id: it comes from a `traceparent` header or is minted.
- **The SDKs read errors their own way.** Both retry 408, 409, 429, and every 5xx unless the `x-should-retry` header says otherwise, and both raise on a streamed error frame.
- **An MCP tool generated from an OpenAPI operation answers once.** FastMCP's OpenAPI tool buffers the HTTP answer; it cannot pass server-sent events through.
- **FastAPI validates against the SDK's own request types.** Keys the type does not list are dropped silently, and anthropic 1.11's `MessageCreateParams` lacks `temperature`, `top_p`, `top_k`, and `mcp_servers`.

Options considered:

| # | Question | Option | In short |
| - | -------- | ------ | -------- |
| A1 | What the chassis does with a chat field | Carry, refuse, or ignore by one rule | Carry what all formats share; refuse what the client would read and not find; ignore what only tunes the answer |
| A2 | | Pass the whole chat body through in `input.data` | The workload sees every field; the workload then knows the interface |
| A3 | | Refuse every field the canonical input has no slot for | Strict, but the stock SDKs send harmless defaults and would fail |
| B1 | `model` | Must equal the agent name; 404 otherwise | A client that thinks it calls GPT-4o learns at once that it does not |
| B2 | | Accept any `model` and ignore it | Any client config works unchanged, even one pointed at a model name |
| C1 | Trace id in use, non-native interfaces | Re-mint a fresh id once and count it | No SDK client ever sees a 409 it would retry |
| C2 | | Keep contract v1's 409 everywhere | One rule, but SDK clients retry a conflict they did not cause |
| D1 | MCP | Complete only, generated from the OpenAPI spec | One tool, no hand-written definition; no streaming |
| D2 | | A hand-written streaming MCP tool | Streams, but the tool and the REST API can drift (051 H-13 forbids it) |

## Decision

**We choose A1, B1, C1, and D1.**

1. **Carry, refuse, ignore.** Every chat field falls under one rule:
   - **Carry** the last user message as `input.text`, the system prompt, earlier text turns, the token budget, and `stream`. Text parts are joined with `"\n"`, the model proxy's rule.
   - **Refuse with 400** in the format's own error shape what the client would read and not find: client tools (`tools`, `functions`, a `tool_choice` or `function_call` other than none or auto, `mcp_servers`), `n > 1`, logprobs, any part that is not text (images, audio, files, documents, tool and thinking blocks), assistant prefill, tool turns in the history, a structured output format, `audio` output, `web_search_options`, `moderation`, and `container`.
   - **Ignore and count** what only tunes the answer: sampling and stop settings, `service_tier`, `reasoning_effort`, `thinking`, cache settings, and newer tuning fields. Each is counted as `chassis.inbound_ignored{interface, param}` (suggested: the name). The Anthropic router merges top-level keys the SDK type lacks back from the raw JSON, so they are refused or counted, not lost.
   - **Drop, never count or log,** fields that may identify a person: `metadata`, `user`, `safety_identifier`, `user_profile_id`. Caller identity is PoC-8's.
2. **`input.data.system` and `input.data.history`** (suggested: the key names). The system prompt is OpenAI's `system` and `developer` messages, or Anthropic's `system` and then any `role: system` messages, joined with `"\n"`. The history is the earlier turns as `[{"role": "user" | "assistant", "text": str}]`. Each key is set only when not empty, so a one-turn call gives `data == {}`, exactly what `/v1/run` sent before. The agent's own prompt (`spec.prompt`) still rules; the workload may ignore both keys.
3. **`model` is the agent's name.** Any other value is 404 (`model_not_found` in OpenAI's shape, `not_found_error` in Anthropic's), and the engine never runs. `model` is not copied into the request. The model route is reported only in the native `versions.model_route`. "Only a base URL change" means no custom client: the caller sets the base URL and `model` to the agent's name.
4. **One id rule for all four interfaces.** `trace_id` is the native body's, else a valid inbound `traceparent`'s, else minted. `request_id` is the native body's, else minted; never a header. `idempotency_key` is the `Idempotency-Key` header (suggested), else the native body's, else minted; it is carried only, PoC-4 acts on it.
5. **Re-mint instead of 409 on every interface but one.** When an in-flight run holds the trace id, the chassis replaces it once with a fresh id, counts `chassis.trace_id_reminted{interface}` (suggested), and opens the run. Only a native `/v1/run` whose body set `trace_id` keeps contract v1's 409. MCP calls `/v1/run`, but re-mints even a body `trace_id`, because a model chose it. The client reads the id it got from `x-trace-id` or the envelope.
6. **MCP is complete only, generated from the OpenAPI spec.** The agent is one MCP tool, named after the agent, at `/v1/mcp` on the public port (suggested: the path), stateless streamable HTTP. It is built by `FastMCP.from_openapi` over the spec of the `/v1/run` operation alone, produced by FastAPI's own generator; a test holds it equal to that operation in the full spec. The tool's description is the operation's generated description. Its calls carry `x-chassis-interface: mcp` (suggested), which makes `/v1/run` read `stream` as false, label telemetry `mcp`, and apply item 5. The header grants nothing.
7. **Errors are HTTP, with `x-should-retry`.** For OpenAI and Anthropic, an `error` event in complete mode, or before the first `delta` in a stream (the hold rule), is an HTTP error in the format's shape. The status comes from one table (suggested): 400 refused or invalid body, 404 `model_not_found`, 503 `not_ready`, 504 `a2a.timeout`, 500 `engine_error`, any other code 503 when retryable, else 502. Every such error carries `x-should-retry: true|false` from `retryable`, so the SDKs retry only what may succeed. An error after text is the format's mid-stream error frame. Native and MCP keep the 200 envelope with `status: error`.
8. **The answer maps back the same way everywhere.** Stop reasons are always `stop` and `end_turn`. The agent's own `tool_call` events are never sent as client tool calls. Usage is the whole run's. An `end.output` without text is sent as compact JSON text. Streamed text stands; the final answer adds only what extends it.

Why not the others:

- **A2** leaks the interface into the workload and makes each workload parse three chat formats. **A3** fails the stock SDKs, which send defaults such as `temperature` and `stream_options`.
- **B2** (accept any `model`) was the looser option. It lets a client configured for a real model name work unchanged. It was not chosen because the caller would believe it reached the model it named: a request for GPT-4o would be answered by a small agent, with no error and no sign. Usage, cost, and quality would all be read against the wrong model. A 404 costs one config line and removes the doubt. It also keeps `model` free to mean something later (for example an agent version) without breaking callers that sent junk.
- **C2** makes the SDKs retry a conflict the caller cannot fix, since it never chose the id. The 409 stays where the caller did choose it.
- **D2** is a hand-written tool, which 051 H-13 and the PoC-3 exit criterion forbid.

## Consequences

Pros:

- The workload gets the same `Request` from every interface and cannot tell them apart. One cassette per engine holds exactly one model call for every interface, mode, and lane, which proves it.
- Stock OpenAI and Anthropic SDKs work with a base URL change, and their retries follow the chassis's `retryable`.
- No SDK client sees a 409. Contract v1's stopgap narrows to the one caller that chose its trace id.
- MCP and REST cannot drift: the tool is generated from the same operation.

Cons:

- Chat clients lose features: client tools, several answers, media, logprobs, structured output formats, and real stop reasons. Each gap is listed with its handling in the PoC-3 gaps note.
- `max_tokens` changes meaning: it is the run's whole budget, input included, not one call's output limit.
- A run's `retry` or `fallback` status is not reported in a stream, because the headers go out first.
- An MCP client sees a failed run as a normal tool result (`status: error` in the envelope, not `isError`).
- A re-minted trace id breaks the link to the client's own trace for that one run.
- The rule needs upkeep: each SDK release adds fields, and each must be sorted into carry, refuse, ignore, or drop.

Costs across lanes: none. The interfaces sit before the connector, so the event schema (`"0"`), `handle`, the ports, and the A2A mapping are unchanged. `inprocess` streams arrive in one batch, `sidecar` per delta, and `remote` adds the network hop, as before. MCP adds one in-process ASGI hop per call, in every lane.

## Revisit

- If a real client needs client tools, several answers, or media, reopen item 1 for that field, with a canonical slot for it (a `TaskInput` change bumps the event schema rules in contract v1).
- If more than one caller in the agent MVP fails on the `model` 404 with a config it cannot change, reopen item 3 (option B2 behind a `spec.interfaces` flag).
- If `chassis.trace_id_reminted` is more than a small share of runs (suggested: 1%), clients are reusing trace ids, and the link loss in item 5 matters; reopen it with PoC-5's per-run credential, which removes the need for a unique trace id.
- If a FastMCP release can stream an OpenAPI tool's answer, reopen item 6.
