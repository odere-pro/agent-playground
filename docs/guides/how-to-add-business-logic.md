# How to add business logic

Status: written 2026-10-29 for PoC-9. The code and the contract win where this guide and they differ. Names from the PoC-9 template that were still being built when this was written carry the comment `verify after merge`.
Contract: [contract v7](../contracts/contract-v7.md), whose frozen surface is [contract v5, part C](../contracts/contract-v5.md). Decisions: [ADR-001](../planning/adr/001-chassis-delivery-model.md), [ADR-002](../planning/adr/002-template-a2a-server-placement.md), [ADR-005](../planning/adr/005-remote-lane-auth-and-trust-admission.md), [ADR-006](../planning/adr/006-agent-engines-default-supported-lanes.md), [ADR-007](../planning/adr/007-observability-otel-collector-langfuse.md), [ADR-008](../planning/adr/008-security-identity-guardrails-defense-in-depth.md), [ADR-009](../planning/adr/009-feedback-langfuse-scores-event.md), [ADR-010](../planning/adr/010-evals-offline-tool-online-gate.md). Plan: [PoC-9](../planning/poc/009-PoC-9-agent-mvp-template.md).

This guide is for someone who has business logic and wants it to run as an agent. You write one function. The chassis does the rest: the public interfaces, auth, guardrails, budgets, traces, metrics, and the evaluator gate.

## 1. Scaffold an agent

```bash
agentctl new my-agent --kind transformer --framework pydanticai --connector sidecar
```

- `--kind` is `transformer` (text in, text out), `evaluator` (scores or labels an input), or `tool` (answers by calling tools). These are the stateless kinds of the epic.
- `--framework` is `python`, `pydanticai`, `langgraph`, `openai-agents`, `smolagents`, or `claude-agent`. The default is `pydanticai` ([ADR-006](../planning/adr/006-agent-engines-default-supported-lanes.md), item 1).
- `--connector` is `sidecar` (the default) or `remote`. `smolagents` and `claude-agent` are allowed only with `remote` (section 5).
- `--dest DIR` and `--platform-path PATH` set where the repo goes and where this monorepo is. <!-- verify after merge -->

The command renders a Copier template into a new git repo. `copier update` pulls later template changes into it. suggested: Copier, from the PoC-9 plan.

What you get, and what you edit:

| Path in the new repo | What | You edit it |
| -------------------- | ---- | ----------- |
| `src/<package>/handle.py` | The business logic | Yes |
| `src/<package>/mapping.py` | Framework events to chassis events | Only if you change frameworks |
| `src/<package>/telemetry.py` | OpenTelemetry setup for the workload | Rarely |
| `config/agent.yaml` | The chassis config: lane, trust, adapters | Yes |
| `config/fake.yaml` | The chassis config for offline tests | Rarely |
| `tests/` | Tests on the `agent_testkit` kit | Yes |
| `deploy/compose/docker-compose.yaml` | Profiles `fake` and `local`, a debugger per container | Rarely |
| `Dockerfile`, `Makefile`, `.github/workflows/ci.yml`, `.gitlab-ci.yml` | Image and CI | No |

<!-- verify after merge: the paths above come from the agentctl spec, not from the merged template. -->

## 2. The `handle` contract

The source is [contract v5, part C.2](../contracts/contract-v5.md), item 1. It is frozen. A test pins it.

```python
async def handle(input: dict, ctx: dict) -> AsyncIterator[dict]:
    ...
```

- It is an async generator. Plain dicts in, event dicts out ([ADR-002](../planning/adr/002-template-a2a-server-placement.md), item 2).
- The workload never imports `chassis`. The template A2A server, [workload-a2a](../../packages/workload-a2a/README.md), wraps `handle` and serves it on localhost.
- A worked example is the default engine: [echo-pydanticai `handle.py`](../../packages/workloads/echo-pydanticai/src/echo_pydanticai/handle.py).

### 2.1 What comes in

`input` follows [`task_input.v0.json`](../../packages/chassis/schemas/task_input.v0.json):

- `text`: a string or null.
- `data`: any JSON value.

`ctx` follows [`context.v0.json`](../../packages/chassis/schemas/context.v0.json):

- `request_id`, `trace_id`, `idempotency_key`, `agent`, `agent_version`, `versions`.
- `budget`: `max_tokens` and `timeout_ms`.
- `model_route`: the route to name in every model call.
- `traceparent`: the W3C header to forward.

Rules for reading them:

- Ignore keys you do not know, in both `input` and `ctx`. New optional keys are added without a version bump ([contract v5, part C.3](../contracts/contract-v5.md)).
- Use `ctx["model_route"]` as the model name in every model call. The evaluator gate changes it on a fallback ([ADR-010](../planning/adr/010-evals-offline-tool-online-gate.md), item 12).
- Use `ctx["budget"]["timeout_ms"]` as the timeout of each model and tool call. The chassis owns the deadline of the whole run.
- Keep the input text in its own user message. Never merge it into the system prompt.

### 2.2 What goes out

`handle` yields events. Each event is one dict that matches [`events.v0.json`](../../packages/chassis/schemas/events.v0.json), with `schema_version: "0"`.

| Event | Fields | When |
| ----- | ------ | ---- |
| `start` | `request_id` | First, once |
| `delta` | `text` | Each chunk of the answer |
| `tool_call` | `call_id`, `name`, `arguments`, `result` (optional) | Each finished tool call |
| `metrics` | `input_tokens`, `output_tokens`, `attempt`, and optional `cost_usd`, `model_route`, `latency_ms` | Usage; the chassis sums them |
| `end` | `status` (`ok`, `retry`, `fallback`), `output` (optional) | Last, on success |
| `error` | `code`, `message`, `retryable` | Last, on failure |

The order rule is absolute: `start` first and once, then `end` or `error` last, and nothing after.

- Unknown fields are refused. The template server validates every event before it leaves the workload. A bad event becomes `workload.bad_event`, a bad order `workload.bad_order`.
- `end.output`, when set, replaces the joined deltas as the answer.
- Never raise for a model, tool, or transport failure. Yield one `error` instead. An exception that escapes becomes `workload.exception`.
- Your own error codes are a convention, not part of the contract. The echo engines use `http_<status>`, `timeout`, `connect_error`, `bad_response`, and `model_error`.

### 2.3 Streaming and the envelope

`handle` always streams. You do not write a second, complete form.

- A streaming caller gets each event as it comes, over A2A and then over the caller's own interface.
- A complete caller gets one `Response` envelope ([`response.v0.json`](../../packages/chassis/schemas/response.v0.json)). The chassis builds it from your events: the text, the tool calls, the summed metrics, and the status.
- The chassis serves the same run as `POST /v1/run`, the OpenAI and Anthropic interfaces, and MCP. Your code does not know which one was called.

## 3. The event mapping

A framework has its own event stream. The mapping turns it into the six chassis events. That is the only framework-specific code in a workload ([ADR-001](../planning/adr/001-chassis-delivery-model.md), item 2).

- It lives in its own file, `mapping.py`, so its size can be counted and reviewed.
- One mapper instance per run. It may hold state, for example a tool call held until its result arrives.
- The template ships one mapping per supported framework (backlog 058 CH-8). Example: [echo-pydanticai `mapping.py`](../../packages/workloads/echo-pydanticai/src/echo_pydanticai/mapping.py).
- A mapping adds nothing for auth, guardrails, marking, feedback, or the evaluator gate. The chassis does those ([ADR-008](../planning/adr/008-security-identity-guardrails-defense-in-depth.md), item 10; [ADR-009](../planning/adr/009-feedback-langfuse-scores-event.md), item 9).
- A tool error becomes a `tool_call` result (`{"error": ...}`) or an `error` event, never an unhandled exception ([ADR-008](../planning/adr/008-security-identity-guardrails-defense-in-depth.md), item 10).

Most business logic changes only `handle.py`: the prompt, the input shaping, the tools offered. Keep `mapping.py` as the template gives it.

How the six engines map their runs, with sizes: [the PoC-6 guide](poc-06-how-it-works.md).

## 4. Models and tools: through the chassis only

The workload holds no key. Only the chassis holds credentials ([ADR-001](../planning/adr/001-chassis-delivery-model.md), hard requirement 1).

- **Models.** Point an OpenAI-compatible client at `CHASSIS_MODEL_URL`, the chassis's model proxy (suggested: `http://127.0.0.1:8090/v1`). The proxy adds the real, scoped LiteLLM key. Send no `Authorization` header in the `sidecar` lane. Turn the client's own retries off: the chassis owns retries and fallback.
- **The Claude Agent SDK** reaches the proxy through `POST /v1/messages` ([contract v5, part A](../contracts/contract-v5.md)).
- **Tools.** Connect an MCP client to `CHASSIS_TOOL_URL`, the chassis's tool proxy (suggested: `http://127.0.0.1:8090/mcp`). The model is offered what the chassis serves. Behind the proxy is the MCP gateway, with a tool allow-list per key ([ADR-001](../planning/adr/001-chassis-delivery-model.md), item 6).
- **Write tools** take the run's `idempotency_key`, because the gate may run `handle` twice for one request ([ADR-010](../planning/adr/010-evals-offline-tool-online-gate.md), item 12).
- **Code from a model** runs in the `code-runner` tool, never in the workload. It does not make the agent untrusted ([ADR-005](../planning/adr/005-remote-lane-auth-and-trust-admission.md), item 6).
- **Trace headers.** Forward `ctx["traceparent"]` on every model and MCP call. Forward `baggage` too ([ADR-007](../planning/adr/007-observability-otel-collector-langfuse.md), item 5). Without the header the proxy cannot tie the call to its run, and the call counts against a per-replica budget.
- **Spans.** `telemetry.py` runs the framework's OpenInference instrumentor or native OpenTelemetry and exports OTLP to the Collector ([ADR-007](../planning/adr/007-observability-otel-collector-langfuse.md), item 5).

To reach a new MCP server or service, add a connector to the chassis, not a client to the workload.

## 5. The lanes and the trust rule

The lane is named once, in `spec.engine.connector` ([ADR-001](../planning/adr/001-chassis-delivery-model.md), item 4).

| Lane | For | Shape |
| ---- | --- | ----- |
| `sidecar` (default) | Trusted code | Two containers in one pod. The workload serves A2A on localhost only |
| `remote` | Untrusted code, third-party images, managed runtimes | Its own pod, gVisor, egress denied by default. The chassis stays outside the sandbox |
| `inprocess` | The chassis's own tests only | The template server in the chassis process, over A2A in memory |

An agent repo uses `sidecar` or `remote`. Its own tests do not use `inprocess`: they run the chassis as a separate process, so the workload never installs the chassis package.

The trust rule ([ADR-001](../planning/adr/001-chassis-delivery-model.md), item 5). A workload is trusted only if both hold:

- **Source:** your team wrote and reviewed it, and the image is built in our registry.
- **Behavior:** it does not run code, shell commands, or file writes itself.

Everything else is untrusted and runs in `remote`. That is why smolagents and the Claude Agent SDK are `remote` only ([ADR-006](../planning/adr/006-agent-engines-default-supported-lanes.md), item 2).

The rule is checked twice ([ADR-005](../planning/adr/005-remote-lane-auth-and-trust-admission.md), item 4):

- At config load: `spec.trust: untrusted` needs `spec.engine.connector: remote`. The `cloud` profile must name `spec.trust`.
- At admission: a ValidatingAdmissionPolicy rejects an `untrusted` pod in the `sidecar` lane.

In the `remote` lane the workload holds one bearer token, `CHASSIS_API_TOKEN`. It sends it on every model and tool call and requires it on its own A2A server ([ADR-005](../planning/adr/005-remote-lane-auth-and-trust-admission.md), item 1). It is not a provider key. Rotation: [workload-a2a](../../packages/workload-a2a/README.md), "Remote lane".

## 6. Config

`config/agent.yaml` is a chassis config. The schema is [`chassis-config.v0.json`](../../packages/chassis/schemas/chassis-config.v0.json). A worked example is [`sidecar.yaml`](../../packages/chassis/configs/sidecar.yaml).

```yaml
profile: local
agent:
  name: my-agent
  version: 0.1.0
spec:
  trust: trusted              # trusted | untrusted; untrusted needs connector: remote
  engine:
    connector: sidecar        # sidecar | remote
    url: http://127.0.0.1:9000
  adapters:                   # merged over the profile defaults, field by field
    state: memory
  model:
    route: big-default
```

- `spec.engine.connector` picks the lane. `spec.engine.url` is where the workload serves A2A. In `sidecar` it must be loopback (ADR-001).
- `spec.trust` is `trusted` or `untrusted` (section 5).
- `spec.adapters` picks each port's adapter by name. Adapters are never picked in code. Only the fields you set change.
- The profile sets the defaults:

| Profile | For | Model | Tools | State | Telemetry |
| ------- | --- | ----- | ----- | ----- | --------- |
| `fake` | Offline tests | `fake` | `fake` | `memory` | `memory` |
| `local` | Compose on a laptop | `litellm` | `mcp` | `valkey` | `otel` |
| `cloud` | A cluster | `litellm` | `mcp` | `valkey` | `otel` |

The `cloud` profile also turns on `oidc` auth, `litellm` guardrails, and `langfuse` feedback. It refuses a `fake` or `memory` adapter (suggested). The full list per profile: [the chassis reference](chassis-reference.md).

Guardrails, rate limits, marking, and the evaluator gate are `spec.guardrails`, `spec.limits`, `spec.governance`, and `spec.harness`. They change chassis behavior and need no code in the workload ([ADR-008](../planning/adr/008-security-identity-guardrails-defense-in-depth.md), [ADR-010](../planning/adr/010-evals-offline-tool-online-gate.md)). The output URL allow-list is empty by default: an answer that links to an outside host loses the link unless `spec.guardrails.output_url_allow` lists it ([contract v7, part D.2](../contracts/contract-v7.md)).

## 7. Test offline

`make test` in the agent repo runs with no network and no keys. It passes on the first commit (PoC-9 exit criterion 1).

- The kit is `agent_testkit`. <!-- verify after merge -->
- It starts the chassis in the `fake` profile as a separate process (suggested: `uvx chassis serve --profile fake`) and drives the workload over A2A.
- The fixture `workload` starts your `handle` behind the template server. The fixture `chassis` gives `.run(input)`, which sends one run and returns the result. <!-- verify after merge -->
- The class `agent_testkit.contract.WorkloadContract` is the lane contract suite. Subclass it in `tests/` and it runs every case against your workload. <!-- verify after merge -->
- The fake model server scripts the model's answers, so a test is repeatable.

```python
async def test_keeps_the_numbers(chassis):  # verify after merge: fixture name
    result = await chassis.run({"text": "simplify: Costs fell 30 percent in 2026."})
    assert "30" in result.text and "2026" in result.text
```

The test layers and what each checks: [testing](testing.md).

## 8. Run with Compose and attach the debugger

```bash
docker compose -f deploy/compose/docker-compose.yaml --profile fake up    # no keys
docker compose -f deploy/compose/docker-compose.yaml --profile local up   # LiteLLM, MCP gateway, Collector
```

- Both profiles run two containers: the chassis and the workload. Only the chassis gets credentials.
- Each container runs a debugger. suggested: debugpy, the chassis on port 5678 and the workload on 5679. Attach your editor's Python remote debugger to the port. <!-- verify after merge -->
- Call the agent on the chassis port (suggested: 8080): `POST /v1/run`, the OpenAI SDK with `base_url` set to the chassis, or an MCP client on `/v1/mcp`.
- `GET /metrics` on the same port shows the agent's counters ([contract v7, part A](../contracts/contract-v7.md)).

## 9. Evals and the red-team set

**Offline evals** use DeepEval on the golden set ([ADR-010](../planning/adr/010-evals-offline-tool-online-gate.md), item 1).

- In the agent repo: `make eval`. <!-- verify after merge -->
- In this monorepo: `make evals` runs every engine and lane and checks the baseline. `make evals-regression` shows a logic change that drops facts and fails the gate.
- A change to the business logic, the prompt, or the model needs a passing eval.

**The online gate** runs in the chassis, in every lane, with no engine code. Turn it on with `spec.harness.evaluator_gate` ([ADR-010](../planning/adr/010-evals-offline-tool-online-gate.md), item 3).

**The red-team set** covers injection in the input, injection in a tool output, exfiltration through a tool, and a secret read ([ADR-008](../planning/adr/008-security-identity-guardrails-defense-in-depth.md), item 9).

- In the agent repo: `make redteam`, and `make hostile` for the hostile workload suite. <!-- verify after merge -->
- In this monorepo: `make redteam` runs it on every engine, lane, and guardrail config against the recorded baseline.

**Feedback** from users goes to `POST /v1/feedback` and onto the run's trace in Langfuse. The workload exposes nothing for it ([ADR-009](../planning/adr/009-feedback-langfuse-scores-event.md)).

## 10. What not to do

- Do not import `chassis` in the workload ([ADR-002](../planning/adr/002-template-a2a-server-placement.md)).
- Do not put a key, token, or secret in code, config, a prompt, or a log ([ADR-001](../planning/adr/001-chassis-delivery-model.md), hard requirement 1).
- Do not call a model or a tool except through the chassis proxies.
- Do not add a field or an event type. Every reader is strict, so it is a breaking change ([contract v5, part C.3](../contracts/contract-v5.md)).
- Do not run model-written code in the workload. Use `code-runner`, or move the agent to `remote`.
- Do not bind the workload to a non-loopback host in the `sidecar` lane.
