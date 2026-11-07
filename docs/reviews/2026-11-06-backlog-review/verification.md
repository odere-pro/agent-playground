# Verification

The checks that close the 2026-11-06 backlog review, run on 2026-11-07. Plan: [2026-11-06-backlog-review.md](../../plans/2026-11-06-backlog-review.md), section "Verification".

## Results

| Check | Result |
| ----- | ------ |
| Every issue has one findings row | 127 of 127, no duplicates (all ten findings files) |
| Every finding is placed | 241 of 241 ids, each in `fix-plan.md` or `proposals.md` |
| `make planning-sync planning-check`, now with `--strict` | `133 issues: 1 done (5 days), 23 parked (52.5 days), 272 engineer-days open (W0–W11)`; `133 issues, 113 epic stories` / `OK` |
| `make harness-lint` | `harness-lint: ok` |
| Totals against `fix-plan.md` section 4 "after" | open W0–W11 272 days, done 5 (1 issue), parked 52.5 (23 issues), 133 rows: no difference |
| Phases A to F against `phase-map.md` | 86.5 days, the same as the phase map's totals |
| Ticked boxes | 137 of 903 at PR #88, up from 19 of 869 at the baseline |
| A sample of 10 ticked tests, re-run | 10 passed in 3.27s (below) |
| The pre-commit hook on each apply commit | #82 2510 passed; #88 6599 passed; PR-R 6606 passed; no failures |

## The sample

Ten ticked boxes, picked with a fixed seed (`random.seed(20261107)`) from the 174 that cite a `path::test_name` under `packages/`, `pocs/`, or `scripts/`:

```
scripts/check_offline.sh <the ten ids> -q -p no:cacheprovider -rA
PASSED packages/chassis/tests/test_mcp_interface.py::test_calling_the_tool_returns_the_native_response_envelope   (051 H-13)
PASSED packages/agentctl/tests/test_agentctl_cli.py::test_the_template_default_framework_matches_the_rules      (058 CH-8)
PASSED pocs/poc-04-stateless-scalable/tests/test_idempotency_replicas.py::test_the_cache_expires_after_ttl      (018 H-18)
PASSED packages/chassis/tests/test_marking_body.py::test_the_openai_body_carries_the_flags_complete_and_on_every_chunk (023 C-5)
PASSED packages/chassis/tests/test_schemas.py::test_events_from_a_typescript_workload_validate                   (007 H-1)
PASSED packages/chassis/tests/test_readiness.py::test_the_sidecar_probe_is_false_on_a_bad_status_or_a_dead_workload (011 H-2)
PASSED packages/chassis/tests/test_tool_endpoint.py::test_fake_profile_builds_the_fake_tools                     (054 H-16)
PASSED packages/chassis/tests/test_evaluator_gate.py::test_a_failing_answer_retries_once_then_falls_back         (017 H-4)
PASSED packages/chassis/tests/test_otel_telemetry.py::test_traceparent_continues_the_callers_trace               (014 H-7)
PASSED packages/chassis/tests/test_model_proxy.py::test_a_call_of_an_in_flight_run_gets_a_span_and_is_charged    (013 CH-2)
10 passed in 3.27s
```

## Differences from the plan

- **Status bullets.** The plan's check said "no `Status after PoC` bullet is left". Eight issues keep a new one that the fix plan asked for, because they stay `later` or parked and the bullet is the only place a PoC fact fits: 036, 064, 072, 073, 074, 075, 104, and 107. No stale bullet is left.
- **One PR for the ticks.** The plan had seven tick PRs. They ran as seven parallel workers on disjoint files and landed as one commit, PR #88, so the user signed once instead of seven times.
- **PR-P and the close** landed together, for the same reason.
- **PR-2 carried one test fix.** `test_redaction.py` timed a ReDoS guard on the wall clock, and a loaded machine failed it in the hook twice. It now times on CPU (PR #82).

## Left for owners

Not backlog edits; each has an owner.

- **A spec or code drift, for `chassis-architect`:** 012 H-3's admission box says the proxy refuses a call that names another route with 403 `model_route_denied`. The code forces the call onto the resolved route and counts `chassis.model_route_forced`; only LiteLLM answers 403. Filed as P-25 in [proposals.md](proposals.md).
- **006 G-6** still filters spend by `user`, but after 004 G-2 the metrics carry no `user` label. Reword when 006 is picked up (phase E).
- **096 P-2a's done-when box and 116 X-1b's smoke box** still name the simplifier, which is parked. Reword when either leaves `later`.
- **ADR-006 decision 1** has a sentence the 2026-11-06 amendment left stale (finding W5's note). For the `adr` skill.
- **A Kyverno install note** in 038 X-1a, from the W5 findings: ADR-005 picked ValidatingAdmissionPolicy, so check 038 when the first cloud is picked.
- **Open proposals:** P-3 to P-5, P-7 to P-15, and P-17 to P-34, in [proposals.md](proposals.md). Each names its owner agent. P-16 was done by PR #79 (rule 9 in `agent-lib`). P-26 to P-34 came from the #81 gap review, in PR #95.
- **New gaps from #81: done after the close.** PR #95 mapped every gap in the product plan's section "If phases A to F are all built: what is still missing" to the backlog: issues 134 CH-14 to 139 CH-19, new boxes on nine issues, proposals P-26 to P-34, and the phase G row in the product plan. PR #96 then brought the backlog in line with PRs #83 to #92.
