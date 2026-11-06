# The agent container: the plan

Status: in progress
Date: 2026-11-06

## What this is

The one plan to ship the agent container: what is decided, what is built in which order, and what is still open. It merges PoC-1 to PoC-9 into one product. What the container *is* lives in [the specs](../specs/README.md), one fact in one place. Why each rule exists lives in the ADRs. What each PoC proved, and what it left open, lives in the PoC READMEs and in [what is left](../planning/poc/011-what-is-left.md). This plan repeats none of that; it links it.

## Decisions

From the user, 2026-11-06:

- PoC-1 to PoC-5 are must-haves. The architecture stays as built: [architecture](../specs/architecture.md).
- The SLM track is parked (PoC-6c, 012 H-3, 127 L-3). The model stays swappable from a route map per task: [models](../specs/models.md).
- A cheap judge model is on by default, behind `EvaluatorPort`: [models](../specs/models.md), "The judge model".
- Security and observability are must-haves, and the container measures and shows them: [security](../specs/security.md), [observability](../specs/observability.md), [metrics](../specs/metrics.md).
- LangChain, LangGraph, and LangSmith come first. Amended the same day: [ADR-006](../planning/adr/006-agent-engines-default-supported-lanes.md) (LangGraph is the default engine), [ADR-007](../planning/adr/007-observability-otel-collector-langfuse.md) item 10 (LangSmith behind the Collector), [ADR-009](../planning/adr/009-feedback-langfuse-scores-event.md) item 3a (a `langsmith` feedback adapter), and a note in [ADR-010](../planning/adr/010-evals-offline-tool-online-gate.md) (LangSmith datasets as a candidate store).

Open, for the user:

1. **The north star.** "Good tasks per week", or "agents in production on the chassis" ([metrics](../specs/metrics.md)).
2. **The primary trace view per environment**, LangSmith or Langfuse, and where the score goes when both run (ADR-007 open question 4, ADR-009 open question 5).

## The order of work

suggested: the sizes. Phases A and B can run in parallel. C needs B's route map. D needs C only for the demo. E can start after A. F is last.

| Phase | What | Done when | Size |
| ----- | ---- | --------- | ---- |
| A. Close the must-haves | The engineering items in [what is left](../planning/poc/011-what-is-left.md#4-engineering-work) that touch PoC-4 and PoC-5: the sidecar hostile suite in CI, MinIO through the chassis, the T10 probe, throughput at 4 pairs on Linux (122 X-5); plus default-deny egress as a product rule ([026 CH-4](../planning/issues/026-CH-4-chassis-only-credentials-egress.md)) and the admission rules in the chart | every PoC-1 to PoC-5 flag has plain evidence; the chart carries the egress and admission rules | 2 weeks |
| B. Models and the judge | `ModelSpec.routes` and `judge_route`; the gate on by default, async at 0.1 on `decision-small`; the judge's cost, latency, and agreement from the Mac run ([what is left](../planning/poc/011-what-is-left.md#1-runs-on-the-users-mac)); the retry stage on a model error ([017 H-4](../planning/issues/017-H-4-harness-features.md)) | a config map swaps a model per task with no agent change; the judge's numbers are recorded and the defaults confirmed or changed by ADR-010's rule | 2 weeks |
| C. LangChain first | LangGraph as the template default (step 1: commit `ed1e834` on branch `claude/recursing-wozniak-9e4b4d`, not merged, so `main` still defaults to `pydanticai`); the MCP stand-in replaced by `langchain-mcp-adapters` on `mcp` 2, or a test that fails when it is ready; a trace-join test that shows LangGraph's graph steps under the run span; a `langchain` `create_agent` variant; the checkpointer and interrupt decisions (an ADR each, `chassis-architect`) | `agentctl new` defaults to LangGraph and passes the lane contract and the evals; the two ADR-006 Revisit triggers are closed | 2 weeks |
| D. LangSmith | The `otlphttp/langsmith` exporter in the Collector; `fake-langsmith`; the `langsmith` feedback adapter; LangSmith datasets against the 20 golden cases (ADR-010's trial rule); the two stack tests | one trace, one score, and one eval run visible in LangSmith, with the key only in the Collector and the chassis | 1–2 weeks |
| E. Measure and show | SLOs and alerts ([117 X-3](../planning/issues/117-X-3-slos-alerts.md)); the container dashboard; a guard-stage span; sampling in the Collector; the OWASP agentic threat list (ASI01–ASI10) mapped to the controls; the weekly record | the north star, inputs, and counters are on one dashboard with one week of real numbers | 2 weeks |
| F. Template GA | The chart in the generated repo and a published platform pin ([059 H-15](../planning/issues/059-H-15-agent-factory-cli.md), 025 H-10); a per-case scriptable fake model; a trial by a person new to the code | a person goes from `agentctl new` to a demo in under a day, recorded | 1–2 weeks |

The user's own items, Mac runs and repository settings, are in [what is left](../planning/poc/011-what-is-left.md), sections 1 to 3. The product cannot close phase B or E without the Mac run.

## Gaps the PoC track did not cover

These are the product's gaps, beyond the per-PoC flags in [what is left](../planning/poc/011-what-is-left.md). P1 blocks a first production agent; P2 blocks the metrics; P3 is next.

| # | Gap | Phase |
| - | --- | ----- |
| P1 | No route map per task: one route per agent, no per-request or per-task choice | B |
| P1 | The judge is off in every profile; the gate is off; no real judge numbers | B |
| P1 | Default-deny egress is not a product rule; the admission rules are not in the chart | A |
| P1 | LangSmith is in no code or config; Langfuse is the only `FeedbackPort` backend | D |
| P1 | LangGraph's MCP client is a stand-in; its instrumentor coverage is a doc claim | C |
| P2 | No SLOs, no alerts, no container dashboard, no weekly record | E |
| P2 | No guard-stage span; sampling deferred; the OTel SDK's cost unmeasured; `/metrics` open by default | E |
| P2 | The OWASP agentic threat list and the vault's threat model are not mapped to the controls | E |
| P2 | The retry stage on a model error is not built; only the gate retries | B |
| P2 | The template's fake model cannot be scripted per case; the platform pin is a local symlink; no trial by a person | F |
| P3 | A LangGraph agent keeps no state (no checkpointer); interrupts have no place in the `handle` contract | C |
| P3 | Caller API keys, `user.id` hashing, rate limits with auth off, the URL allow-list default (ADR-008 open questions) | the user |
| P3 | Feedback: subject binding, a reviewer role, comment redaction (ADR-009 open questions) | the user |
| P3 | HPA on CPU only; the `remote` lane has one replica and no HPA | later ([104 O-3](../planning/issues/104-O-3-agent-pools-keda.md)) |
| P3 | The public A2A endpoint, the first cloud, and the broker are open decisions | [001 DEC-1](../planning/issues/001-DEC-1-resolve-open-decisions.md) |
| P3 | Orchestration of many agents (the epic's figure 3) | after the MVP |

## What is reused

Product code, as is: `packages/chassis`, `contract-suites`, `workload-a2a`, `agent-testkit`, `agentctl`, `code-runner`, `guardrail-server`, `deploy/helm/agent-lib`, `deploy/dashboards`, `evals/deepeval` and the golden sets in `eval-kit`. Changed by this plan: `chassis/server/config.py` (the route map), `profiles.py` (the judge on in `cloud`), `chassis/dashboards` (the container dashboard), `otel-collector.yaml` (the LangSmith exporter), `agentctl` (the default, the chart, the published pin). Test-only, kept: the fake servers, `poc-harness`, `load-tools`, the Compose and kind stacks. Parked: `EncoderEvaluator`, the vLLM adapter, the router SLM, PoC-6c, `evals/promptfoo`, Dapr, and the `remote`-only engines as a first choice.

Related: [the specs](../specs/README.md), [the PoC track](../planning/poc/000-plan.md), [the backlog plan](../planning/issues/000-plan.md).
