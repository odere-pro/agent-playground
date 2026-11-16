# Wave 4 (live Compose, no provider keys) and three design leftovers

Status: in progress

Plan of 2026-11-16, from `main` `1ca0cf3` (261 of 344 boxes, 16 of 44 issues). HANDOVER.md "Wave 4" is the source. 4a to 4c need the user's provider keys and are out of scope.

## Pieces

One piece per worktree, one PR per piece. "Live" means the piece ends with one run on a fresh stack under the infra lock, logged, and a dated note.

| # | Piece | Issue and box | Stack | Offline part | Depends on |
| - | ----- | ------------- | ----- | ------------ | ---------- |
| D1 | Sidecar-lane DNS egress gap: options and a recommendation | 026 CH-4 egress box (H20), note only | none | a note in `docs/reviews/` | nothing |
| D2 | Tag-to-digest map in `agent-lib`: a backlog story and a one-line ADR-005 amendment draft | 056 CH-7 follow-up | none | `backlog.py`, a new issue file, planning-sync | nothing |
| D3 | Does an emptied scratch `/tmp` meet "no writable volumes"? | 057 H-19 box 3, note only | none | a note in `docs/reviews/` | nothing |
| P4 | Rate limit across two chassis on one Valkey | 022 H-6 L57 (4e) | PoC-8 | a `network` stack test on `chassis` and `chassis-stages` | nothing |
| P5 | Burn-rate error alert to an Alertmanager receiver; p95 latency alert | 117 X-3 L42, L43 (4f) | PoC-8 | a new rules file `deploy/prometheus/rules/slo.yaml` (W3-9 keeps `chassis.yaml`), Alertmanager and a webhook receiver in the PoC-8 Compose file, fault scripts | nothing |
| P6 | OTel SDK CPU and latency cost; chassis memory with redaction on | 014 H-7 L61; 022 H-6 L62 (4d) | PoC-8 with the Collector | a measurement script: SDK off, SDK on at `ALWAYS_ON` with `tail_sampling` | P5 merged if it shares the Compose file |
| P8 | The Langfuse dataset stack test | 131 CH-12 L32 (4g, Langfuse half) | PoC-7 Langfuse | a `network` test on `eval_kit.langfuse_datasets` | nothing |
| P9 | `demo.sh --live` calls `/v1/messages` on the LangGraph default | 059 H-15 L52 (4i) | the PoC-9 demo Compose | `demo.sh` change and its static test | nothing |
| P10 | NATS JetStream `EventPort` adapter; a publish test against a real broker started by the test | 019 H-17 L53 (4j part 1) | one NATS container | adapter, contract binding, `spec.adapters.events: nats` | nothing |
| P11 | NATS in the local Compose profile: result events land; streams from config, auto-create off; restart and retention; ordering by key; consumer lag and its alert; `traceparent` and `modelroute` on a real event | 020 X-8 box 1 recheck, L47, L48, L49, L50; 019 H-17 L57 (4j part 2) | local Compose with NATS | Compose service, stream config, exporter, tests | P10 |
| P12 | LangSmith stack tests | 129 CH-10 L32, L33 (4g) | PoC-7 with the LangSmith overlay | tests | wave 3's W3-6 (129 L30) merged; the key-variable question (129 L33 vs 130) settled |
| P13 | Every dashboard panel has data, Compose half | 046 A-5 L35 (4h) | PoC-8 | rerun `test_every_panel_query_has_data` | P6; wave 3's W3-5 and W3-11 merged |

## Order

1. Now, offline, in parallel: D1, D2, D3, P4, P5, P8, P9, P10. Each builds its tests and scripts and stops before Docker.
2. When the kind orchestrator releases the infra lock: one stack at a time, each fresh. PoC-8 (P4, then P5, then P6), PoC-7 (P8), the demo (P9), NATS (P10, then P11).
3. P12 and P13 when wave 3's pieces merge.

## Left open by design

- 014 H-7 L55 (a model call in Langfuse with its cost): the user decided on 2026-11-16 that it needs a real provider. It waits on the keys, with 4a to 4c.
- 020 X-8's boxes are Kafka-shaped. They are read in NATS terms (decided 2026-11-16): a subject per key hash for ordering, account permissions for ACLs, TLS only with no plain listener. P11 rewords the boxes through planning-sync.
- 020 X-8 L46 (install on Kubernetes), L51 (ACLs per service), L52 (TLS only): the kind half. It needs `deploy/kind`, which the kind orchestrator owns.
- 046 A-5 L35 also names Kubernetes. P13 proves the Compose half only; the box stays open.
- 022 H-6 L60 (names masked), L63 (Prompt Guard 2 on the Mac): not this wave.

## Machine rules

Infra lock `/tmp/agent-playground-infra.lock` before any stack, stack down before release. Gate lock only for a commit whose hook runs non-PoC package tests. CI is the full gate. Subagents run targeted tests only.
