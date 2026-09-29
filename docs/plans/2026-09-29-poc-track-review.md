# Review of the PoC track and ADR-001

Status: done
Date: 2026-09-29
Scope: `docs/planning/poc/*`, `docs/planning/adr/001-chassis-delivery-model.md`, chassis issues CH-1 to CH-8 and the harness issues they touch.

## Verdict

The design is coherent and the discipline is right. ADR-001, the PoC track, and the CH issues tell one story; the two hard requirements are release gates; ports plus fakes plus one contract suite per port is the best decision in the plan. The concerns were holes in the security boundary, one unspecified mechanism the budget story rests on, and time boxes.

## Findings and what changed

1. **Hard requirement 1 had three holes in the sidecar lane.** The pod's service account token is projected into every container; the cloud metadata service is reachable unless egress denies it by name; the workload can call the chassis's public port on localhost. Added to ADR-001 HR1, CH-3 (`automountServiceAccountToken: false`), CH-4 (deny list, public port off localhost), and the PoC-5 hostile suites.
2. **HR1 contradicted ADR item 8.** A managed-runtime agent pointed at LiteLLM holds a key. Decided: a `remote` workload reaches the chassis's proxies over the network with a per-remote credential; a managed runtime that cannot holds its own scoped key as the one recorded relaxation. In ADR-001 item 8, CH-2, CH-6, PoC-5, PoC-6b.
3. **Outbound proxy calls were not correlated to the inbound request.** The proxy keys accounting on `traceparent`, propagated by the workload's HTTP client; calls without a trace id count against a per-replica budget. In ADR-001 item 6, CH-2, H-4, PoC-2 exit criteria, follow-up (h).
4. **`inprocess` reintroduced the dependency clash.** Redefined as A2A over an in-memory ASGI transport; workload authors test against the chassis as a separate process in the `fake` profile. One wire contract. In ADR-001 (HR2, items 2 and 4), PoC plan, PoC-1, PoC-2, PoC-9, H-1, H-14, CH-1, H-8, H-2, H-15, plan template.
5. **Day 0 was over-scoped.** PoC-1 keeps four ports; the others arrive with their first adapter.
6. **A TypeScript echo moved into PoC-2**, to prove the contract has nothing Python in it while it is still v0.
7. **N−1 schema compatibility.** The chassis accepts the current and previous event schema major; CH-7 tests it against a pinned old workload. In H-1 and CH-7.
8. **Pod lifecycle.** A liveness probe on the workload container; the workload as the Kubernetes native sidecar and the chassis as the main container, so the chassis drains first. In CH-3 and PoC-4, follow-up (g).
9. **Dapr decided in PoC-4**, with a broker client behind `EventPort` as the suggested default. In PoC-4, DEC-1, H-17, plan template gap (c).
10. **Presidio in the chassis broke the cost estimate.** Chassis redaction is regex only; named-entity redaction runs in LiteLLM and the OTel Collector. In H-6, ADR-001 assumptions, PoC-7.
11. **Time boxes.** PoC-2 two weeks, PoC-4 one week, PoC-5 three weeks, PoC-7 two to three weeks; PoC-6 split into 6a (sidecar lane, after PoC-3) and 6b (remote lane, after PoC-5); total about 13–15 weeks. The track is an incremental MVP build with go/no-go gates, and says so.
12. **Smaller.** A2A per-token overhead and time to first token measured in PoC-2 with a Revisit trigger in ADR-001; the Claude Agent SDK needs a scratch volume under a read-only root; a retry reruns the whole `handle` call.

## Open concern, marked in the issues

The evaluator gate adds a big-model judge call to every request until the encoder SLM lands, which roughly doubles p95 latency and eats the saving. Marked as an open concern in H-4, A-2, G-5, E-1, E-3, and PoC-7, with what to explore: a sample rate, gating only on low confidence, an asynchronous gate, the judge's cost in the request metrics, and the break-even shown with and without the judge.

## Withdrawn after discussion

- "Platform before proof": the platform is the goal and the SLM is the first workload that exercises it.
- "Strict statelessness will meet reality": the orchestrator owns memory and context; every other agent takes context as input through `context_ref`.
