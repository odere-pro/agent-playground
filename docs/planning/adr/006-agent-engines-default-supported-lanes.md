# ADR-006: Agent engines: the default, the supported set, and their lanes

- **Date:** 2026-10-09
- **Deciders:** Oleksandr (epic owner) and the delivery team
- **Format:** Michael Nygard's template: Status, Context, Decision, Consequences
- **Related:** [ADR-001](001-chassis-delivery-model.md) (lanes, the trust rule), [ADR-002](002-template-a2a-server-placement.md), [ADR-005](005-remote-lane-auth-and-trust-admission.md), [PoC plan](../poc/000-plan.md#bake-off-criteria), [PoC-6](../poc/006-PoC-6-framework-bake-off.md), [PoC-6c](../poc/006c-PoC-6c-pretrained-slm.md), [contract v5](../../contracts/contract-v5.md), [008 H-14](../issues/008-H-14-one-agent-interface.md), [054 H-16](../issues/054-H-16-tool-port.md), [058 CH-8](../issues/058-CH-8-framework-event-mappings.md), [055 CH-6](../issues/055-CH-6-remote-lane-trust-rule.md), [013 CH-2](../issues/013-CH-2-outbound-model-proxy.md)

## Status

Proposed, 2026-10-09. The user accepts it at the PoC-6 pull request.

Two parts of the evidence are not in yet:

- **The hosted-model and SLM numbers.** Every number below is offline: the fake model server, processes on localhost, no key. Real tokens, real latency, and real time to first token come from the Mac run (`make poc06-mac`). The SLM numbers are PoC-6c.
- **The kind numbers for `kagent-adk`.** They come from the `poc06-kind.yml` CI run.

Both can reopen the default (see Revisit). PoC-6c lists this reopening as an exit criterion.

## Context

PoC-6 ran eight engines through the same three tasks (smoke, simplifier, lookup) behind the same chassis. The scorecard is `pocs/poc-06a-bake-off-sidecar-lane/notes/2026-10-09-scorecard.md`. It has one table per criterion of the [bake-off criteria](../poc/000-plan.md#bake-off-criteria).

What the offline run showed:

- **All sixteen cells that can run offline pass 30 of 30.** That is every engine in every lane it is allowed in. `kagent-adk` is SKIP offline, by design: it runs only on kind.
- **The five Chat Completions engines send the same prompt.** Plain Python, PydanticAI, LangGraph, OpenAI Agents SDK, and TypeScript are within 9 bytes of each other. The framework adds nothing to the messages.
- **The untrusted engines pay for what they add.** smolagents sends about 9 KB per call (20 times the baseline over a lookup) and streams one delta. The Claude Agent SDK sends 4.4 times the baseline bytes, adds about 0.5 s per run, and holds about 320 MB resident.
- **No engine needs a provider-only feature.** The chassis model proxy drops `stream_options`, `tool_choice`, and `stop`; nothing an engine needs is lost.
- **No engine needed an event change.** The six events carry every mapping (contract v5, part C).

Offline numbers for the three engines that compete for the default:

| | Plain Python | PydanticAI | OpenAI Agents SDK | LangGraph |
| - | ------------ | ---------- | ----------------- | --------- |
| Mapping code lines (criterion 1) | 243 | 181 (-26%) | 295 (+21%) | 224 (-8%) |
| Lookup p50 ms, sidecar (criterion 6) | 151 | 109 | 119 | 138 |
| Cold start s, median | 1.4 | 2.8 | 2.4 | 2.4 |
| Image MB, CI run 37997346774 (criterion 7) | 181 | 255 | 209 | 252 |
| RSS idle MB | 87 | 131 | 118 | 133 |
| Prompt bytes against plain Python, lookup | baseline | -1% | +0% | +0% |
| Keys the proxy dropped | `stream_options` | `stream_options`, `tool_choice` | none | `stream_options` |
| OpenInference instrumentor (criterion 10) | none | yes, plus native OpenTelemetry | yes | only under the LangChain name |
| Temporal extra resolves (criterion 11) | no framework | yes | yes | not verified |
| Releases in the last year (criterion 12) | n/a | 192 | 87 | 37 |
| Licence | MIT (`mcp`) | MIT | MIT | MIT |

Read the latency as an order of magnitude: p95 of 10 runs is near the maximum, and the fake model has no delay.

Options for the default engine:

| # | Option | In short |
| - | ------ | -------- |
| D1 | PydanticAI | Least mapping code, lowest latency of the Python engines, native OpenTelemetry. Largest image and slowest start. Sends `tool_choice`, which the proxy drops |
| D2 | OpenAI Agents SDK | Smaller image, nothing dropped by the proxy. 21% more mapping code than plain Python. A vendor SDK whose defaults point at the vendor: the Responses API and trace export to OpenAI, both switched off by the workload |
| D3 | LangGraph | Close on every number, but it needed a stand-in MCP client and has no instrumentor under its own name |
| D4 | Plain Python | Lightest and no framework risk. The slowest sidecar engine here, and all 243 lines are ours to maintain |
| D5 | TypeScript | Fastest and quickest to start, but the largest code (551 lines, with its own A2A server). The case for it is a team, not a default |

## Decision

1. **The default engine for new agents is PydanticAI** (suggested: `echo-pydanticai` is the template's engine). Reasons from the data: the smallest mapping to maintain (181 lines, 26% under plain Python); the lowest latency of the Python engines (lookup p50 109 ms); native OpenTelemetry and an OpenInference instrumentor; a Temporal extra that resolves; no vendor default to switch off. The margin over the OpenAI Agents SDK is small and offline. The default stands only if the hosted and SLM numbers agree (see Revisit).
2. **The supported engines, each with its lane:**

   | Engine | Lane | Note |
   | ------ | ---- | ---- |
   | Plain Python (baseline) | `sidecar` (also `inprocess`) | The reference. Every other engine is scored against it |
   | PydanticAI | `sidecar` (also `inprocess`) | The default |
   | LangGraph | `sidecar` (also `inprocess`) | Supported. Its MCP client is a stand-in until `langchain-mcp-adapters` imports against `mcp` 2 |
   | OpenAI Agents SDK | `sidecar` (also `inprocess`) | Supported. The workload uses the chat-completions model and turns the SDK's own tracing off |
   | TypeScript agent | `sidecar` | Proves the contract is language-neutral. It serves A2A itself and has no `inprocess` lane |
   | smolagents | `remote` | Untrusted: the model's Python runs in the workload process. Needs a model that follows the `<code>` format |
   | Claude Agent SDK | `remote` | Untrusted: the model can run `Bash` and write files. Reaches the chassis through `POST /v1/messages` on the model proxy |
   | kagent-adk (the remote solution) | `remote`, plain-A2A mode | The Python runtime of kagent, not kagent on Kubernetes. Supported once its kind run passes in CI |

   The trust rule picks the lane (ADR-001 item 5). `bakeoff` and `spec.trust` refuse an untrusted engine in any other lane.
3. **Not supported, with reasons:**

   | Engine | Why |
   | ------ | --- |
   | Google ADK as a `sidecar` workload | Not evaluated. Skipped by the user's decision on 2026-10-09: the planning doc allows it only "if the team has a reason". `kagent-adk` runs ADK underneath, as a `remote` |
   | Full kagent on Kubernetes | No-go, from the probe (`pocs/poc-06b-bake-off-remote-lane/notes/2026-10-09-kagent-probe.md`). A working install needs Substrate (alpha) and PostgreSQL, which do not fit the kind memory budget or the admission rules. Its inbound auth cannot check our token. Its runtime-identity header "can be forged", in kagent's own docs |
   | AWS Bedrock AgentCore, Vertex AI Agent Engine | Out of scope by the user's decision on 2026-10-09. [001 DEC-1](../issues/001-DEC-1-resolve-open-decisions.md) names no first cloud. No cloud runtime and no cloud auth adapter in PoC-6 |

4. **Plain-A2A mode is how a third-party agent is fronted.** `spec.engine.protocol: a2a` in the `remote` connector reads the agent's own A2A stream and builds chassis events from it. It is opt-in, restart-only, and refused outside `remote`. The chassis cannot see an agent's tool calls in this mode, and usage is reported by the agent or unknown. Contract v5, part B, is the text. It is not frozen until a second plain agent has passed.
5. **`POST /v1/messages` on the chassis model proxy** serves the Claude Agent SDK, on both proxy listeners, behind the same admission as the chat route. The remote holds no key (ADR-001 hard requirement 1). A refused model route answers 403 `model_route_denied`, not 500. Contract v5, part A.
6. **"Frozen as v1" means the first stable line, not the string `"1"`.** `schema_version` stays `"0"`, `events.v0.json` is unchanged, and no engine needed an event change. A test pins the surface (`packages/chassis/tests/test_contract_freeze.py`). The first breaking change bumps to `"1"` and keeps `"0"` accepted. Contract v5, part C.

## Consequences

**Easier:**

- PoC-9's template has one engine to start from and a list of the others that are known to work, each with its lane.
- A new framework has a yardstick: the criteria, the three tasks, and `python -m bakeoff smoke`.
- A third-party agent that emits no chassis events can sit behind the chassis.

**Harder:**

- Three of the four Python frameworks release often (192, 109, and 87 releases in a year for PydanticAI, the Claude Agent SDK, and the OpenAI Agents SDK). The guards are a pinned lock file and the offline suites.
- The default is chosen on offline numbers. If the hosted numbers disagree, the choice moves.
- `kagent-adk` hides its tool calls, keeps its own state, and checks no inbound token. The control is NetworkPolicy by label. The chassis count of model tokens at the proxy is the reliable number for it.
- The `remote` engines cost real tokens and time. smolagents and the Claude Agent SDK stay `remote`, so they pay the gVisor and network cost on top.

**Costs:**

- Each supported engine is an image to build, scan, and run in the CI matrix: the engine contract suite in its lane, the load test, and the PoC-5 hostile suites.
- The TypeScript agent carries its own A2A server (551 lines), which the Python engines get from `workload-a2a`.

## Revisit

Reopen the default, or an engine's place in the set, on any of these:

- **The Mac run (`make poc06-mac`).** If the real prompt tokens per call for PydanticAI exceed the OpenAI Agents SDK's by more than 10% (suggested), or if either fails a task on the hosted model that plain Python passes.
- **PoC-6c, the SLM.** If PydanticAI fails the lookup task on Qwen3-1.7B where another engine passes, because `tool_choice` is dropped by the llama.cpp path or the model needs a forced tool call, the default is reopened. The same run shows whether smolagents' `<code>` format and the Claude Agent SDK's 15k-token tool list work on a small context.
- **The `poc06-kind.yml` run.** If `kagent-adk` fails the lane contract's observable subset on kind, or does not fit the memory budget, drop it from the supported set. The remote solution slot is then open again, and the owner is asked before another is picked.
- **A second plain agent.** When one passes, freeze the plain-A2A mode.
- **A breaking change in a pinned framework**, or a licence change (the Claude CLI's own terms are not verified).
- **A cloud is chosen** in 001 DEC-1. A managed runtime then joins the `remote` list.
