# Proposals

Every finding of the 2026-11-06 backlog review that is a code conflict (rubric check 11) or needs a code or spec behavior change. The review makes none of these changes. The user decides; the owner agent advises and makes the change in its own PR after PR-R. A box a proposal touches stays open until the proposal is decided; `fix-plan.md` section 3 says which.

Each finding id is assigned here once, in bold. P-24 has no id: it carries two items the 055 CH-6 and 054 H-16 body edits send here.

| P | Topic | Findings | Owner |
| - | ----- | -------- | ----- |
| P-1 | An evaluator outage: fail open or fail closed | W2b-03, W4-01 | `eval-expert` |
| P-2 | A rate-limit store outage: fail open or fail closed | W2b-04 | `platform-security` |
| P-3 | The master key in the `local` Compose variant | W01-01 | `platform-security` |
| P-4 | `/metrics` with no token | W2b-09 | `platform-security` |
| P-5 | The agent tag on model calls | W2a-03 | `observability-expert` |
| P-6 | Drain on SIGTERM | W2a-04 | `chassis-architect` |
| P-7 | Cancel and streaming in `inprocess` | W2a-05 | `chassis-architect` |
| P-8 | Config and prompt through `ctx` | W2a-06 | `chassis-architect` |
| P-9 | A JSON-RPC error from the workload | W2a-18 | `chassis-architect` |
| P-10 | The core import rule | W2a-30 | `chassis-architect` |
| P-11 | The budget error code | W2b-12 | `chassis-architect` |
| P-12 | A failed dead-letter publish | W2b-13 | `chassis-architect` |
| P-13 | The public port's bind | W2b-26 | `platform-security` |
| P-14 | The judge's tokens and cost in `/metrics` | W4-04 | `observability-expert` |
| P-15 | `agentctl --trust` | W5-02 | `platform-security` |
| P-16 | The minimum chassis version rule in the specs | W5-04, W7-06 | `platform-security` |
| P-17 | One tool allow-list or two | W5-06 | `platform-security` |
| P-18 | A body that is not a CloudEvent | W5-09 | `chassis-architect` |
| P-19 | Publishing `chassis` for in-process tests | W5-10 | `chassis-architect` |
| P-20 | Swapping the evaluator adapter with no restart | W6-08 | `eval-expert` |
| P-21 | The `read_only` default on a tool | W89-10 | `platform-security` |
| P-22 | The cost-per-good-task target | W3-13 | `observability-expert` |
| P-23 | The gate set's size in the models spec | W6-24 | `eval-expert` |
| P-24 | Two PoC-6b review leftovers | (from body edits) | `platform-security` |

## Decided by the user, 2026-11-06

- **P-1:** fail open everywhere, as built. No code change. An alert on `gate_failed_open / gate_runs` goes into 117 X-3.
- **P-2:** fail open everywhere, as built. No code change. An alert on the store's availability goes into 117 X-3.
- **P-6:** option 1. The 011 H-2 box is rewritten to the contract; 011 H-2 retires.
- Not a proposal, decided the same day: the 023 C-5 client guide joins phase A, before 2026-12-02.

The other proposals, P-3 to P-5 and P-7 to P-24, stay open. Each one's owner agent advises; a pick that changes code is its own PR after PR-R.

## P-1: An evaluator outage: fail open or fail closed

- Findings: **W2b-03**, **W4-01**. Known before the review: B-1.
- Issues: 042 A-2, 017 H-4.
- Where: `packages/chassis/src/chassis/server/evaluator_gate.py:29` (the rule) and `:326-330` (the `except EvaluatorError` path); `packages/chassis/tests/test_evaluator_gate.py` asserts it.
- Conflict: An evaluator outage lets the attempt out unscored, counted as `chassis.gate_failed_open` (ADR-010 item 4). 042 A-2 box 5 wants the gate to fail closed. Phase B turns the judge on by default, so an outage then ships unscored answers in `cloud`.
- Options:
  1. Keep fail-open everywhere, and add an alert on `gate_failed_open / gate_runs` in 117 X-3.
  2. A setting, as for the rate limiter: `fail_closed` in the `cloud` profile (the outage counts as a failed attempt, so the run falls back) and `fail_open` in `local` and `fake`.
  3. Fail closed everywhere.
- suggested: option 2. It matches the `rate_limit_on_unavailable` pattern, keeps local runs working, and gives `cloud` the box 5 behavior. Amend ADR-010 item 4 and record the default in the DEC-1 pack.
- Owner: `eval-expert`.
- In the backlog: 017 H-4 gets one sentence: "An evaluator outage fails open as built (ADR-010 item 4); P-1 decides." 042 A-2 box 5 stays open until P-1 is decided.

## P-2: A rate-limit store outage: fail open or fail closed

- Findings: **W2b-04**. Known before the review: the plan's known item.
- Issues: 022 H-6.
- Where: `packages/chassis/src/chassis/server/config.py:215` (`rate_limit_on_unavailable` defaults to `fail_open`), `packages/chassis/src/chassis/server/rate_limit.py:33`; contract v7 C.2.
- Conflict: A store outage removes the rate limit, because the default lets every call through. 022 H-6 wants the limit to hold.
- Options:
  1. Keep `fail_open` as the default.
  2. Default per profile: `fail_closed` in `cloud` (503 `state_unavailable` on counted calls), `fail_open` in `local` and `fake`.
  3. `fail_closed` everywhere.
- suggested: option 2, with the same shape as P-1. 022 H-6 gets the box "with the `cloud` profile, a store outage refuses counted calls with 503 `state_unavailable`".
- Owner: `platform-security`.
- In the backlog: 022 H-6: add the box in the pick; it stays open until P-2 is decided.

## P-3: The master key in the `local` Compose variant

- Findings: **W01-01**.
- Issues: 002 G-1.
- Where: `deploy/compose/docker-compose.local.yaml:27` (`LITELLM_API_KEY`, which the PoC-1 key-debt note sets to the master key).
- Conflict: 002 G-1 box 5 says no service config holds the router's master key. The `local` Compose variant gives the chassis the master key, because that variant has no key minting.
- Options:
  1. The `local` variant mints a scoped virtual key on start, as `deploy/compose/poc08-litellm/stack.sh` does, and the master key stays in the router container.
  2. Point the `local` variant at the `poc08-litellm` stack.
  3. Keep it, and rewrite box 5 to exclude `local`.
- suggested: option 1. It closes the PoC-1 key debt with the mechanism PoC-8 already proved.
- Owner: `platform-security`.
- In the backlog: 002 G-1 box 5 stays open, verdict `conflict`, until P-3 lands.

## P-4: `/metrics` with no token

- Findings: **W2b-09**.
- Issues: 022 H-6.
- Where: `packages/chassis/tests/test_metrics.py:242` (`test_metrics_need_no_token_by_default_even_with_auth_on`).
- Conflict: 022 H-6 box 1 says every endpoint except `/health` and `/ready` needs a token. `/metrics` is open by default, even with auth on (PoC-8 Q1). The product plan lists it as a P2 gap.
- Options:
  1. `/metrics` needs a scope (`agent:read`) when auth is on.
  2. `/metrics` stays token-free, and the chart's NetworkPolicy lets only the scraper reach it.
  3. Keep it open, and rewrite box 1 to list `/metrics` as an exception.
- suggested: option 2. Prometheus scrapers rarely carry tokens; the chart already owns the pod's ingress. Record Q1 in the DEC-1 pack.
- Owner: `platform-security`.
- In the backlog: 022 H-6 box 1 splits: "OIDC bearer tokens on every run and read route" (ticks after P-4) and "caller API keys" (its own open box).

## P-5: The agent tag on model calls

- Findings: **W2a-03**.
- Issues: 012 H-3.
- Where: `packages/chassis/src/chassis/adapters/litellm/client.py:5` and `:207` (`x-litellm-tags: agent:<name>`, also in `metadata.tags`).
- Conflict: 012 H-3 box 6 wants request metadata with nothing that picks whose spend it is. The client sends an agent tag so per-agent cost shows in the router, and sends no request id.
- Options:
  1. The tags stay as a report label only, the key alone attributes spend, and the box says so.
  2. Drop the tags; spend is by key only.
- suggested: option 1, and send the request id in both cases. The key already pins the agent; the tag only labels the router's report.
- Owner: `observability-expert`.
- In the backlog: 012 H-3 box 6 stays open until P-5 is decided.

## P-6: Drain on SIGTERM

- Findings: **W2a-04**.
- Issues: 011 H-2.
- Where: `docs/contracts/contract-v3.md#shutdown`.
- Conflict: 011 H-2 box 7 says new requests are refused on SIGTERM, and that drain holds when both containers get SIGTERM at once. The contract serves new calls during drain (`/ready` 503, `Connection: close`) and makes the container order a deploy rule.
- Options:
  1. Rewrite the box to the contract: "`/ready` goes 503 first; in-flight runs finish; the workload stops after the chassis has drained". No code change.
  2. Change the chassis to refuse new calls on SIGTERM (a contract change).
- suggested: option 1. The contract is in force and the drain tests pass; 011 H-2 then retires.
- Owner: `chassis-architect`.
- In the backlog: 011 H-2 box 7: rewrite per the pick and tick; the retirement depends on it.

## P-7: Cancel and streaming in `inprocess`

- Findings: **W2a-05**. Known before the review: the plan's known item (`inprocess` batches events vs CH-1).
- Issues: 009 CH-1.
- Where: `packages/chassis/src/chassis/adapters/a2a/inprocess.py:10` ("no per-delta streaming and no mid-run cancel").
- Conflict: 009 CH-1 box 5 wants the workload to see a cancel in both lanes. `inprocess` has no mid-run cancel and batches events by design.
- Options:
  1. Scope box 5 to `sidecar` and `remote`; move the `inprocess` cancel and the batching to `## Out of scope`.
  2. Build cancel and per-delta streaming into `inprocess`.
- suggested: option 1. ADR-001 keeps `inprocess` for tests and local runs.
- Owner: `chassis-architect`.
- In the backlog: 009 CH-1 box 5: scope and tick per the pick.

## P-8: Config and prompt through `ctx`

- Findings: **W2a-06**.
- Issues: 010 H-12.
- Where: `packages/chassis/src/chassis/core/envelope.py:36` (`Context` has no config or prompt field, only `versions.prompt`).
- Conflict: 010 H-12 box 11 says the workload gets its config and system prompt through `ctx`. The template workload holds its own prompt.
- Options:
  1. Drop the `ctx` half: the workload owns its prompt, and the chassis reports its version.
  2. Add config and prompt fields to `Context` (a contract change).
- suggested: option 1. Keep the no-credential half open in phase A; it ticks when the sidecar kind suite runs in CI (`pocs/poc-05-sandboxed/tests/test_poc05_kind_hardreq1.py::test_minio_refuses_the_workload_and_any_unsigned_call`).
- Owner: `chassis-architect`.
- In the backlog: 010 H-12 box 11: rewrite per the pick.

## P-9: A JSON-RPC error from the workload

- Findings: **W2a-18**.
- Issues: 009 CH-1.
- Where: `packages/chassis/src/chassis/adapters/a2a/connector.py:263` (catches `httpx.TransportError` and `A2AClientError` only); `docs/contracts/contract-v1.md#where-the-code-differs-from-the-decisions-text`.
- Conflict: A JSON-RPC error answer from the workload surfaces as `engine_error`. Only the remote plain mode maps it to an `a2a.*` code.
- Options:
  1. Map JSON-RPC error codes to `a2a.*` codes in the connector, as the remote plain mode does, with a stronger cancel case.
  2. Keep it, and document `engine_error` as the code.
- suggested: option 1.
- Owner: `chassis-architect`.
- In the backlog: 009 CH-1: add the open box "a JSON-RPC error answer from the workload surfaces as an `a2a.*` code"; drop the fixed `a2a.unsupported_state` item.

## P-10: The core import rule

- Findings: **W2a-30**.
- Issues: 007 H-1.
- Where: `pyproject.toml` (the `chassis.core` import-linter contract).
- Conflict: The core import rule forbids `httpx`, `a2a`, `valkey`, `aiokafka`, and `minio`, but not `socket`, `urllib`, `requests`, or `aiohttp`. Core imports none of them today.
- Options:
  1. Add the four modules to the core contract's `forbidden_modules`.
  2. Leave it.
- suggested: option 1. A one-line guard; `make lint` enforces it.
- Owner: `chassis-architect`.
- In the backlog: 007 H-1: the tick on the import rule stands.

## P-11: The budget error code

- Findings: **W2b-12**.
- Issues: 017 H-4.
- Where: `packages/workloads/echo-python/src/echo_python/handle.py` (the 429 path, finding cites `:96`) and `echo_langgraph/mapping.py:121`.
- Conflict: A spent budget reaches the client as `http_429`, with `budget_exhausted` only in the message.
- Options:
  1. The template A2A server maps the proxy's `budget_exhausted` to its own error code.
  2. The chassis maps a 429 `budget_exhausted` from its own proxy to the code, whatever the workload returns.
- suggested: option 2. The proxy is the chassis's own, so the chassis knows the cause; every framework gets it with no template change.
- Owner: `chassis-architect`.
- In the backlog: 017 H-4: under `## Out of scope`, "the budget error code: P-11".

## P-12: A failed dead-letter publish

- Findings: **W2b-13**.
- Issues: 053 H-22.
- Where: `packages/chassis/src/chassis/adapters/kafka/events.py:331-339` (`_dead_letter`); the finding cites the raise at `:302` and `:319`.
- Conflict: A failed dead-letter publish raises out of `_deliver` and `_consume`, which ends the subscription's consumer task. Contract v3 lists the dead-letter topic as the safe end.
- Options:
  1. Catch it, count it, retry the dead-letter publish with backoff, and keep the consumer alive.
  2. Let the process crash so the pod restarts.
- suggested: option 1, with a counter that 117 X-3 can alert on.
- Owner: `chassis-architect`.
- In the backlog: 053 H-22: add the box "a failed dead-letter publish is counted and retried, and the consumer keeps running".

## P-13: The public port's bind

- Findings: **W2b-26**.
- Issues: 026 CH-4.
- Where: `packages/chassis/src/chassis/server/cli.py` (`chassis serve --host`; PoC-5 B6).
- Conflict: Nothing in the CLI holds the pod-IP bind of the public port; only the manifests and the chart's argument allow-list do.
- Options:
  1. `serve` refuses a loopback host for the public port in the `sidecar` lane.
  2. Leave it to the chart.
- suggested: option 1.
- Owner: `platform-security`.
- In the backlog: 026 CH-4: under `## Out of scope`, "the CLI bind rule: P-13".

## P-14: The judge's tokens and cost in `/metrics`

- Findings: **W4-04**.
- Issues: 042 A-2.
- Where: `packages/chassis/src/chassis/server/model_admission.py:341` (`count_usage` runs only on the model proxy path); `packages/chassis/src/chassis/adapters/evaluators.py`.
- Conflict: The judge calls `ModelPort` directly, so its tokens and cost never reach `chassis_model_tokens_total` or `chassis_model_cost_microusd_total`. The metrics spec's cost per good task counts every route and the judge (`docs/specs/metrics.md:32`).
- Options:
  1. Count the judge's usage in the same families under the judge route.
  2. A named judge family.
- suggested: option 1. One family keeps the dashboards and the cost per good task simple.
- Owner: `observability-expert`.
- In the backlog: 042 A-2: add the box "the judge's tokens and cost count under the judge route, and a scrape test shows it".

## P-15: `agentctl --trust`

- Findings: **W5-02**.
- Issues: 059 H-15.
- Where: `packages/agentctl/src/agentctl/rules.py:89` (`trust_of(framework)`).
- Conflict: 059 H-15 boxes 6 and 7 want `--trust trusted|untrusted`. `agentctl` has no such flag: trust follows the framework.
- Options:
  1. Rewrite boxes 6 and 7 to the framework rule: "an untrusted framework renders a `remote` chart"; "an untrusted framework with `--connector sidecar` is refused" (ticks today).
  2. Add `--trust` for a trusted framework run as untrusted.
- suggested: option 1 now; option 2 only when someone asks for it.
- Owner: `platform-security`.
- In the backlog: 059 H-15 boxes 6 and 7: rewrite per the pick; box 6 stays open until the generated repo has a chart.

## P-16: The minimum chassis version rule in the specs

- Findings: **W5-04**, **W7-06**.
- Issues: 056 CH-7, 026 CH-4, 077 C-1.
- Where: `docs/specs/architecture.md:93` and `docs/specs/security.md:17`.
- Conflict: Both specs say a minimum chassis version rule lives in `deploy/kind/poc05/admission/`. No such rule exists.
- Options:
  1. The specs say "a minimum chassis version (not built; 026 CH-4)", and 026 CH-4 ships the rule in the chart in phase A.
  2. Build the rule in `deploy/kind/poc05/admission/` now.
- suggested: option 1. It is a spec edit, made in the product plan's files, not under `docs/planning`.
- Owner: `platform-security`.
- In the backlog: 056 CH-7 box 4 moves to 026 CH-4; 077 C-1's box on the rule stays open.

## P-17: One tool allow-list or two

- Findings: **W5-06**.
- Issues: 054 H-16.
- Where: `packages/chassis/src/chassis/adapters/mcp/gateway.py:4-5` ("the gateway's per-key allow-list is the hard limit ... adds no allow-list of its own").
- Conflict: 054 H-16 `## What` and box 4 want a chassis config allow-list (`tools` with `name` and `mode`), with write tools refused unless `mode: write`.
- Options:
  1. Rewrite `## What` and box 4 to the built rule: the gateway allow-list is the only one; a tool with no `readOnlyHint` is a write tool and needs an `idempotency_key`.
  2. Build the config allow-list in the chassis.
- suggested: option 1. One allow-list, held where the credential is.
- Owner: `platform-security`.
- In the backlog: 054 H-16 box 4: rewrite per the pick; box 2's "logged" half stays open either way.

## P-18: A body that is not a CloudEvent

- Findings: **W5-09**.
- Issues: 053 H-22.
- Where: the Kafka and Dapr event adapters (`packages/chassis/tests` cases on a body that is not a CloudEvent).
- Conflict: 053 H-22 box 4 wants a schema-invalid event in the dead-letter topic with no retries. Both adapters drop it.
- Options:
  1. Dead-letter it at once, with the reason.
  2. Drop it and count it.
- suggested: option 1. A body nobody can parse is the case a dead-letter topic is for.
- Owner: `chassis-architect`.
- In the backlog: 053 H-22 box 4 stays open until P-18 lands.

## P-19: Publishing `chassis` for in-process tests

- Findings: **W5-10**.
- Issues: 062 H-11.
- Where: ADR-002 and `docs/specs/architecture.md` (a workload never imports `chassis`).
- Conflict: 062 H-11 box 4 and its fourth `## What` bullet publish the `chassis` package so a service's tests run `handle` in `inprocess`.
- Options:
  1. Rewrite box 4: "a service's tests run the stock chassis in the `fake` profile through `agent-testkit`, offline, with no keys, and never import `chassis`", citing ADR-002, and tick it.
  2. Publish `chassis` for in-process tests (reopens ADR-002).
- suggested: option 1. Box 3 becomes "the chassis image and the platform packages carry one version from one CI release".
- Owner: `chassis-architect`.
- In the backlog: 062 H-11 boxes 3 and 4: rewrite per the pick.

## P-20: Swapping the evaluator adapter with no restart

- Findings: **W6-08**.
- Issues: 075 E-3.
- Where: `spec.adapters` is restart-only (the reload tests in `packages/chassis/tests`).
- Conflict: 075 E-3 box 6 says a rollback to the big-model judge is one config change with no restart.
- Options:
  1. Make `spec.adapters.evaluator` reloadable.
  2. Reword the box to "one config change and a rolling restart".
- suggested: option 2. 075 is parked, and a judge-model swap reloads through the route map (012 H-3) with no restart.
- Owner: `eval-expert`.
- In the backlog: 075 E-3 box 6: stays open, verdict `conflict`.

## P-21: The `read_only` default on a tool

- Findings: **W89-10**.
- Issues: 095 P-4.
- Where: `packages/chassis/src/chassis/ports/tool.py:31` (`read_only: bool = True`).
- Conflict: An untagged tool lists as read-only, while the gateway adapter treats a missing hint as a write tool. 095 P-4 box 6 needs one rule.
- Options:
  1. Make `read_only` required, with no default.
  2. Default it to `False`.
  3. Keep it and add a lint test.
- suggested: option 1. Every tool definition then says what it is.
- Owner: `platform-security`.
- In the backlog: 095 P-4 box 6 stays open; the row gains `conflict`.

## P-22: The cost-per-good-task target

- Findings: **W3-13**.
- Where: `docs/specs/metrics.md:32` ("Cost per good task" target: the G-5 break-even).
- Conflict: The target points at 031 G-5, which is parked.
- Options:
  1. "Set from the first week of real numbers (phase E)", with the judge's cost of about $5 per million requests as an input.
- suggested: that text. A spec edit, outside `docs/planning`.
- Owner: `observability-expert`.

## P-23: The gate set's size in the models spec

- Findings: **W6-24**.
- Where: `docs/specs/models.md`, the judge section, at `70244ad`.
- Conflict: It says the gate set is `golden/cases-gate.yaml`, 30 cases. The gate set is 50: `cases.yaml` (20) plus `cases-gate.yaml` (30). The phrase is not on this checkout's copy; re-check on `main` before editing.
- Options:
  1. "50 cases (the 20 trial cases plus 30)".
- suggested: that text. A spec edit, outside `docs/planning`.
- Owner: `eval-expert`.

## P-24: Two PoC-6b review leftovers

- Findings: none (from the 055 CH-6 and 054 H-16 body edits).
- Issues: 055 CH-6, 054 H-16.
- Where: `packages/chassis/src/chassis/adapters/a2a/remote.py:82` (also `sidecar.py:65`, `loopback.py:24`): `except ValueError`; the gateway client's `follow_redirects`.
- Conflict: Two PoC-6b review items with no home: an `except ValueError` that is too wide (LOW), and `follow_redirects` on the tool path. They come from the 055 CH-6 and 054 H-16 body edits.
- Options:
  1. Narrow the `except` to the parse call, and set `follow_redirects=False` with a test, as `packages/code-runner` does.
  2. Leave both.
- suggested: option 1.
- Owner: `platform-security`.
