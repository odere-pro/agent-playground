"""Source of truth for the backlog: order, IDs, titles, waves, priorities, sizes, dependencies, epic refs.

Edit ROWS here, then run `python3 tools/sync.py`. The position in ROWS is the issue index (execution order).
"""
import re
import sys
from pathlib import Path

HERE = Path(__file__).parent

WAVES = {
    "W0": "W0 Decisions",
    "W1": "W1 Router and baseline",
    "W2": "W2 Chassis MVP",
    "W3": "W3 Simplifier SLM",
    "W4": "W4 Simplifier agent live",
    "W5": "W5 Chassis completion",
    "W6": "W6 Golden set and evaluator SLM",
    "W7": "W7 Registry and governance gate",
    "W8": "W8 Platform MCP server",
    "W9": "W9 Orchestrator",
    "W10": "W10 Production readiness",
    "W11": "W11 Compliance evidence",
    "W12": "W12 Later",
}

PHASES = {
    "cross": ("phase:cross", "open-decisions", "Open decisions"),
    "0": ("phase:0-harness", "phase-0-agent-harness", "Phase 0"),
    "1": ("phase:1-router", "phase-1-baseline-and-llm-router", "Phase 1"),
    "2": ("phase:2-simplifier-slm", "phase-2-simplifier-slm-and-model-lifecycle", "Phase 2"),
    "3": ("phase:3-simplifier-agent", "phase-3-simplifier-agent", "Phase 3"),
    "4": ("phase:4-golden-set", "phase-4-golden-set-agent-and-evaluator-slm", "Phase 4"),
    "5": ("phase:5-registry", "phase-5-service-registry", "Phase 5"),
    "6": ("phase:6-mcp-server", "phase-6-platform-mcp-server", "Phase 6"),
    "7": ("phase:7-orchestrator", "phase-7-orchestrator", "Phase 7"),
    "8": ("phase:8-production", "phase-8-production-readiness", "Phase 8"),
    "9": ("phase:9-governance", "phase-9-governance-and-compliance", "Phase 9"),
    "10": ("phase:10-shared-memory", "phase-10-shared-memory-at-scale", "Phase 10"),
    "11": ("phase:11-later", "phase-11-later-ideas", "Phase 11"),
}

# (id, title, slug, wave, phase, area, priority, size, deps, refs, type, note); index = position.
# deps are epic IDs; refs are epic section keys (F.1, Fig.2, R13, H, I, J, or heading slugs prefixed with '#').
ROWS = [
    ('DEC-1', 'Resolve the open decisions that block the backlog', 'resolve-open-decisions', 'W0', 'cross', 'planning', 'P0', 'S', [], ['#open-decisions', '#success-metrics', 'D.1', 'H'], 'decision', "Derived from the epic's Open decisions. Blocks the event port, broker, first cloud, the public A2A endpoint, and the rewrite rules."),
    ('G-1', 'LiteLLM router in front of all model calls', 'litellm-router', 'W1', '1', 'router', 'P0', 'M', [], ['Fig.1', 'H', 'R3'], 'story', 'First in the backlog: the baseline (G-3) needs at least two weeks of data, so the router must count tokens as early as possible.'),
    ('G-1b', 'Named model routes, swapped in router config only', 'named-model-routes', 'W1', '1', 'router', 'P0', 'S', ['G-1'], ['C.1', 'I'], 'story', ''),
    ('G-2', 'Token and cost counting per request, user, and agent', 'token-cost-counting', 'W1', '1', 'router', 'P0', 'M', ['G-1'], ['J'], 'story', ''),
    ('G-3', 'Baseline report: token spend and cost over at least two weeks', 'baseline-report', 'W1', '1', 'router', 'P0', 'M', ['G-2'], ['J', '#success-metrics'], 'story', 'Start collecting on day one of G-2; the report itself is written after two weeks of data.'),
    ('G-6', 'Savings dashboard with live token spend', 'savings-dashboard', 'W1', '1', 'router', 'P0', 'M', ['G-2'], ['G.2', '#success-metrics'], 'story', 'P0 because it is how the epic goal (token and cost savings) is made visible.'),
    ('H-1', 'Chassis package and image: ports, `handle` contract, JSON event schema, envelope', 'harness-library-ports-envelope', 'W2', '0', 'harness', 'P0', 'L', [], ['F.1', 'F.2', 'G.1'], 'story', ''),
    ('H-14', 'One agent interface for all classes (stateless, orchestrator, data)', 'one-agent-interface', 'W2', '0', 'harness', 'P0', 'M', ['H-1'], ['B.1', 'B.5', 'Fig.1'], 'story', ''),
    ('CH-1', 'Engine connectors `inprocess` and `sidecar` over A2A, plus the template A2A server that wraps `handle`', 'engine-connectors-a2a', 'W2', '0', 'harness', 'P0', 'M', ['H-1', 'H-14'], ['F.1', 'F.2', 'G.1'], 'story', 'From ADR-001: sidecar is the default lane, and A2A is the one contract to workloads. Needed by the testing kit and the local profile.'),
    ('H-12', 'Config loader: object storage, JSON Schema check, reload on change', 'config-loader', 'W2', '0', 'harness', 'P0', 'M', ['H-1'], ['C.1', 'E.4'], 'story', 'The config JSON Schema includes the governance block fields from day one (compliance by design); C-1 later adds enforcement on activation.'),
    ('H-2', 'Inbound adapters: native, OpenAI-compatible, Anthropic-compatible, with streaming', 'inbound-adapters', 'W2', '0', 'harness', 'P0', 'L', ['H-1', 'H-14', 'CH-1'], ['F.2', 'G.1', 'R2'], 'story', ''),
    ('H-3', 'Model port to the LLM router, plus direct vLLM and llama.cpp adapters', 'model-port', 'W2', '0', 'harness', 'P0', 'M', ['G-1b', 'H-1', 'G-2'], ['F.1', 'I', 'R3'], 'story', ''),
    ('CH-2', "Outbound model proxy: an OpenAI- and Anthropic-compatible URL for workloads, forwarded with the service's scoped key", 'outbound-model-proxy', 'W2', '0', 'harness', 'P0', 'M', ['H-3', 'CH-1'], ['F.1', 'R3', 'J'], 'story', 'From ADR-001: workloads hold no key, so their model calls go through the chassis.'),
    ('H-7', 'Observability: logs, traces, metrics, Langfuse', 'observability', 'W2', '0', 'harness', 'P0', 'S', ['H-1', 'H-3', 'CH-1'], ['G.2'], 'story', ''),
    ('H-8', 'Testing kit: fake adapters, contract tests per API, record and replay', 'testing-kit', 'W2', '0', 'harness', 'P0', 'L', ['H-2', 'H-3', 'CH-1'], ['F.1'], 'story', ''),
    ('H-9', 'Local debug profile: Docker Compose, hot reload, debugger port', 'local-debug-profile', 'W2', '0', 'harness', 'P0', 'S', ['H-12', 'H-2', 'H-7', 'CH-1'], ['G.4'], 'story', ''),
    ('H-4', 'Harness features: schema check, evaluator gate, retry, fallback, timeout, budget', 'harness-features', 'W2', '0', 'harness', 'P0', 'L', ['H-12', 'H-3', 'CH-1', 'CH-2'], ['B.2', 'G.1'], 'story', 'Idempotency is owned by H-18, not this issue.'),
    ('H-18', 'Idempotency: key check, optional Valkey result cache, key passed to write tools', 'idempotency', 'W2', '0', 'harness', 'P0', 'M', ['H-1', 'H-9', 'CH-1'], ['B.2', 'D.1'], 'story', ''),
    ('H-17', 'Event port: one-way CloudEvents result events to the broker', 'event-port', 'W2', '0', 'harness', 'P0', 'M', ['DEC-1', 'H-1', 'CH-1'], ['B.4', 'D.1', 'D.2', 'R13'], 'story', 'Ships the first broker adapter (the one chosen in DEC-1). Other adapters are H-23.'),
    ('X-8', 'Event broker in Docker Compose and Kubernetes, with retention and consumer-lag alerts', 'event-broker', 'W2', '8', 'infra', 'P0', 'M', ['DEC-1', 'H-9'], ['D.1', 'H'], 'story', 'Pulled forward from Phase 8: the event port, the recorder, and the audit log all need a running broker.'),
    ('H-20', 'Event consumer adapter: agents started by CloudEvents, same core as REST', 'event-consumer-adapter', 'W2', '0', 'harness', 'P0', 'S', ['H-2', 'H-17', 'X-8'], ['D.1', 'Fig.2', 'R13'], 'story', 'Moved into the harness MVP: the recorder (D-0) consumes result events, and the Phase 0 done-when needs agents started by events.'),
    ('H-6', 'Security middleware: auth, scopes, rate limits, PII redaction', 'security-middleware', 'W2', '0', 'harness', 'P0', 'M', ['H-2', 'H-18'], ['G.3'], 'story', ''),
    ('C-5', 'AI-generated marking on outputs and a disclosure flag for client apps', 'ai-generated-marking', 'W2', '9', 'governance', 'P1', 'S', ['H-12', 'H-2', 'H-17'], ['E.2', 'E.4', 'E.5', 'R10'], 'story', 'Pulled forward from Phase 9: it is cheap to add in the harness now, and AI Act Art. 50(2) marking applies to systems already on the market from 2 December 2026.'),
    ('CH-3', 'Shared Helm library chart: chassis sidecar with a pinned tag, secrets on the chassis only, Service on the chassis port', 'helm-library-chart', 'W2', '0', 'harness', 'P0', 'M', ['CH-1', 'H-9'], ['G.4', 'H'], 'story', 'From ADR-001: the chassis tag is set once, in one chart every service includes. Split out of H-10, which was too big.'),
    ('H-10', 'Service template: workload Dockerfile, Helm chart on the chassis library chart, CI (tests, signing, deploy, registration)', 'template-repo', 'W2', '0', 'harness', 'P0', 'L', ['H-8', 'H-9', 'CH-3'], ['G.4', 'H'], 'story', 'The registry registration step is a stub until the registry exists (R-1, R-2).'),
    ('CH-4', 'Chassis-only credentials for every internal service, and default-deny egress per workload pod', 'chassis-only-credentials-egress', 'W2', '0', 'harness', 'P0', 'M', ['G-1', 'X-8', 'H-6', 'H-10'], ['G.3', 'G.4'], 'story', 'ADR-001 hard requirement 1: a release gate for every service, so it comes before the simplifier ships.'),
    ('S-1', 'Rewrite rules and banned-word list', 'rewrite-rules', 'W3', '2', 'slm', 'P0', 'S', ['DEC-1'], ['C.1', 'F.9'], 'story', 'Data work can start during W2, in parallel with the harness.'),
    ('S-3', 'Simplifier metrics: tokens, readability, semantic similarity, facts_kept, banned words', 'simplifier-metrics', 'W3', '2', 'slm', 'P0', 'M', ['S-1'], ['I', '#success-metrics'], 'story', 'Placed before data generation so every generated pair can be scored.'),
    ('S-2', 'Training data generation: 2,000 to 5,000 pairs with an open-weight teacher', 'training-data-generation', 'W3', '2', 'slm', 'P0', 'M', ['G-1b', 'S-1', 'S-3'], ['I', 'R1', '#principles'], 'story', ''),
    ('S-4', 'Hand review of a sample and a held-out test set of at least 300 records', 'hand-review-test-set', 'W3', '2', 'slm', 'P0', 'M', ['S-2'], ['E.5'], 'story', ''),
    ('G-5', 'Cost model: GPU hosting cost vs API savings, with the break-even point', 'cost-model-break-even', 'W3', '1', 'router', 'P0', 'S', ['G-3'], ['J', '#risks'], 'story', 'Placed right before GPU spend (training and serving): it is the mitigation for the top risk, GPU costs more than the tokens saved.'),
    ('M-1', 'MLflow: experiments, model registry, links to data version and scores', 'mlflow', 'W3', '2', 'lifecycle', 'P0', 'S', [], ['E.5', 'H'], 'story', ''),
    ('S-5', 'Reproducible training pipeline (config, data version, seed) with Unsloth or TRL', 'training-pipeline', 'W3', '2', 'slm', 'P0', 'M', ['S-4', 'M-1'], ['E.3', 'H'], 'story', ''),
    ('C-8', 'Training compute logged per fine-tune run, checked against the GPAI threshold', 'training-compute-log', 'W3', '9', 'governance', 'P1', 'S', ['S-5'], ['E.3', 'R11'], 'story', 'Pulled forward from Phase 9: cheapest to add while the training pipeline is being built.'),
    ('S-6', 'Train candidates on Qwen3, Gemma 3, and Llama 3.2, and pick the best', 'train-candidates', 'W3', '2', 'slm', 'P0', 'L', ['G-5', 'S-5'], ['I'], 'story', ''),
    ('S-7', 'Serve the simplifier SLM with vLLM, with guided decoding for JSON', 'serve-with-vllm', 'W3', '2', 'slm', 'P0', 'S', ['G-1b', 'S-6'], ['I', 'R2'], 'story', ''),
    ('M-2', 'Eval gate in CI: a new model ships only if it beats the current one', 'eval-gate-ci', 'W3', '2', 'lifecycle', 'P0', 'M', ['S-3', 'S-4', 'M-1', 'X-8'], ['E.5', '#principles'], 'story', 'P0 because of the principle: nothing ships without an eval.'),
    ('X-1a', 'Terraform module for the first cloud (AWS or GCP)', 'terraform-first-cloud', 'W3', '8', 'infra', 'P0', 'L', ['DEC-1', 'H-10', 'CH-4'], ['G.4', 'H'], 'story', 'Split from X-1 and pulled forward from Phase 8: the simplifier needs a real environment to show savings. X-1b adds the second cloud.'),
    ('X-2', 'GPU setup on Kubernetes (NVIDIA GPU Operator) and model weight storage with fast cold start', 'gpu-setup', 'W3', '8', 'infra', 'P1', 'M', ['S-7', 'X-1a'], ['G.4', 'H'], 'story', 'Pulled forward from Phase 8 so the SLM can be served in the cloud. P1 because a rented GPU host can serve the first version.'),
    ('D-0', 'Recorder agent: reads result events, removes PII, drops duplicates, writes records to Postgres', 'recorder-agent', 'W4', '4', 'data', 'P0', 'M', ['H-14', 'H-17', 'X-8', 'H-20', 'H-6'], ['B.4', 'D.1', 'D.3'], 'story', "Pulled forward from Phase 4: the simplifier agent's done-when says every call is recorded by the recorder."),
    ('A-1', 'Simplifier business logic as a workload behind the chassis', 'simplifier-core', 'W4', '3', 'agent', 'P0', 'M', ['H-4', 'H-10', 'S-7', 'CH-2', 'CH-4'], ['Fig.2', 'B.1'], 'story', ''),
    ('A-2', 'Evaluator gate on facts_kept, one retry, then fallback to a big model', 'evaluator-gate-fallback', 'W4', '3', 'agent', 'P0', 'S', ['H-4', 'S-3', 'A-1'], ['B.2'], 'story', 'Uses a big-model judge until the evaluator SLM replaces it (E-3).'),
    ('A-3', 'Result events with source, stored by the recorder', 'result-events', 'W4', '3', 'agent', 'P0', 'S', ['H-17', 'D-0', 'A-1'], ['B.4', 'D.3'], 'story', 'Last P0 story: first measured savings.'),
    ('G-4', 'History swap in the chassis: long past answers replaced by simplified versions', 'history-swap', 'W4', '1', 'router', 'P1', 'M', ['H-3', 'D-0', 'CH-2'], ['F.1', 'J'], 'story', ''),
    ('A-4', 'History swap switched on for the simplifier', 'history-swap-on', 'W4', '3', 'agent', 'P1', 'S', ['A-3', 'G-4'], ['J'], 'story', ''),
    ('A-5', 'Simplifier agent dashboard: pass rate, fallback rate, tokens saved, latency', 'agent-dashboard', 'W4', '3', 'agent', 'P1', 'S', ['H-7', 'A-2'], ['G.2', '#success-metrics'], 'story', ''),
    ('M-3', 'Shadow mode: new model runs next to the current one without serving users', 'shadow-mode', 'W4', '2', 'lifecycle', 'P1', 'M', ['M-2', 'A-1', 'A-2', 'CH-2'], ['G.4'], 'story', ''),
    ('M-4', 'Canary rollout and one-step rollback', 'canary-rollback', 'W4', '2', 'lifecycle', 'P1', 'S', ['M-3', 'A-5'], ['G.4'], 'story', ''),
    ('M-5', 'Quantized 4-bit variants for CPU and edge, each with its own eval', 'quantized-variants', 'W4', '2', 'lifecycle', 'P2', 'M', ['M-2'], ['I'], 'story', ''),
    ('CH-5', 'Service operations: declared in config, routed by the chassis through the pipeline', 'service-operations', 'W5', '0', 'harness', 'P1', 'M', ['H-2', 'H-12'], ['G.1', 'B.5'], 'story', "From ADR-001: the chassis owns the public port, so a service's own operations enter through it."),
    ('H-13', 'OpenAPI 3.1 spec per agent, with MCP tools generated from it', 'openapi-mcp-tools', 'W5', '0', 'harness', 'P1', 'S', ['H-2', 'CH-5'], ['G.1'], 'story', ''),
    ('H-21', 'AsyncAPI 3.0 spec per agent, generated next to the OpenAPI spec', 'asyncapi-spec', 'W5', '0', 'harness', 'P1', 'S', ['H-17', 'H-13'], ['D.1', 'R14'], 'story', ''),
    ('H-22', 'Event reliability: idempotent consumers, retries with backoff, dead-letter topic', 'event-reliability', 'W5', '0', 'harness', 'P1', 'S', ['H-18', 'H-20'], ['D.1'], 'story', ''),
    ('H-16', 'Tool port: native tool calls or JSON via guided decoding; read and write modes', 'tool-port', 'W5', '0', 'harness', 'P1', 'M', ['H-3', 'H-18', 'CH-2'], ['B.3', 'R2'], 'story', ''),
    ('CH-6', 'Remote lane and the trust rule: sandboxed pod or managed runtime over A2A, cloud auth adapter, admission check', 'remote-lane-trust-rule', 'W5', '0', 'harness', 'P1', 'L', ['CH-1', 'CH-4', 'H-12', 'X-1a'], ['G.3', 'G.4', 'H'], 'story', 'From ADR-001: untrusted workloads and managed runtimes use the remote lane. Not supported until this ships (hard requirement 2).'),
    ('CH-7', 'Chassis release in rings, with a per-service pin and a minimum-version admission rule', 'chassis-release-rings', 'W5', '0', 'harness', 'P1', 'M', ['CH-3', 'CH-6', 'H-7'], ['G.4'], 'story', 'From ADR-001: one image rolled out in rings, with no service rebuild. Placed before the registry and orchestrator add many services.'),
    ('H-19', 'Stateless check in CI: fail the build on local disk writes or kept data', 'stateless-check-ci', 'W5', '0', 'harness', 'P1', 'S', ['H-10'], ['B.2'], 'story', ''),
    ('CH-8', 'Framework event mappings and one non-Python workload in the service template', 'framework-event-mappings', 'W5', '0', 'harness', 'P1', 'M', ['H-8', 'H-10'], ['F.1', 'B.5'], 'story', "From ADR-001: each service maps its framework's events to the chassis schema, and the template ships the mappings."),
    ('H-15', 'Agent factory CLI: scaffold an agent by class and kind, modules by config', 'agent-factory-cli', 'W5', '0', 'harness', 'P1', 'M', ['H-14', 'H-10', 'H-13', 'H-19', 'CH-8', 'CH-6'], ['B.5'], 'story', ''),
    ('H-23', 'More broker adapters behind the event port: Kafka, AWS, GCP', 'broker-adapters', 'W5', '0', 'harness', 'P2', 'S', ['H-20'], ['D.1', 'H'], 'story', 'The first adapter ships in H-17; this issue adds the rest.'),
    ('H-5', 'A2A adapter: JSON-RPC endpoint, task states, signed agent card', 'a2a-adapter', 'W5', '0', 'harness', 'P2', 'S', ['DEC-1', 'H-2'], ['G.1', 'B.5'], 'story', 'P2: DEC-1 now decides only the public A2A endpoint. A2A to workloads ships in version 1 per ADR-001 (CH-1).'),
    ('H-11', 'Chassis reuse in other projects: the stock image in front of any A2A workload', 'adapter-package', 'W5', '0', 'harness', 'P2', 'S', ['H-2', 'H-3', 'CH-8'], ['F.1'], 'story', ''),
    ('S-8', 'Optional: fine-tune an SLM on tool-call examples for tool agents', 'tool-call-finetune', 'W5', '2', 'slm', 'P2', 'L', ['S-5', 'H-16'], ['B.3', 'I'], 'story', ''),
    ('D-1', 'Golden set agent: reads records, scores them with a judge, sends uncertain ones to review', 'golden-set-agent', 'W6', '4', 'data', 'P1', 'M', ['H-4', 'D-0', 'A-2'], ['B.1', 'B.4'], 'story', ''),
    ('D-2', 'Review UI: approve, reject, edit, with access control and an audit log', 'review-ui', 'W6', '4', 'data', 'P1', 'M', ['H-6', 'D-1'], ['E.5', 'D.3'], 'story', ''),
    ('D-6', 'Tags and splits (train, test, holdout) per record', 'tags-splits', 'W6', '4', 'data', 'P1', 'S', ['D-1'], ['H'], 'story', ''),
    ('D-5', 'Schema check and deduplication on import', 'import-schema-dedup', 'W6', '4', 'data', 'P1', 'S', ['D-1'], ['H'], 'story', ''),
    ('D-3', 'Import from file upload, API call, or bucket watch', 'import', 'W6', '4', 'data', 'P1', 'S', ['D-5', 'S-4'], ['H'], 'story', ''),
    ('D-7', 'Versioned datasets with lakeFS; each export is an immutable version with an ID', 'lakefs-versions', 'W6', '4', 'data', 'P1', 'M', ['D-1'], ['H', 'R7', 'R8'], 'story', ''),
    ('D-4', 'Export in JSONL, CSV, Parquet, and Hugging Face datasets format', 'export-formats', 'W6', '4', 'data', 'P1', 'S', ['D-6', 'D-7', 'D-3'], ['H'], 'story', ''),
    ('D-8', 'Data rules: PII removal before storage, retention period, deletion on request', 'data-rules', 'W6', '4', 'data', 'P1', 'M', ['D-0', 'D-7', 'D-2', 'D-3'], ['E.5'], 'story', ''),
    ('C-3', 'Audit log data agent: append-only, object lock, retention per risk class, export API', 'audit-log-agent', 'W6', '9', 'governance', 'P1', 'L', ['H-14', 'H-17', 'X-8', 'H-20', 'H-22', 'H-6'], ['E.6', 'D.3'], 'story', 'Pulled forward from Phase 9: same data-agent pattern as the recorder, and later stories (P-4, C-6, C-7) write to it.'),
    ('E-1', 'First evaluator SLM (ModernBERT or DeBERTa) trained on approved records', 'evaluator-slm', 'W6', '4', 'evaluator', 'P1', 'L', ['S-5', 'D-2', 'D-4'], ['I'], 'story', ''),
    ('E-2', 'Evaluator agreement test against human labels', 'evaluator-agreement-test', 'W6', '4', 'evaluator', 'P1', 'S', ['E-1', 'S-4'], ['E.5'], 'story', ''),
    ('E-3', 'Evaluator SLM replaces the big-model judge in the harness', 'evaluator-replaces-judge', 'W6', '4', 'evaluator', 'P1', 'S', ['A-2', 'E-2', 'M-3', 'A-5'], ['F.1', 'J'], 'story', 'Cuts the cost of judging every simplifier call with a big model.'),
    ('R-1', 'Registry data model and API', 'registry-data-model-api', 'W7', '5', 'registry', 'P1', 'L', ['H-14', 'H-13', 'H-17', 'H-6'], ['F.3'], 'story', ''),
    ('C-1', 'Governance block enforced by the registry on activation', 'governance-enforcement', 'W7', '9', 'governance', 'P1', 'S', ['H-12', 'R-1', 'CH-6', 'CH-7'], ['E.4'], 'story', 'Pulled forward from Phase 9: the gate must exist before agents register automatically.'),
    ('R-16', 'Registry entries carry the governance block and serve as the AI system inventory', 'ai-system-inventory', 'W7', '5', 'registry', 'P1', 'S', ['C-1'], ['E.4', 'E.5'], 'story', ''),
    ('C-2', 'Prohibited-practice checklist at registration', 'prohibited-practice-checklist', 'W7', '9', 'governance', 'P1', 'S', ['C-1'], ['E.4', 'R10'], 'story', 'Pulled forward from Phase 9, next to the governance gate.'),
    ('R-7', 'Trust levels: internal signed entries go live, external and manual need approval', 'trust-levels', 'W7', '5', 'registry', 'P1', 'M', ['R-1'], ['F.3', 'E.5', 'R5'], 'story', ''),
    ('R-8', 'Description scanning for hidden instructions on registration', 'description-scanning', 'W7', '5', 'registry', 'P1', 'M', ['R-7'], ['G.3', 'R5'], 'story', ''),
    ('R-9', 'Description hash pinning: a changed description goes back to review', 'description-hash-pinning', 'W7', '5', 'registry', 'P1', 'S', ['R-7'], ['F.3', 'R5'], 'story', ''),
    ('R-2', 'Kubernetes controller (kopf): watches labeled deployments and reads /manifest', 'kubernetes-controller', 'W7', '5', 'registry', 'P1', 'M', ['H-10', 'R-1', 'R-7', 'CH-7'], ['F.3', 'G.4'], 'story', 'Replaces the registration stub in the template CI (H-10).'),
    ('R-3', 'Docker watcher for local runs', 'docker-watcher', 'W7', '5', 'registry', 'P1', 'S', ['H-9', 'R-1', 'R-7'], ['G.4'], 'story', ''),
    ('R-6', 'Manual registration: API, UI, and YAML import', 'manual-registration', 'W7', '5', 'registry', 'P1', 'M', ['R-7'], ['F.3'], 'story', ''),
    ('R-10', 'Health checks: dead entries marked inactive, never deleted', 'health-checks', 'W7', '5', 'registry', 'P1', 'S', ['R-1', 'R-2', 'H-7'], ['F.3'], 'story', ''),
    ('R-4', 'MCP import: call tools/list and register each tool', 'mcp-import', 'W7', '5', 'registry', 'P1', 'S', ['R-8', 'R-9'], ['F.3', 'R5'], 'story', ''),
    ('R-5', 'OpenAPI import: register each operation of a REST API', 'openapi-import', 'W7', '5', 'registry', 'P1', 'M', ['H-13', 'R-8'], ['F.3'], 'story', ''),
    ('R-15', 'Event schemas (AsyncAPI) registered next to OpenAPI specs', 'event-schemas-registered', 'W7', '5', 'registry', 'P1', 'S', ['H-21', 'R-1'], ['D.1', 'R14'], 'story', ''),
    ('R-13', 'Registry search test set of at least 200 queries with expected results', 'search-test-set', 'W7', '5', 'registry', 'P1', 'M', ['R-1'], ['I', '#success-metrics'], 'story', 'Placed before hybrid search so search is built against a test set.'),
    ('R-11', 'Hybrid search: SQL filters, keyword, vector, reciprocal rank fusion, reranker', 'hybrid-search', 'W7', '5', 'registry', 'P1', 'L', ['R-1', 'R-13', 'G-1b', 'S-7'], ['H', 'I', 'R6', 'R9'], 'story', ''),
    ('R-12', 'Search results return the main pick plus ranked fallbacks with difference metadata', 'main-pick-fallbacks', 'W7', '5', 'registry', 'P1', 'M', ['R-11'], ['F.3'], 'story', ''),
    ('R-14', 'Registry exposed as an MCP server', 'registry-mcp-server', 'W7', '5', 'registry', 'P1', 'S', ['H-13', 'R-12'], ['F.4'], 'story', ''),
    ('P-1', 'Platform MCP server built from the harness, with registry and agent tools', 'platform-mcp-server', 'W8', '6', 'mcp', 'P1', 'S', ['H-13', 'R-14'], ['F.4'], 'story', ''),
    ('P-4', 'MCP read and write scopes, confirmation on destructive tools, audit log on every call', 'mcp-scopes-audit', 'W8', '6', 'mcp', 'P1', 'M', ['C-3', 'P-1'], ['G.3'], 'story', 'Placed right after P-1: no write tools are exposed before scopes and audit exist.'),
    ('P-2a', 'MCP golden set and metrics tools', 'mcp-golden-set-metrics-tools', 'W8', '6', 'mcp', 'P1', 'M', ['G-6', 'D-4', 'P-4', 'A-5'], ['F.4'], 'story', 'Split from P-2: the golden set tools are needed for the Phase 6 done-when (export a golden set version end to end).'),
    ('P-3', 'MCP resources and prompts', 'mcp-resources-prompts', 'W8', '6', 'mcp', 'P2', 'S', ['P-1'], ['F.5'], 'story', ''),
    ('O-10', 'Orchestrator config and workflow schemas (JSON Schema) in the config store', 'orchestrator-schemas', 'W9', '7', 'orchestrator', 'P1', 'M', ['H-12'], ['C.2'], 'story', 'First in the orchestrator wave: orchestration is config, so the schemas come before the engine.'),
    ('O-1', 'Orchestrator agent built from the harness, using registry search', 'orchestrator-agent', 'W9', '7', 'orchestrator', 'P1', 'M', ['H-14', 'R-12', 'O-10'], ['Fig.3', 'F.6'], 'story', ''),
    ('O-2', 'Temporal workflows with a checkpoint per step', 'temporal-checkpoints', 'W9', '7', 'orchestrator', 'P1', 'M', ['O-1', 'CH-5'], ['F.8'], 'story', ''),
    ('O-13', 'Version pinning per run', 'version-pinning', 'W9', '7', 'orchestrator', 'P1', 'S', ['O-2'], ['C.2'], 'story', ''),
    ('O-7', 'Idempotent steps, so a resume never repeats a side effect', 'idempotent-steps', 'W9', '7', 'orchestrator', 'P1', 'M', ['H-18', 'O-2'], ['B.2', 'D.1'], 'story', ''),
    ('O-11', 'Workflow engine that runs chain, fan_out, and compare patterns from workflow files', 'workflow-engine', 'W9', '7', 'orchestrator', 'P1', 'L', ['O-10', 'O-2'], ['C.2', 'F.7'], 'story', ''),
    ('O-3', 'Agent pools on task queues with KEDA autoscaling', 'agent-pools-keda', 'W9', '7', 'orchestrator', 'P1', 'M', ['H-10', 'O-2'], ['F.6', 'J'], 'story', ''),
    ('O-4', 'Fan-out and fan-in with overlap and a consistency pass', 'fan-out-fan-in', 'W9', '7', 'orchestrator', 'P1', 'M', ['O-11', 'O-3'], ['F.7', 'C.2'], 'story', ''),
    ('O-5', 'Compare runs with an evaluator and a tie rule', 'compare-runs', 'W9', '7', 'orchestrator', 'P1', 'M', ['O-11', 'D-3'], ['F.7'], 'story', ''),
    ('O-6', 'Budgets per run: max tokens, cost, steps, and time', 'run-budgets', 'W9', '7', 'orchestrator', 'P1', 'S', ['O-2', 'O-11'], ['C.2'], 'story', ''),
    ('O-12', 'Routing rules and planner modes (fixed, planned, hybrid)', 'routing-planner-modes', 'W9', '7', 'orchestrator', 'P1', 'L', ['O-11'], ['C.2'], 'story', ''),
    ('O-8', 'Cancel and human approval steps', 'cancel-approval', 'W9', '7', 'orchestrator', 'P1', 'S', ['O-2', 'O-6'], ['C.2', 'E.5'], 'story', ''),
    ('C-6', 'Human oversight controls: review queue, approval steps, stop control, all audited', 'human-oversight', 'W9', '9', 'governance', 'P1', 'M', ['D-2', 'C-3', 'O-8'], ['E.5', 'Fig.3'], 'story', "Pulled forward from Phase 9: built together with the orchestrator's approval and cancel steps."),
    ('O-14', 'Workflow validation and dry run before activation, and rollback', 'workflow-validation-rollback', 'W9', '7', 'orchestrator', 'P1', 'M', ['O-11'], ['C.2'], 'story', ''),
    ('O-16', 'Event triggers: workflows started by events, run and step events at each stage', 'event-triggers', 'W9', '7', 'orchestrator', 'P1', 'M', ['H-20', 'O-2', 'O-12', 'O-11'], ['D.1', 'D.3'], 'story', ''),
    ('O-9', 'Short-term memory (execution state) and long-term memory (settings, gotchas, feedback)', 'orchestrator-memory', 'W9', '7', 'orchestrator', 'P1', 'L', ['O-2', 'D-3'], ['F.8', 'F.9'], 'story', ''),
    ('P-2b', 'MCP run, memory, config, and workflow tools', 'mcp-run-memory-config-tools', 'W9', '6', 'mcp', 'P1', 'M', ['P-4', 'O-8', 'O-14', 'O-9'], ['F.4'], 'story', 'Split from P-2: these tools need the orchestrator. Also covers the Config and Workflows tools listed in F.4.'),
    ('O-15', 'Save a reviewed planned run as a new workflow file', 'save-planned-run', 'W9', '7', 'orchestrator', 'P2', 'S', ['O-12', 'O-14', 'C-6'], ['C.2'], 'story', ''),
    ('X-1b', 'Terraform module for the second cloud, with the same inputs and outputs', 'terraform-second-cloud', 'W10', '8', 'infra', 'P1', 'L', ['X-1a', 'CH-6'], ['G.4', 'H'], 'story', 'Split from X-1: the Phase 8 done-when needs both clouds.'),
    ('X-3', 'SLOs and alerts for every agent', 'slos-alerts', 'W10', '8', 'infra', 'P1', 'M', ['H-7', 'X-1a', 'CH-7'], ['G.2'], 'story', ''),
    ('C-7', 'Incident flow: incident events, alert routing, runbook for serious incidents', 'incident-flow', 'W10', '9', 'governance', 'P1', 'M', ['C-3', 'X-3'], ['D.3', 'E.5'], 'story', ''),
    ('X-6', 'Runbooks for common failures', 'runbooks', 'W10', '8', 'infra', 'P1', 'M', ['X-3'], ['E.5'], 'story', ''),
    ('X-7', 'Cost dashboard per agent and per pool', 'cost-dashboard', 'W10', '8', 'infra', 'P1', 'S', ['G-2', 'O-3'], ['J'], 'story', ''),
    ('X-4', 'Backups and a tested restore for Postgres, object storage, and golden sets', 'backups-restore', 'W10', '8', 'infra', 'P1', 'M', ['X-1a', 'D-7'], ['E.6'], 'story', ''),
    ('X-5', 'Load tests for agent pools and the registry', 'load-tests', 'W10', '8', 'infra', 'P1', 'M', ['R-11', 'O-3'], ['G.4'], 'story', ''),
    ('C-4', 'Documentation pack generator: technical docs, instructions for use, model card per agent version', 'documentation-pack', 'W11', '9', 'governance', 'P1', 'M', ['M-1', 'H-13', 'H-21', 'R-16'], ['E.5', 'R12'], 'story', ''),
    ('C-9', 'Control mapping report: ISO 42001 Annex A and AI Act articles to platform evidence', 'control-mapping-report', 'W11', '9', 'governance', 'P1', 'S', ['C-3', 'C-4'], ['E.5', 'R12'], 'story', ''),
    ('L-1', 'Shared memory at scale: session context, long-term memory, context_ref', 'shared-memory-at-scale', 'W12', '10', 'memory', 'P3', 'L', ['O-9'], ['#phase-10-shared-memory-at-scale', 'F.9', 'G.1'], 'later', 'Derived from Phase 10 (plan now, build when scaling).'),
    ('L-2', 'System prompt improver', 'system-prompt-improver', 'W12', '11', 'slm', 'P3', 'L', ['D-4', 'E-3'], ['#phase-11-later-ideas'], 'later', 'Derived from Phase 11.'),
    ('L-3', "Router SLM: classifier that replaces part of the orchestrator's routing", 'router-slm', 'W12', '11', 'orchestrator', 'P3', 'L', ['D-0', 'O-12'], ['#phase-11-later-ideas'], 'later', 'Derived from Phase 11.'),
]


# Layer per issue: chassis (standard for every service), agent-profile (agent-only modules), platform (built on the chassis).
CHASSIS = {"H-1", "H-14", "H-12", "H-2", "H-7", "H-8", "H-9", "H-4", "H-18", "H-17", "H-20", "H-6", "H-10",
           "H-13", "H-21", "H-22", "H-19", "H-15", "H-23", "H-11",
           "CH-1", "CH-2", "CH-3", "CH-4", "CH-5", "CH-6", "CH-7", "CH-8"}
AGENT_PROFILE = {"H-2", "H-3", "H-4", "H-6", "H-5", "H-16", "C-5", "G-4", "CH-2"}


def layers(eid):
    out = [l for l, ids in (("chassis", CHASSIS), ("agent-profile", AGENT_PROFILE)) if eid in ids]
    return out or ["platform"]

EPIC = "../slm-agent-platform-epic-v3.md"


def ref_anchor(ref):
    if ref.startswith("#"):
        return ref[1:], {
            "open-decisions": "Open decisions",
            "success-metrics": "Success metrics",
            "principles": "Principles",
            "risks": "Risks",
            "phase-10-shared-memory-at-scale": "Phase 10",
            "phase-11-later-ideas": "Phase 11",
        }[ref[1:]]
    if ref.startswith("Fig."):
        return "fig" + ref[4:], "Fig. " + ref[4:]
    if re.fullmatch(r"R\d+", ref):
        return ref.lower(), ref
    if re.fullmatch(r"[A-K]", ref):
        return "app-" + ref.lower(), ref
    if re.fullmatch(r"[A-K]\.\d+", ref):
        return ref.replace(".", "").lower(), ref
    raise ValueError(ref)


def build():
    by_idx = {}
    id2idx = {r[0]: n for n, r in enumerate(ROWS, 1)}
    assert len(id2idx) == len(ROWS), "duplicate epic IDs"
    for idx, r in enumerate(ROWS, 1):
        eid, title, slug, wave, phase, area, prio, size, deps, refs, typ, note = r
        deps = [id2idx[d] for d in deps]
        by_idx[idx] = dict(
            index=idx, epic_id=eid, title=title, slug=slug, wave=wave, milestone=WAVES[wave],
            phase=phase, area=area, priority=prio, size=size, deps=deps, refs=refs, type=typ,
            note=note, file=f"{idx:03d}-{eid}-{slug}.md", blocks=[], layers=layers(eid),
        )
    assert sorted(by_idx) == list(range(1, len(ROWS) + 1)), "indexes must be contiguous"
    for it in by_idx.values():
        for d in it["deps"]:
            assert d < it["index"], (it["epic_id"], d)
            by_idx[d]["blocks"].append(it["index"])
    return by_idx


def label(it):
    return f'{it["index"]:03d} {it["epic_id"]}'


def render(it, by_idx):
    phase_label, phase_anchor, phase_name = PHASES[it["phase"]]
    labels = [it["type"], f'priority:{it["priority"]}', phase_label, f'area:{it["area"]}', f'size:{it["size"]}'] + [f'layer:{l}' for l in layers(it["epic_id"])]
    fm = [
        "---",
        f'title: "{it["epic_id"]}: {it["title"]}"',
        "labels: [" + ", ".join(f'"{l}"' for l in labels) + "]",
        f'milestone: "{it["milestone"]}"',
        f'index: {it["index"]}',
        f'epic_id: {it["epic_id"]}',
        "depends_on: [" + ", ".join(f'"{label(by_idx[d])}"' for d in it["deps"]) + "]",
        "blocks: [" + ", ".join(f'"{label(by_idx[b])}"' for b in it["blocks"]) + "]",
        "epic_refs: [" + ", ".join(r.lstrip("#") for r in it["refs"]) + "]",
        "---",
    ]
    link = lambda i: f"[{label(by_idx[i])}]({by_idx[i]['file']})"
    deps = ", ".join(link(d) for d in it["deps"]) or "none"
    blocks = ", ".join(link(b) for b in it["blocks"]) or "none"
    where = f"[{it['epic_id']} in {phase_name}]({EPIC}#{phase_anchor})" if it["type"] == "story" else f"[{phase_name}]({EPIC}#{phase_anchor})"
    source = f"- Epic story: {where}"
    if it["epic_id"].startswith("CH-"):
        source = f"- Source: [ADR-001](../adr/001-chassis-delivery-model.md), not an epic story. Epic phase: [{phase_name}]({EPIC}#{phase_anchor})"
    refs = " · ".join(f"[{name}]({EPIC}#{a})" for a, name in map(ref_anchor, it["refs"]))
    body = [
        "",
        "<!-- BODY: replace this line with ## Why, ## What, ## Out of scope, ## Acceptance criteria -->",
        "",
        "## Dependencies",
        "",
        f"- Depends on: {deps}",
        f"- Blocks: {blocks}",
        "",
        "## References",
        "",
        source,
        f"- Epic context: {refs}",
        "- Backlog plan: [000-plan.md](000-plan.md)",
        "",
    ]
    return "\n".join(fm + body)
