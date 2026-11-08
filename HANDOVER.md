# Handover

Checkpoint of 2026-11-08, at `main` `f49d542` plus the PRs below. Read this first in the next session, then replace it at the next checkpoint.

## Where things stand

The PoC track is over. The product is the agent container: one chassis image in front of every agent.

- **What it is:** [docs/specs/README.md](docs/specs/README.md). One fact in one place. The wire contract in force is [docs/specs/contract.md](docs/specs/contract.md).
- **What is built next, and in what order:** [the product plan](docs/plans/2026-11-06-agent-container-product.md), phases A to G. Its new section "The issues in each phase" names the issues per phase (PR #103).
- **The backlog follows the plan:** [docs/planning/issues/000-plan.md](docs/planning/issues/000-plan.md).
- **Open PoC items, by who can do them:** [docs/planning/poc/011-what-is-left.md](docs/planning/poc/011-what-is-left.md).

Landed or in flight on 2026-11-08:

| What | PR | State when this was written |
| ---- | -- | --------------------------- |
| `remote-lane` green again: `agent-echo-events`' allowed control is Kafka, not LiteLLM | #100 | Merged (another session). Run 42503295381 on `f49d542`: `207 passed, 3 skipped, 2 xfailed` |
| Step 0: the plan and the specs in line with the backlog; ADR-012 (drain order, Proposed) | #103 | Open, squash auto-merge on |
| Step 1: 134 CH-14, the `cloud` security defaults; 134 retires to `WD` | #104 | Open, squash auto-merge on |
| This file, and the PoC-5 criterion 3 flag lifted (MinIO through the chassis, run 42503295381) | this PR | Open, squash auto-merge on |

The user decided on 2026-11-08, each option 1: **P-4** (`/metrics` needs `agent:read` in `cloud`), **P-26** (`fail_closed` on a rate-limit store outage in `cloud` only), **P-27** (`user.id` in baggage is `sha256[:32]` of the subject, in every lane). They are recorded in [the backlog review proposals](docs/reviews/2026-11-06-backlog-review/proposals.md), ADR-007, ADR-008, and 022 H-6 (in #104).

## In flight when this was written

- **#103 and #104** both edit `docs/specs/contract.md` and the backlog review `proposals.md`, in different places. If the second one to merge shows a conflict, rebase it on `main`, run `make planning-sync planning-check`, and push. Never force-push to `main`; a branch push after a rebase is fine.
- **#104's kind proof:** its two new raw `tools/call` asserts in `pocs/poc-05-sandboxed/tests/test_poc05_kind_tool_gateway.py` (H07, H08, the gateway's own `tool_denied`) are proven only by the `remote-lane` run on the PR. If the gateway answers another denial code there, widen those two lines to `DENIED_CODES`.
- **#103's open choices** for the user to check in review: the phase for issues 134 to 139 (the phase map predates them), and the plan keeps its own week sizes with a note on the gap to the map's days.
- **Two proposed sessions** (cards in the 2026-11-08 session, not started when this was written): "Finish phase A: 122 X-5 throughput, 056 CH-7 warn-first" and "Model proxy failures and backpressure: CH-16 then CH-15". If they are gone, start them from steps 1 and 3 below.

## Next steps, in order

Each step names its issue. Read the issue's acceptance criteria before you start; they are the done-when.

1. **Finish phase A.** Throughput at 4 pairs on a Linux host ([122 X-5](docs/planning/issues/122-X-5-load-tests.md)); a GitHub Actions Linux runner counts. Warn-first for a new minimum chassis version ([056 CH-7](docs/planning/issues/056-CH-7-chassis-release-rings.md), now in phase A). The 023 C-5 client guide is due before 2026-12-02 (AI Act Art. 50(2)). The T10 probe workload is the user's.
2. **The drain order (P-29).** [ADR-012](docs/planning/adr/012-drain-order-on-rollout.md) (in #103) proposes option 1: the chassis becomes the main container and the workload the native sidecar, as PoC-4 measured. It waits on the user's pick. Then the chart change, its tests (`pocs/poc-09-agent-mvp-template/tests/test_poc09_chart.py`), and the 024 CH-3 rolling-restart drill on kind, first on the chart as built.
3. **The model proxy under failure, and backpressure (phase G).** [136 CH-16](docs/planning/issues/136-CH-16-model-proxy-failures.md) first: LiteLLM `num_retries` and `fallbacks` per route, the budget refusal code, a per-call timeout. Then [135 CH-15](docs/planning/issues/135-CH-15-backpressure.md): `max_in_flight`, 503 `overloaded`. The schema versions on the card are P-30.
4. **Make the judge safe to turn on (phase B).** [137 CH-17](docs/planning/issues/137-CH-17-judge-calibration.md), then [138 CH-18](docs/planning/issues/138-CH-18-judge-per-agent.md). Until both pass, the gate reports (`async`) and never blocks. The judge numbers need the Mac run.
5. **LangChain first, the rest (phase C).** [128 CH-9](docs/planning/issues/128-CH-9-langgraph-checkpointer-interrupts.md), then the `langchain-mcp-adapters` swap, a trace-join test for LangGraph's graph steps, and a `create_agent` variant in the template.
6. **LangSmith (phase D).** Needs the user's decision on the primary trace view first. 129 CH-10, 130 CH-11, 131 CH-12.
7. **Measure and show (phase E).** Needs the north star decision. 117 X-3, 046 A-5, 132 CH-13.
8. **Template GA (phase F), last.** 059 H-15, 025 H-10, 139 CH-19.

Follow-ups CH-14 left, not yet issues: a keyed `user.id` (HMAC with a chassis-only key; the unkeyed hash lets a reader test guessed subjects and link a user across agents), and P-27 option 2 (no `user.id` at all to an `untrusted` remote).

Steps 1 and 3 touch different files and can run as parallel sessions, one worktree each. Step 3 touches the model proxy files CH-14 changed; start it after #104 merges, or rebase on it.

## Only the user can do these

- **The Mac run:** `make poc06-mac`. It gives the hosted big-model numbers (closes PoC-6a and 6b) and the judge's cost, latency, and agreement (unblocks step 4).
- **Decisions:** P-29, the drain order (ADR-012, blocks step 2); the north star (blocks phase E); the primary trace view per environment, LangSmith or Langfuse (blocks phase D). The other open product decisions are [001 DEC-1](docs/planning/issues/001-DEC-1-resolve-open-decisions.md).
- **Repository settings:** the `SNYK_TOKEN` secret; `poc08-evals` as a required check next to `check`.
- **The T10 probe workload** (PoC-5 criterion 8, now its only flag).
- **Git identity:** `git config --global user.name` is unset. The app's sync tool refuses until it is set.
- **Local leftovers:** the 2026-11-07 handover commits (`fdaa693`, `2b93a9f`, `0c187fb`) sit on the main checkout's local `main` (ahead 3) and on branch `claude/agent-container-checkpoint-efb1b0`, never pushed. This file supersedes them; drop them when convenient.

## How to work here

- `make setup` after every merge of `main` that adds a workspace package, or `make quick` fails in lint ("Could not find package 'chassis'").
- `make quick` before every commit; the pre-commit hook runs it too. It takes 3 to 10 minutes, more when other sessions run tests on the same Mac. Never kill another session's processes; stop your own by PID.
- Commits are SSH-signed through 1Password. Run `git commit` in the foreground. If the signer times out, ask the user to approve it and retry once. Never commit unsigned, never skip the hook.
- `main` is protected: the CI `check` job is required. Open a PR from a branch and turn on squash auto-merge. The permission classifier may refuse `gh pr merge --auto` from a subagent; the app's PR tool (`set_auto_merge`) asks the user instead. Never push to `main`, never force-push.
- The remote is GitHub (`odere-pro/agent-playground`), not GitLab.
- Subagents in their own worktree cannot be written into by another agent of the same session (a hook blocks it). Hand a patch to the agent that owns the worktree. Long agent tasks hit a turn limit; resume them with a short brief.
- Kind, Compose, and the load stack are one shared machine: run infra work in one worktree at a time (the `git-flow` skill).
- Docs: plain words, short sentences, US spelling. A value the epic does not give is marked `suggested:`. A rule change is an ADR first, then the spec.

## Kickoff prompt for the next session

```text
Read HANDOVER.md at the repository root, then the product plan it links
(docs/plans/2026-11-06-agent-container-product.md) and docs/specs/README.md.

Goal: continue the agent container product from the checkpoint of 2026-11-08.

1. Check what changed since main f49d542: git log, open PRs (gh pr list),
   and the in-flight items in HANDOVER.md (#103, #104, the two proposed
   sessions). Report their state in two lines.
2. Do step 1 of "Next steps" (phase A's rest: 122 X-5, 056 CH-7), unless a
   session already owns it. Read each issue's acceptance criteria first.
   Failing tests first, make quick green, ask platform-security to review,
   then a PR with squash auto-merge.
3. If the user has picked P-29, do step 2 (the drain order) the same way.
4. Update HANDOVER.md at the end: what landed, what is in flight, the next
   step.

Do not ask before opening PRs or enabling auto-merge; deliver until the
step's done-when holds. Ask only for the user-only items in HANDOVER.md.
```
