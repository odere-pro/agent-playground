# ADR-006: Agent engines: the default, the supported set, and their lanes

- **Date:** 2026-10-09
- **Deciders:** Oleksandr (epic owner) and the delivery team
- **Format:** Michael Nygard's template: Status, Context, Decision, Consequences
- **Related:** [ADR-001](001-chassis-delivery-model.md) (lanes, the trust rule), [ADR-002](002-template-a2a-server-placement.md), [ADR-005](005-remote-lane-auth-and-trust-admission.md), [PoC plan](../poc/000-plan.md#bake-off-criteria), [PoC-6](../poc/006-PoC-6-framework-bake-off.md), [PoC-6c](../poc/006c-PoC-6c-pretrained-slm.md), [contract v5](../../contracts/contract-v5.md), [008 H-14](../issues/008-H-14-one-agent-interface.md), [054 H-16](../issues/054-H-16-tool-port.md), [058 CH-8](../issues/058-CH-8-framework-event-mappings.md), [055 CH-6](../issues/055-CH-6-remote-lane-trust-rule.md), [013 CH-2](../issues/013-CH-2-outbound-model-proxy.md)

## Status

Accepted, 2026-10-30, by the user. Proposed 2026-10-09. Its open questions stay open; a change to one of them needs a new ADR or an edit here.

Two parts of the evidence are not in yet:

- **The hosted-model numbers.** The numbers in Context are offline: the fake model server, processes on localhost, no key. Real hosted tokens, latency, and time to first token come from the Mac run (`make poc06-mac`). The SLM numbers are in, from CI on CPU (PoC-6c, see [SLM (PoC-6c)](#slm-poc-6c)). They do not reopen the default. The side by side with the hosted model and the Mac latency are still open.
- **The `kagent-adk` scorecard cells.** `poc06-kind.yml` run 38010290978 passed its contract and hostile checks on kind. Its image, RSS, latency, and token cells in the scorecard are still unfilled. They are listed in [What is left](../poc/006-PoC-6-framework-bake-off.md#what-is-left).

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
   | kagent-adk (the remote solution) | `remote`, plain-A2A mode | The Python runtime of kagent, not kagent on Kubernetes. Supported: its kind run passed in CI (run 38010290978). Its memory on kind is not measured yet (see Revisit) |

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

## SLM (PoC-6c)

PoC-6c ran the five trusted engines on a pre-trained SLM. It is the second Revisit trigger below. The run is CI only: a 4-core GitHub runner, CPU, no GPU. It is GitHub Actions run 40724222330 (`poc06-slm.yml`, commit `bad1a45`). The notes are `pocs/poc-06c-pretrained-slm/notes/2026-10-28-slm-run.md` and `2026-10-28-scale-run.md`, each with its `.results.json`.

### Model and pins

| Item | Value |
| ---- | ----- |
| Model | Qwen3-1.7B, not fine-tuned, the epic's default student SLM ([I](../slm-agent-platform-epic-v3.md#app-i)) |
| File | `Qwen3-1.7B-Q8_0.gguf` from `Qwen/Qwen3-1.7B-GGUF`, revision `90862c4b9d2787eaed51d12237eafdfe7c5f6077` |
| File sha256 | `061b54daade076b5d3362dac252678d17da8c68f07560be70818cace6590cb1a` (`deploy/compose/poc06/model.sha256`) |
| Runtime | llama.cpp `llama-server`, release `b11963` (`version: 0.8.0-dev (build 11963, commit 39febd2ad)`), asset `llama-b11963-bin-ubuntu-x64.tar.gz`, sha256 `c117406e0eaaaeca0ac4a081b1e94152754fa79bcfc7f8af7016aace40defadf` |
| Server flags | `--jinja --chat-template-kwargs '{"enable_thinking":false}' -c 16384 -np 4 --alias local-small` (suggested: context and slots) |
| Route | LiteLLM `local-small`, `drop_params: true`. Only `spec.model.route` changed from `big-default` |
| Host | GitHub `ubuntu-latest`: AMD EPYC 7763, 4 CPUs, 15983 MiB for Docker, Ubuntu 24.04.5, CPU only, no GPU offload |
| Runs per cell | 5 (suggested: the script's `POC06_REPEAT` default), lanes `inprocess` and `sidecar` (TypeScript: `sidecar` only) |

### Result per engine

Sidecar lane. Pass is runs passed of runs made. Latency is per run, on CPU. Tokens are the mean per run of the lookup.

| Engine | Simplifier pass | Lookup pass | Tool-call success | Simplifier p50 / p95 ms | Lookup p50 / p95 ms | Lookup tokens in / out |
| ------ | --------------- | ----------- | ----------------- | ----------------------- | ------------------- | ---------------------- |
| Plain Python (baseline) | 5/5 | 5/5 | 5/5 | 1955.6 / 2527.5 | 9544.7 / 9854.5 | 675 / 89 |
| PydanticAI (default) | 5/5 | 5/5 | 5/5 | 1916.7 / 2484.1 | 9514.2 / 9813.1 | 669 / 89 |
| LangGraph | 5/5 | 5/5 | 5/5 | 1869.2 / 2366.9 | 9393.2 / 9594.8 | 675 / 89 |
| OpenAI Agents SDK | 5/5 | 5/5 | 5/5 | 1899.2 / 2402.0 | 9444.8 / 9760.2 | 675 / 89 |
| TypeScript | 5/5 | 5/5 | 5/5 | 1909.6 / 2506.1 | 9325.0 / 9608.8 | 669 / 89 |

The `inprocess` lane gives the same picture: every cell passes 5 of 5, smoke included. The smoke task passes 5 of 5 in the sidecar lane too.

Token overhead against plain Python on the SLM: none on the simplifier (296 in, 24 out for every engine). On the lookup, PydanticAI and TypeScript send 6 fewer input tokens (-0.9%). The other engines match plain Python.

Read the latency as CPU latency. It is not the Mac's, and not a GPU's. The engines are within about 5% of each other on both tasks, so the model dominates the time.

The checks use no judge model. The simplifier passes when it keeps the facts listed with its input (suggested: numbers and names, matched as strings). The lookup passes when it calls the right tool, with arguments valid against the tool's schema, and its answer holds the looked-up value.

Request features: the chassis proxy sends only six keys on (`model`, `messages`, `temperature`, `max_tokens`, `tools`, `stream`), so no engine feature reaches llama-server unchecked. It drops `stream_options` and PydanticAI's `tool_choice`. That `tool_choice` is `auto`, which is the server default. Details are in the slm-run note, "Request features". No request was rejected: every run passed.

### Fallback share

The share of failed runs is the share that would need the big-model fallback. The epic's target is a fallback rate under 10%: "Fallback rate | Share of requests sent to the big model | < 10%", with targets "to confirm in Phase 1" ([success metrics](../slm-agent-platform-epic-v3.md#success-metrics)).

| Engine | Failed runs, simplifier and lookup, both lanes | Fallback share | Under 10%? |
| ------ | ---------------------------------------------- | -------------- | ---------- |
| Plain Python | 0 of 20 | 0% | yes |
| PydanticAI | 0 of 20 | 0% | yes |
| LangGraph | 0 of 20 | 0% | yes |
| OpenAI Agents SDK | 0 of 20 | 0% | yes |
| TypeScript | 0 of 10 (sidecar only) | 0% | yes |

This is an upper bound on a base model. The epic's target is for the platform with routing and a fine-tuned SLM (063 S-8). The sample is small: 20 runs per engine (10 for TypeScript), so one failure is 5% (10% for TypeScript).

### The default

The rule, from Revisit: if PydanticAI fails the lookup task on Qwen3-1.7B where another engine passes, the default is reopened. suggested: an engine fails a task when fewer than 4 of its 5 runs pass.

Verdict: PydanticAI passes the lookup 5 of 5 in both lanes, as every engine does. The SLM does not reopen the default. The hosted-model trigger in Revisit is still open.

### Scale

N agent pairs share one llama-server with 4 slots (N = 1, 2, 4, suggested), engines `echo-python` and `echo-openai-agents`, 16 users, 60 s each.

| Engine | OK RPS, 1 pair | OK RPS, 2 pairs | OK RPS, 4 pairs | p50 ms |
| ------ | -------------- | --------------- | --------------- | ------ |
| `echo-python` | 1.84 | 2.34 | 2.03 | 6000 to 7900 |
| `echo-openai-agents` | 2.16 | 2.44 | 1.94 | 6400 to 8300 |

- **Throughput stops growing at 2 pairs**, at about 2.3 to 2.4 requests per second. It dips at 4 pairs.
- **The model server is the limit.** Each chassis uses 0.19 to 0.25 vCPU and each workload 0.09 to 0.17 vCPU, far under their 1-vCPU caps. llama-server has 4 slots, decodes on CPU, and shares the 4 CPUs with the stack.
- **`echo-python` at 1 pair had 13 failures in 124 requests.** The envelope status was `error`, and p95 was 30000 ms: the run budget timeout, while 16 users queue for 4 slots. There were no failures at 2 or 4 pairs.
- **The llama-server CPU line understates load.** It used `ps -o %cpu`, which on Linux is a lifetime average. `scripts/poc06_mac.sh` now samples per interval from `/proc` on Linux.

On a 4-core runner, llama-server and the agent pods share the same CPUs. So the limit seen here may be the runner, not the design.

### Still open

- **The big-model side by side.** The hosted numbers need the user's Mac and the provider key (`make poc06-mac`, the `hosted` step). Until then, there is no SLM-against-big-model comparison of success, tokens, or latency.
- **Mac Metal latency and time to first token.** The CPU numbers here are not what a Mac with Metal or a GPU gives. The `slm` step on the Mac fills them.
- **The untrusted engines on the SLM.** smolagents' `<code>` format and the Claude Agent SDK's 15k-token tool list on a small context run only on kind.
- **The default.** It stands only if the hosted numbers and the SLM numbers both agree (Decision 1). The SLM half agrees.

## Revisit

Reopen the default, or an engine's place in the set, on any of these:

- **The Mac run (`make poc06-mac`).** If the real prompt tokens per call for PydanticAI exceed the OpenAI Agents SDK's by more than 10% (suggested), or if either fails a task on the hosted model that plain Python passes.
- **PoC-6c, the SLM.** If PydanticAI fails the lookup task on Qwen3-1.7B where another engine passes, because `tool_choice` is dropped by the llama.cpp path or the model needs a forced tool call, the default is reopened. The same run shows whether smolagents' `<code>` format and the Claude Agent SDK's 15k-token tool list work on a small context. Checked 2026-10-28 in CI: not triggered (see [SLM (PoC-6c)](#slm-poc-6c)). The untrusted engines' part is still open.
- **The `kagent-adk` result on kind.** Run 38010290978 of `poc06-kind.yml` passed; memory is not measured yet. If `kagent-adk` fails the lane contract's observable subset on kind, or does not fit the memory budget, drop it from the supported set. The remote solution slot is then open again, and the owner is asked before another is picked.
- **A second plain agent.** When one passes, freeze the plain-A2A mode.
- **A breaking change in a pinned framework**, or a licence change (the Claude CLI's own terms are not verified).
- **A cloud is chosen** in 001 DEC-1. A managed runtime then joins the `remote` list.
