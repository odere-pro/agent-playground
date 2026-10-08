# Backlog changes when PoC-4 closes

What the backlog issues change once this iteration closes. Applied with skill `planning-sync`: issue bodies by hand, order and dependencies in `docs/planning/tools/backlog.py`. No order, size, or dependency change is needed: `backlog.py` is not touched.

Evidence: `docs/planning/adr/004-events-through-a-broker-client.md`, `docs/contracts/contract-v3.md` (Known gaps), `deploy/compose/SECURITY.md` section 7, and the notes in this folder: `2026-10-01-dapr-vs-broker.md`, `2026-10-01-container-roles.md`, `2026-10-01-load-results.md`, `2026-10-01-hidden-state.md`, `2026-10-01-drills.md`.

Applied on 2026-10-01 as a "Status after PoC-4" bullet at the end of `## What` in each issue below. No acceptance box was ticked: the gate run that proves them goes in the PoC-4 README. ADR-004 was Proposed then; it was accepted on 2026-10-08 (see below). The items under "Not applied" need the owner.

## Applied

- **001 DEC-1** (exit criterion 10): the Dapr decision with its measurements and a link to ADR-004; the broker product stays open, and a NATS adapter must pass the same `EventPortContract`. Why: the PoC-4 criterion names this issue.
- **019 H-17** (exit criterion 10): what PoC-4 built (`EventPort`, the in-memory bus, the `kafka` and `dapr` adapters, the suite, result events), the ADR-004 link, that the Dapr criterion cannot pass as written, and the open gaps (a failed dead-letter publish ends the consumer task; `input` in result events on an open broker). Why: the PoC-4 criterion names this issue.
- **024 CH-3** (exit criterion 9): the native sidecar wins (0 failed in six rolling restarts after the drain fix; preStop still loses calls on `echo-typescript`); `--drain-delay-s 10` and the grace rule (50 = 10 + 30 + 10); the exec liveness probe on the agent card with its own `terminationGracePeriodSeconds: 10`; the read-only root settings; Valkey sized as rate × TTL × about 1.5 KB. Why: the chart carries the container roles and these values.
- **122 X-5** (exit criterion 11): the sidecar's measured memory (150 to 182 MiB), CPU (0.08 to 0.14 vCPU idle, 0.105 to 0.129 at 10 RPS, about 0.6 to 1.3 vCPU per 100 RPS at saturation), and hop (below the 1 ms resolution, not isolated), next to ADR-001's suggested figures. CPU at or above the top of 0.05–0.1 vCPU goes to the epic owner as a Revisit input; it is not more than twice the top, so the issue's suggested "far above" rule is not met. Why: ADR-001's Revisit rule names no issue, and its text does not invite measured figures, so they go to the issue that runs the Revisit check (ADR-001 itself is not edited).
- **010 H-12**: the S3 `ConfigPort`, the schema check, reload with the last good config kept, content-named `versions.config`; open: no ceilings on reloaded `spec.limits` and `ttl_s`, `/openapi.json` limits fixed at start. Why: PoC-4 built this issue's core.
- **018 H-18**: built (lease, renew, takeover, the fence at `0.8 * lease_s`, replay in any interface); open: per-caller scope, a per-caller quota, a `ttl_s` ceiling and retention, TLS to Valkey. Why: PoC-4 built this issue's core.
- **057 H-19**: the hidden-state scenario test and what it found (task stores pruned, two harmless counters, LangGraph and PydanticAI state rules), and the read-only root held on every engine. Why: it is this issue's kept-data check, run early.
- **017 H-4**: timeout and budget built on all four engines; the `http_429` code finding and the TypeScript xfail. Why: PoC-4 built two of this issue's stages.
- **020 X-8**: the PLAINTEXT Kafka gaps (no auth, open controller listener, auto-created topics, writable root) and the ADR-004 link. Why: SECURITY.md section 7 names this issue as owner.
- **022 H-6**: any caller can fill Valkey with new idempotency keys; the per-caller rate limit must cap them too. Why: SECURITY.md section 7 names this issue as owner.
- **026 CH-4**: the scale stack's secrets stay on the chassis; open: Kafka with no credential, one shared Valkey password with no TLS, open Compose egress, and the daprd ports if Dapr is ever kept. Why: SECURITY.md section 7 names this issue as owner.
- **Links**: ADR-004 from `docs/planning/poc/000-plan.md` (the outbound proxies paragraph and "Decisions for the epic owner"), from `docs/planning/poc/004-PoC-4-stateless-scalable.md` (Links, with contract v3 and the notes), and from `docs/planning/issues/000-plan.md` through `tools/plan-template.md` (after ADR-003's line, and in gap (c)). Why: skill `adr`, step 3.

## Applied at the close, 2026-10-08

- **122 X-5** (exit criterion 4): PoC-4 closed with criterion 4 flagged, so the throughput rerun on a Linux host or with pinned CPUs belongs to this issue. Why: the Docker Desktop VM cannot show growth for every engine.

## Applied after ADR-004 acceptance, 2026-10-08

The user accepted ADR-004 on 2026-10-08. No acceptance box was ticked.

- **ADR-004 status.** Proposed to Accepted, 2026-10-08, by the user.
- **019 H-17 body.** The `## What` bullet "The Dapr pub/sub component for the broker chosen in DEC-1" became "the broker client adapter for the broker chosen in DEC-1"; the criterion "The Dapr adapter and the in-memory bus pass the same `EventPort` contract suite" became "the broker client adapter and the in-memory bus ..."; "With Dapr, a publish call without the chassis's token is refused" was dropped. Why: Dapr fails two cases of that suite by design. The "Status after PoC-4" bullet and the Dapr-or-broker-client bullet now say accepted.
- **019 H-17 `## Reuse`.** In `docs/planning/tools/reuse_map.py`: "Use: Dapr pub/sub" became a broker client in the chassis; the Dapr "Scope change" bullet went; the CloudEvents SDK line went too, since contract v3 uses no `cloudevents` SDK. The Dapr "Watch" bullet became the broker client's costs (retries, dead-lettering, broker auth with 020 X-8). Why: `## Reuse` is generated, so the source is `reuse_map.py`. Regenerated with `make planning-sync`.
- **Reuse picks.** Replaced with the broker client: "Dapr for events" in `docs/planning/poc/010-reuse-analysis.md`, in the `EventPort` row of the real-adapter table in `docs/planning/poc/000-plan.md` ("Swappable and testable from day 0"), and in "Tech beyond Appendix H" in `tools/plan-template.md`. And in 001 DEC-1's "Reuse stack" bullet, the clause "Dapr for events (which makes Kafka the stable broker choice)": without Dapr, Kafka's stable Dapr component no longer favors Kafka. Why: these name Dapr as the pick. The "proposed" mentions of ADR-004 in these files now say accepted.

## Not applied: for the owner

- **060 H-23.** Add the bar ADR-004 item 8 sets: a NATS (or any other) adapter passes the whole `EventPortContract` against a real broker, with no xfail. Why: not named in this close's list, and its scope is the owner's.
- **024 CH-3 `## What`.** Turn "suggested: the workload is the native sidecar" and the shutdown-order bullet into the PoC-4 result, and add `--drain-delay-s` and the grace rule to the criterion "A rolling restart under load fails no request". Why: the container-roles note says no ADR is needed; the wording change is the owner's call.
- **ADR-001's Revisit rule.** The chassis CPU at 10 RPS (0.105 to 0.129 vCPU) is at or above the top of the suggested 0.05–0.1 vCPU. For the epic owner as a Revisit input; the hop is not isolated yet. ADR-001's cost table also says Dapr "is decided in PoC-4": an amendment line pointing to ADR-004 is the owner's.
- **051 H-13.** The OpenAPI limits in `describe_run` are fixed at start, so `/openapi.json` and the MCP tool description show the start values after a reload (contract v3, Known gaps; suggested there: accept for PoC-4). One line for the owner.
- **`deploy/compose/SECURITY.md` section 7** says result events carry the raw idempotency key in `data` and in the `idempotencykey` and `partitionkey` extensions; contract v3 ("Result events") says all three carry the key's sha256 hex. One of them is out of date; for `platform-security` to check against `server/results.py`. Resolved 2026-10-01: the code publishes the sha256 hex (`packages/chassis/tests/test_result_events.py`), and SECURITY.md section 7 now says so.
- **The vendored CloudEvents schema** must be diffed against upstream cloudevents/spec v1.0.2 before it counts as vendored (contract v3, Known gaps). For `chassis-architect`.
