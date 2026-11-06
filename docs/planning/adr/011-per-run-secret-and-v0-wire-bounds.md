# ADR-011: A per-run secret on the remote listener, and bounds on the v0 wire with no bump

- **Date:** 2026-11-06
- **Deciders:** Oleksandr (epic owner) and the delivery team
- **Format:** Michael Nygard's template: Status, Context, Decision, Consequences
- **Related:** [ADR-001](001-chassis-delivery-model.md) (hard requirement 1, one wire contract), [ADR-002](002-template-a2a-server-placement.md) (the template server's copies), [ADR-005](005-remote-lane-auth-and-trust-admission.md) (decision 2, which this extends), [ADR-006](006-agent-engines-default-supported-lanes.md) (the freeze), [ADR-009](009-feedback-langfuse-scores-event.md) (the feedback owner, closed by the same contract step), [PoC plan](../poc/000-plan.md), [PoC-5](../poc/005-PoC-5-sandboxed.md), [contract v8](../../contracts/contract-v8.md), [contract v5](../../contracts/contract-v5.md) part C, the core review findings R2-02 and R1-09 (`docs/reviews/2026-10-29-core-review/`)

## Status

Proposed, 2026-11-06. The user approved the core-review proposals R2-02 and R1-09, with the cap part of R4-27, as one contract step. The code is in the worktree on top of `0a2e4df` and is described in contract v8, parts C and D. It waits on the remote suites on kind (`POC05_KIND=1`) and the 6b remote lane on the Mac before it is Accepted.

## Context

ADR-005 decision 2 lets a remote spend tokens and call tools only inside a run. The listener checks two things:
- the remote's bearer token, one per remote, shared by all its runs;
- a `traceparent` that names a run in flight.

The trace id is not a secret. It travels in logs, spans, and baggage. So a remote that serves run A can send B's `traceparent` with the shared bearer. It can then spend B's token budget, or send a tool write under B's run key, so that B's real write is dropped as a duplicate (R2-02). `correlation.py` had named a chassis-minted per-run credential as the missing piece since PoC-2.

Separately, the v0 wire had no bounds (R1-09):
- No length or charset limit on `request_id`, `trace_id`, and `idempotency_key`.
- No `max_length` or `ge=0` on event fields.
- Control characters and values up to the 1 MiB body cap reached logs, span attributes, and state keys.
- A negative token count was accepted.
- A lone surrogate in an idempotency key crashed the store key (R4-27).

Contract v5 C.3 says a breaking event change bumps `schema_version`. It did not say whether a tightened bound is breaking.

Forces:

- A kagent-adk remote (plain mode, contract v5 part B) never receives `ctx`. It cannot be asked to send anything back.
- Every lane and every language already reads `ctx` in `handle`. `traceparent` reaches the workload that way and goes back as a header.
- Hard requirement 1: only the chassis holds credentials to internal services.
- A replica keeps nothing a retry needs (exit criterion 8).
- A bump to `"1"` makes every deployed `"0"`-only workload answer `a2a.unsupported_schema_version` (contract v5 C.5).

Options considered:

| # | Question | Option | Verdict |
| - | -------- | ------ | ------- |
| S1 | How the secret reaches the workload | A new optional `Context` field, `run_secret`, inside `chassis.ctx` | **Chosen.** The `traceparent` path. Compatible by contract v5 C.3. One rule in every language |
| S2 | | An A2A HTTP header only | Rejected. `handle` never sees headers, so every A2A server would copy it into `ctx` anyway, and a2a-sdk logs headers at DEBUG |
| S3 | | `tracestate`, or a field folded into `traceparent` | Rejected. Both are exported to telemetry backends |
| S4 | | A new A2A metadata key | Rejected. A mapping change in the frozen surface (contract v5 C.2 item 6) |
| K1 | Where the chassis keeps it | On the `RunRecord`, one replica, for the life of the run | **Chosen.** The remote calls back the replica that opened the run, as `RequireRun` already assumes |
| K2 | | An HMAC of the trace id under a per-process key | Rejected. A client-chosen trace id used twice would give the same secret twice |
| K3 | | `StatePort` | Rejected. Nothing outlives the run, and a retry is a new run |
| P1 | Plain mode | No secret (`None`). Traceparent alone, counted | **Chosen.** A plain remote cannot carry it. Decided by `spec.engine.protocol`, never by the caller |
| P2 | | Refuse plain mode on the remote listener | Rejected. It breaks the 6b plain lane |
| B1 | Bounds on v0 | Tighten in place, keep `"0"`, re-pin the freeze hashes | **Chosen** |
| B2 | | Bump to `"1"` with the bounds, keep `"0"` accepted | Rejected. A hostile producer would declare `"0"` to escape them, so the bounds must hold on `"0"` anyway. The bump would be a rename |
| B3 | | Truncate in the receiver, no schema change | Rejected. The template server's own `jsonschema` check would not catch a bad event before it leaves, and the schema would keep lying about what is accepted |

## Decision

**We choose S1, K1, P1, and B1.**

1. **A per-run secret.**
   - **Mint.** The chassis mints `secrets.token_urlsafe(32)` per run (`RUN_SECRET_BYTES`, suggested) in `context_for`. It puts it in `ctx.run_secret`, and the `RunRecord` keeps a copy. It is minted in every lane but plain mode.
   - **Send back.** The workload forwards it as the header `x-chassis-run-secret` on every model proxy and `/mcp` call, next to `traceparent`. It never logs it.
   - **Check.** The remote listener's `RequireRun` needs exactly one `traceparent` header (more is refused, never guessed). It reads exactly one such header of at most 128 bytes (`RUN_SECRET_MAX`, suggested). It compares it with the record of the run the `traceparent` names, using `hmac.compare_digest`.
   - **Refuse.** Missing or wrong is 403 `run_required`. The body bytes are the same for every reason, so a caller cannot tell "no such run" from "not your run". Counted as `chassis.remote.run_required{reason}`, with `traceparent_count`, `no_run`, `secret_missing`, or `secret_wrong` (suggested).
   - **Strip.** The header is removed before the route.
   - **Plain mode.** A plain-mode run has no secret and passes on the traceparent alone. A run whose record holds a secret needs it, in any mode. It is counted as `chassis.remote.run_secret_skipped{reason="lane_without_secret"}` (suggested).
   - **Credential class.** The secret is not a product or platform credential. It allows only what the run already allows, and only while the run is in flight. So hard requirement 1 holds.
2. **Bounds on the v0 wire, with no bump.** Tightening a bound so that it refuses only values no conforming producer emits is compatible under contract v5 C.3:
   - It needs no `schema_version` bump.
   - It re-pins the freeze hashes in the same change.
   - Every engine's mapping test, which parses each event it emits with `parse_event`, is its evidence.

   Anything else that narrows a field stays breaking. The first use is contract v8 C. The ids get a charset and a length (suggested: 128 characters for `request_id` and `trace_id`, 255 visible ASCII characters for `idempotency_key`). The event fields get bounds, with every value suggested.

## Consequences

Easier:

- A remote process that serves one run, and cannot read another run's secret, can no longer act under that run on the same chassis. A leaked secret is useless once its run ends.
- The trace id can stay what it is, a correlation id, not a credential.
- Unbounded ids and negative counts no longer reach logs, spans, the store, or the collector's sums.
- The rule for future tightening is written down, so the next bound is not a debate about a bump.

Harder:

- Every chassis-mode remote workload must send one more header. Workloads go first in a rollout. A chassis-mode remote that does not send it gets 403 on every proxy call. The seven built engines do it in contract v8. A new engine must too (the service template, 009 PoC-9).
- The secret isolates runs only between processes that share no memory and no `/proc`. A remote pod that serves several runs gives its one process every run's secret. echo-claude-agent puts the secret in the CLI's environment, which any process with the same uid can read from `/proc`. So a shell tool in one run can read another run's secret in the same pod. Full isolation needs one run per pod: core-review proposal R6-11, not in this decision.
- The secret is in `ctx`, so a workload that logs or traces `ctx` as a whole would leak it while the run is in flight. `repr` hides it on the chassis side. A workload must not log `ctx`. The a2a-sdk logs the JSON-RPC body, `ctx` included, at DEBUG, so `workload_a2a` pins its loggers to WARNING. A shell that runs `env` can echo the secret: the `remote` connector scrubs it from every event, like the bearer token.
- Plain mode keeps the old rule. A plain remote can still name another plain run's traceparent.
- A tightened bound can refuse an old producer that wrote an extreme value. The bounds are set far above any honest value to make that unlikely.
- The TypeScript template's validator does not know the new schema keywords. It keeps the old copy of `events.v0.json` until it learns them. The chassis enforces the bounds either way.

Costs across lanes:
- `inprocess` and `sidecar`: 32 random bytes per run, sent and never checked. The loopback proxy has no `RequireRun`.
- `remote`: one constant-time compare per proxy call, and a 43-character header on each call from the remote. No new hop, Secret, or controller.
- Every lane: a pydantic check of the bounds on each event.

Contracts touched: `Context` (one optional field), the proxy wire (one header), `RequireRun`, the envelope and event schemas (bounds), and the freeze test's hashes. `schema_version` stays `"0"`. Written in [contract v8](../../contracts/contract-v8.md), parts C and D.

## Revisit

Reopen decision 1 on any of these:
- A plain remote can echo a per-run header. Then plain mode needs the secret too.
- mTLS per remote arrives with no added controller (ADR-005 Revisit). Then the bearer and the run secret may fold into one identity per run.
- A remote pool serves several chassis replicas behind one Service, so that a call can land on a replica that did not open the run.
- R6-11 lands (one run per pod). Then the secret isolates every run, not only runs in separate processes.

Reopen decision 2 on either of these:
- An engine's mapping test fails a bound.
- A deployed producer is refused by a bound in production. Then raise the bound, or treat that one as breaking.
