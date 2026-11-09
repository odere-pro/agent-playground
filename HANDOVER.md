# Handover

Checkpoint of 2026-11-09 (night), at `main` `d0152bb`. Read this first in the next session, then replace it at the next checkpoint.

## Where things stand

The PoC track is over. The product is the agent container: one chassis image in front of every agent.

- **What it is:** [docs/specs/README.md](docs/specs/README.md). One fact in one place. The wire contract in force is [docs/specs/contract.md](docs/specs/contract.md).
- **What is built next, and in what order:** [the product plan](docs/plans/2026-11-06-agent-container-product.md), phases A to G. Its new section "The issues in each phase" names the issues per phase (PR #103).
- **The backlog follows the plan:** [docs/planning/issues/000-plan.md](docs/planning/issues/000-plan.md).
- **Open PoC items, by who can do them:** [docs/planning/poc/011-what-is-left.md](docs/planning/poc/011-what-is-left.md).

Landed or in flight on 2026-11-08 and 2026-11-09:

| What | PR | State when this was written |
| ---- | -- | --------------------------- |
| `remote-lane` green again: `agent-echo-events`' allowed control is Kafka, not LiteLLM | #100 | Merged (another session). Run 42503295381 on `f49d542`: `207 passed, 3 skipped, 2 xfailed` |
| Step 0: the plan and the specs in line with the backlog; ADR-012 (drain order, Proposed) | #103 | Merged |
| Step 1: 134 CH-14, the `cloud` security defaults; 134 retires to `WD` | #104 | Merged. Kind proof: `remote-lane` 42505718055 on the PR, `208 passed, 3 skipped, 2 xfailed`; green on `main` at `36441d9` |
| PoC-5 criterion 3's flag lifted: MinIO through the chassis, run 42503295381 | #102 | Merged (another session) |
| The first version of this file | #105 | Merged |
| Kind ClusterIP pins moved into the Service CIDR's static band (`10.96.0.91` to `.95`); a random Service took `10.96.85.91` in run 42505945335 | #106 | Merged |
| Step 2: the drain order. The user picked P-29 option 1; ADR-012 accepted. The chart makes the chassis the main container and the workload the native sidecar; a ready marker gates the chassis's startup probe; the render refuses a short grace | #107 | Merged (`9fc9a28`). `platform-security` accepted after one retry |
| ADR-012 items 4, 5, 7 on kind: the rolling-restart drill, the H14 check, the probe grace; `deploy/kind/chart/` and `make kind-chart` | #110 | Merged (`7eb1697`). On #107's head `2f4fbe8`: the chart as built failed 515 of 8587 and 464 of 8365 requests; after the swap 0 of 6312 and 0 of 7736; H14 4 of 4. 024 CH-3's rolling-restart box is ticked. The evidence is the dated PoC-9 note; no CI job runs kind since #108 |
| This file, refreshed | #109 | Merged |
| Step 3: 136 CH-16, the model proxy under failure | #117 | Merged |
| Step 3: 135 CH-15, backpressure: `spec.limits.max_in_flight`, 503 `overloaded`, the gauge `chassis_runs_in_flight`, `hpa.runsInFlight` | #119 | Merged (`1608138`). Its two Compose stack drills passed on CI in #125 |
| The #107 LOW: the ready marker names its writer's pid and start time, and the chart's startup probe (`python -m chassis.ready_marker`) passes only while that process is live | #115 | Merged (`09dfed0`). `platform-security` accepted |
| CI: a finished PoC is checked locally (`make test-pocs-done`); the kind, load, and agent-scan workflows are `workflow_dispatch` only; the remote lane's HR2 test moved into `packages/chassis/tests` | #108 | Merged (another session) |
| CI: one job per suite; `check` and `lint-extra` run on PRs and `main` pushes only, so a branch with no PR gets no CI | #111 | Merged (another session) |
| `make progress`: the product plan per phase; `make planning-check` fails when the plan's phase table drifts from the backlog | #112 | Merged (another session) |
| Stale dependencies off the critical path (009 CH-1 no longer waits on 008 H-14; 056 CH-7 no longer on 038 X-1a or 051 H-13); 056 CH-7 to phase G; warn-first is a box in 026 CH-4; `make quick` picks tests from the changed files and runs no PoC test | #113 | Merged (another session) |
| This file, refreshed again | #114 | Merged |
| Step 1, X-5: the PoC-4 load matrix for every engine on three 4-vCPU Linux runners (`poc06-load.yml` takes `engines` and `only` inputs); the note and the p95 per role in `run_matrix` | #116 | Merged (`74d7f22`). No X-5 box ticked by it: every engine grows 1 to 2 pairs, none fits 4 pairs on 4 vCPUs ([the note](pocs/poc-06a-bake-off-sidecar-lane/notes/2026-11-08-x5-linux-runner-load.md)) |
| Step 1, warn-first: rule 9 in its own policy `agent-chassis-version` with its own binding, kind set and chart; `admission.chassisVersionAction` Deny or Warn | #118 | Merged (`8b0f0f0`). `platform-security` accepted. Kind proof: `remote-lane` 42531478929 |
| Step 1, X-5 box 2: the sidecar at p95 (the `rate` rerun on `main`, run 42625888817) and the hop from a chassis-side timer (`chassis_engine_run_duration_seconds{lane}`, run 42627333030); the connector span carries `error.code` | #120 | Merged (`d13b3d5`). `observability-expert` accepted |
| `make quick` fails when a selected test fails: since #113 the recipe's trailing `echo` had hidden a failing pytest run from the pre-commit hook | #121 | Merged (`b98d2e7`) |
| This file, refreshed | #122 | Merged (`382aa4d`) |
| 128 CH-9: ADR-014 (a LangGraph agent keeps no state between runs; when it must, behind `StatePort`, keyed by `session.id`) and ADR-015 (an `interrupt()` ends the run with `error interrupt_unsupported`), proposed then accepted by the user | #123, #124 | Merged (`4e3254d`, `e6133ec`). 128's first two boxes ticked |
| The CH-16 Compose drills, first run on CI. The fallback route passed (run 42634113481). The budget alert did not fire: the chassis created the counter on a route's first refusal, so `increase()` saw no rise. Fixed at the source: every declared route renders at 0 on each scrape. The alert then fired 69 s after the refusal (run 42701220591) | #125 | Merged (`e23b192`). `observability-expert` rejected a PromQL-only fix and accepted this one; reviewer accepted with one LOW. [The drills note](pocs/poc-08-cross-cutting-build/notes/2026-11-09-ch16-compose-drills.md) |
| Phase C piece 1: `langchain-mcp-adapters` cannot run on `mcp` 2 yet (0.3.2 pins `mcp<2`), so a strict-xfail test and the weekly `adapter-watch.yml` fail loudly when the pairing is ready; 058 CH-8's adapter box ticked | #126 | Merged (`d0152bb`), from the phase C session |
| This file, refreshed | this PR | Open |

The user decided on 2026-11-08, each option 1: **P-29** (the drain order, ADR-012), **P-4** (`/metrics` needs `agent:read` in `cloud`), **P-26** (`fail_closed` on a rate-limit store outage in `cloud` only), **P-27** (`user.id` in baggage is `sha256[:32]` of the subject, in every lane). They are recorded in [the backlog review proposals](docs/reviews/2026-11-06-backlog-review/proposals.md), ADR-007, ADR-008, and 022 H-6 (in #104).

## In flight when this was written

- **Nothing from step 2.** #107 and #110 are merged. The kind evidence for the drain order is [the PoC-9 note](pocs/poc-09-agent-mvp-template/notes/2026-11-08-kind-drain-order.md); since #108 no CI job deploys the chart on kind, so a chart change needs `make kind-chart` by hand.
- **CI:** `check` green on every merge from #114 to #125; on `d0152bb` (#126) it was still running when this was written.
- **Phase C, pieces 2 and 3, in the phase C session** (branch `claude/vigilant-albattani-bbf78e`): the trace-join test (LangGraph's graph steps under the run span) and the `create_agent` variant in the template. They close 128's last box (ADR-006's two Revisit triggers) and the rest of step 5. No PR open when this was written.
- **#113 settled what #112's first `make progress` report raised:** the two stale edges are dropped, 056 CH-7 (the rings) sits in phase G, and warn-first for a new minimum chassis version is a box in 026 CH-4. Phase A now waits on no `later` issue through CH-7.
- **#103's open choices** for the user to check: the phase for issues 134 to 139 (the phase map predates them), and the plan keeps its own week sizes with a note on the gap to the map's days.
- **The two sessions of 2026-11-08 night are done:** phase A (#116, #118, #120) and the model proxy (#117, #119).

## Next steps, in order

Each step names its issue. Read the issue's acceptance criteria before you start; they are the done-when.

1. **Phase A, what is left after #116, #118, and #120.** Warn-first for a new minimum chassis version is done (the 026 CH-4 box). [122 X-5](docs/planning/issues/122-X-5-load-tests.md) ran on Linux runners; box 2 (the sidecar at p95, the hop) is ticked by #120, and the 4-pair box stays open on the host: 4 pairs need about 12 cores with pinned CPUs (the owner-list item, 011 section 3; a GitHub larger runner would do, this account has none). The 023 C-5 client guide is due before 2026-12-02 (AI Act Art. 50(2)). The T10 probe workload is the user's.
2. **The drain order (P-29): done.** #107 (the chart) and #110 (the kind drill, H14, item 7) are merged, and 024 CH-3's rolling-restart box is ticked with the note. Both leftovers are done: the PoC-4 container-roles note points at the drill note (this PR), and the ready-marker follow-up is #115.
3. **The model proxy under failure, and backpressure (phase G).** Done on 2026-11-08 and 2026-11-09: [136 CH-16](docs/planning/issues/136-CH-16-model-proxy-failures.md) ([PR #117](https://github.com/odere-pro/agent-playground/pull/117): 429 `service_budget_exhausted` with a counter and the first alert rule, `timeout_s` on the model port, 403 `run_ended`, `num_retries` and `fallbacks` per route in every LiteLLM config, no chassis retry; [ADR-013](docs/planning/adr/013-model-call-retries-belong-to-the-router.md), [contract v9](docs/contracts/contract-v9.md)) and [135 CH-15](docs/planning/issues/135-CH-15-backpressure.md) ([PR #119](https://github.com/odere-pro/agent-playground/pull/119): `spec.limits.max_in_flight` 32, 503 `overloaded` with `Retry-After: 1` on every interface, the gauge `chassis_runs_in_flight`, `hpa.runsInFlight` in the chart; contract v9 D). Both issues retired to `WD`. Their two Compose stack drills passed on CI on 2026-11-09 (#125, [the note](pocs/poc-08-cross-cutting-build/notes/2026-11-09-ch16-compose-drills.md)); the first run found and fixed a budget alert that could never fire on a route's first refusal. `chassis-architect` corrected the P-30 reading: contract versions are recorded in `docs/contracts/`; P-30 is the event schema versions on the agent card. Found in passing and fixed in #121: `make quick` exited 0 when its pytest step failed.
4. **Make the judge safe to turn on (phase B).** [137 CH-17](docs/planning/issues/137-CH-17-judge-calibration.md), then [138 CH-18](docs/planning/issues/138-CH-18-judge-per-agent.md). Until both pass, the gate reports (`async`) and never blocks. The judge numbers need the Mac run.
5. **LangChain first, the rest (phase C).** [128 CH-9](docs/planning/issues/128-CH-9-langgraph-checkpointer-interrupts.md): ADR-014 and ADR-015 are accepted (#124), and the adapter watch is in (#126; the swap waits on an adapter release for `mcp` 2). Left, in the phase C session: the trace-join test and the `create_agent` variant. ADR-006's trace-join Revisit trigger stays open until the test lands.
6. **LangSmith (phase D).** Needs the user's decision on the primary trace view first. 129 CH-10, 130 CH-11, 131 CH-12.
7. **Measure and show (phase E).** Needs the north star decision. 117 X-3, 046 A-5, 132 CH-13.
8. **Template GA (phase F), last.** 059 H-15, 025 H-10, 139 CH-19.

Follow-ups, not yet issues. From #107: the ready marker is stamped with the writer's pid and start time, and the chart's probe checks it (`chassis.ready_marker`; the LOW from `platform-security`, done in #115). From CH-16: `packages/contract-suites` `TestLiteLLMModel::test_a_call_ends_at_its_timeout` failed once on a cold LiteLLM import (30 s, "DID NOT RAISE ModelError") and passed on every warm rerun; the per-call timeout should not depend on import time (2026-11-09, this checkout). From #125: the error-rate and fallback-rate alerts are blind on a quiet agent the same way the budget alert was, since `chassis_run_errors_total`, `chassis_gate_fallbacks_total`, `chassis_gate_retries_total`, and `chassis_tool_calls_total` are created on their first count (listed in 117 X-3, with the missing `chassis_build_info`); and a route the workload names that `spec.model` does not declare is still not seeded at 0 (the reviewer's LOW, in the rule comment). From CH-14: a keyed `user.id` (HMAC with a chassis-only key; the unkeyed hash lets a reader test guessed subjects and link a user across agents), and P-27 option 2 (no `user.id` at all to an `untrusted` remote).

Steps 1 and 3 touch different files and can run as parallel sessions, one worktree each. Step 3 touches the model proxy files CH-14 changed; #104 is merged, so start from `main`.

## Only the user can do these

- **The Mac run:** `make poc06-mac`. It gives the hosted big-model numbers (closes PoC-6a and 6b) and the judge's cost, latency, and agreement (unblocks step 4).
- **Decisions:** the north star (blocks phase E); the primary trace view per environment, LangSmith or Langfuse (blocks phase D). The other open product decisions are [001 DEC-1](docs/planning/issues/001-DEC-1-resolve-open-decisions.md).
- **Repository settings:** the `SNYK_TOKEN` secret; `poc08-evals` as a required check next to `check`.
- **The T10 probe workload** (PoC-5 criterion 8, now its only flag).
- **Git identity:** `git config --global user.name` is unset. The app's sync tool refuses until it is set; sessions commit with `git -c user.name="Aleksandr Derechei" commit`.
- **Local leftovers:** the 2026-11-07 handover commits (`fdaa693`, `2b93a9f`, `0c187fb`) sit on the main checkout's local `main` (ahead 3) and on branches `claude/agent-container-checkpoint-efb1b0` and `claude/review-agent-handover-6e929d`, never pushed. This file supersedes them; drop them when convenient.

## How to work here

- `make setup` after every merge of `main` that adds a workspace package, or `make quick` fails in lint ("Could not find package 'chassis'").
- `make progress` shows the product plan per phase (`PHASE=A` for one phase's issues). Since #111, CI runs on PRs and on `main` only: a branch pushed with no PR gets no run. Since #113, `make quick` picks its tests from the changed files (`scripts/quick_select.py`): a docs-only change runs no test, a shared file runs every non-PoC test, and PoC tests run only on demand (`make test-poc POC=NN`).
- `make quick` before every commit; the pre-commit hook runs it too. Since #121 a failing selected test fails it; before, the recipe's trailing `echo` hid the failure, so a commit between #113 and #121 relied on the author reading the output. It takes 3 to 10 minutes, more when other sessions run tests on the same Mac. Never kill another session's processes; stop your own by PID.
- Commits are SSH-signed through 1Password. Run `git commit` from a foreground Bash call with a 10-minute timeout, not from the Terminal panel. If the signer times out, ask the user to approve it and retry once. `1Password: failed to fill whole buffer` means the user must unlock or restart 1Password; stop and say so. Never commit unsigned, never skip the hook.
- `main` is protected: the CI `check` job is required. Open a PR from a branch. The permission classifier refuses auto-merge (`gh pr merge --auto` and the app's `set_auto_merge`) every time; the user merges by hand, so say the PR is ready and stop. Never push to `main`, never force-push.
- The remote is GitHub (`odere-pro/agent-playground`), not GitLab.
- Subagents in their own worktree cannot be written into by another agent of the same session (a hook blocks it). Hand a patch to the agent that owns the worktree. Long agent tasks hit a turn limit; resume them with a short brief.
- Kind, Compose, and the load stack are one shared machine: run infra work in one worktree at a time (the `git-flow` skill).
- Docs: plain words, short sentences, US spelling. A value the epic does not give is marked `suggested:`. A rule change is an ADR first, then the spec.

## Kickoff prompt for the next session

```text
Read HANDOVER.md at the repository root, then the product plan it links
(docs/plans/2026-11-06-agent-container-product.md) and docs/specs/README.md.

Goal: continue the agent container product from the checkpoint of 2026-11-09
(night).

1. Check what changed since main d0152bb: git log, open PRs (gh pr list),
   make progress, and the phase C session's PR (trace join, create_agent)
   in HANDOVER.md. Report their state in two lines.
2. If phase C's PR has merged, tick 128's last box or restate it with a
   date, and retire 128 when its boxes hold.
3. Take the next step the user picks: the counters born at their first
   count (117 X-3 follow-up, small, no user input), the 023 C-5 client
   guide (due 2026-12-02), or step 4 once the Mac run is in.
   chassis-architect reviews each code PR before it opens.
4. Update HANDOVER.md at the end: what landed, what is in flight, the next
   step.

Do not ask before opening PRs; deliver until the step's done-when holds.
The user merges. Ask only for the user-only items in HANDOVER.md.
```
