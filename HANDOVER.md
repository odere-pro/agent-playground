# Handover

Checkpoint of 2026-11-15, at `main` `5689002`. Read this first in the next session, then replace it at the next checkpoint.

## Where things stand

The PoC track is over. The product is the agent container: one chassis image in front of every agent.

- **What it is:** [docs/specs/README.md](docs/specs/README.md). The wire contract in force is [docs/specs/contract.md](docs/specs/contract.md), with the v9 rules in [docs/contracts/contract-v9.md](docs/contracts/contract-v9.md).
- **What is built next:** [the product plan](docs/plans/2026-11-06-agent-container-product.md), phases A to G.
- **The backlog:** [docs/planning/issues/000-plan.md](docs/planning/issues/000-plan.md). Order, sizes, and dependencies live in `docs/planning/tools/backlog.py`.
- **Open PoC items, by who can do them:** [docs/planning/poc/011-what-is-left.md](docs/planning/poc/011-what-is-left.md).

Progress, measured with `make progress` on `main`:

| When | `main` | Boxes ticked | Issues done |
| ---- | ------ | ------------ | ----------- |
| 2026-11-10 | `fe32e82` | 144 of 344 (41%) | 4 of 44 |
| 2026-11-13, after PRs 174 to 187 | `4d6cf92` | 213 of 344 (61%) | 6 of 44 |
| 2026-11-15, after PRs 188 to 214 | `c19947b` | 236 of 344 (68%) | 10 of 44 |
| 2026-11-15, after PRs 215 to 218 | `fe55768` | 243 of 344 (70%) | 10 of 44 |
| 2026-11-15, after PRs 220 to 224 | `5689002` | 246 of 344 (71%) | 11 of 44 |

Per phase on `5689002`:

| Phase | Issues done | Boxes ticked | Open complexity |
| ----- | ----------- | ------------ | --------------- |
| A Close the must-haves | 4 of 10 | 73 of 90 (81%) | 2 L, 2 M, 2 S |
| B Models and the judge | 2 of 10 | 57 of 76 (75%) | 1 L, 5 M, 2 S |
| C LangChain first | 1 of 2 | 13 of 14 (92%) | 1 L |
| D LangSmith | 0 of 3 | 8 of 15 (53%) | 1 M, 2 S |
| E Measure and show | 0 of 10 | 52 of 85 (61%) | 1 L, 5 M, 4 S |
| F Template GA | 2 of 6 | 32 of 46 (69%) | 2 L, 2 M |
| G Under load and on rollout | 2 of 3 | 11 of 18 (61%) | 1 L |

The 11 done issues: 007 H-1, 009 CH-1, 015 H-8, 016 H-9, 017 H-4, 018 H-18, 128 CH-9, 134 CH-14, 135 CH-15, 136 CH-16, 138 CH-18. 98 boxes are open.

The critical path to the whole plan, from `make progress`: 024 CH-3 → 025 H-10 → 057 H-19 → 059 H-15.

## Merged this session

- **220, 132 CH-13:** the `platform-security` review of the OWASP agentic map. It waits on the user's sign-off in `docs/reviews/2026-11-15-owasp-agentic-map-review.md`.
- **221, 016 H-9:** the dev hot reload, with the live proof. Built by the old session. 016 is retired.
- **222, the north star:** "agents in production on the chassis", written into the product plan and `docs/specs/metrics.md`.
- **223, 022 H-6:** a route test with `OidcAuth` in front of every run and read route.
- **224, kind part 1:** the 004 G-2 kind box (a spoofed agent tag counts under the key's agent) and the 055 CH-6 live two-key results. 059 H-15 box 2 stays open (see below).

## In flight

This session still runs these. Do not start them. First run `gh pr list --state all` and look for their PRs.

- **024 CH-3, kind part 2:** branch `feat/024-ch3-kind-1115c`. The four live boxes.
- **056 CH-7:** the offline builds for the rollout rings.
- **139 CH-19:** `make platform-up`, offline, and the GHCR publish recipe. The push needs the user's credentials.
- **059 H-15, the evals box:** `agentctl new` renders `evals/cases.yaml`, and `make eval` runs the judge.
- **023 C-5:** built. Both boxes are ticked and 023 is retired. Box (a) is narrowed to "the marking is the two headers" on native routes. Box (b) is accept-and-drop of unknown CloudEvent extensions, contract v9 J.29. Its PR comes from this session. 010 H-12 and 019 H-17 did not block it.

Findings to carry:

- **A regression on `main`:** `run.sh test-remote` fails 2 tests in `pocs/poc-05-sandboxed/tests/test_poc05_kind_admission.py`. Rule 9 (agent-chassis-version) refuses the request before agent-trust-rule does. It is likely since PR 118 split rule 9.
- **059 H-15 box 2:** LangGraph raises "can't start new thread" under gVisor with `agent-lib`'s `RuntimeDefault` seccomp. PoC-6 fixed it by hand with `poc06-runsc-clone3.json`. `agent-lib` needs a seccomp value (`platform-security`). Then a fresh kind run of `test_poc09_kind_untrusted_remote.py`, now `xfail` strict.
- **132 review, two HIGH findings:** ASI04 (the signature is never checked at admission, owned by 056; the chart defaults fail rule 8, image by digest, owned by 026) and ASI05 (the row says None, but three live boxes are open). After the sign-off, a docs piece corrects the map rows.
- **022 H-6:** `close_ports` never closes `bundle.auth`, the `httpx` client of `OidcAuth`.
- **`docs/planning/tools/plan-template.md` line 145** still lists the north star as an open decision.
- From before: the 010 H-12 ceilings and the 130 CH-11 and 131 CH-12 LangSmith paths are `suggested:`. 020 X-8 box 1 was proven with Kafka; NATS is the broker now. `test_contracts.py::TestLiteLLMModel::test_a_call_ends_at_its_timeout` is flaky under load.

## Next steps, in order

Read an issue's acceptance criteria before you start. The epic gives no piece sizes, so every size is `suggested:`. "Needs" is one of: offline, live kind, live Compose, the Mac run, the user.

### Wave 1: the critical path and small fixes

| # | Piece | Issue and box | Needs | Size `suggested:` | Depends on |
| - | ----- | ------------- | ----- | ----------------- | ---------- |
| 1a | Fix the rule 9 regression | PoC-5 `test_poc05_kind_admission.py`, 2 tests | live kind | S | nothing |
| 1b | `agent-lib` seccomp value for gVisor | 059 H-15 box 2, the prep | offline, `platform-security` | S | 024 CH-3's PR: it also changes `deploy/helm`, so wait for it and merge `main` |
| 1c | Close `bundle.auth` in `close_ports` | 022 H-6 fix, no box | offline | S | nothing |
| 1d | Drop the north star from the open decisions | `plan-template.md` line 145 | offline docs | S | nothing |
| 1e | Template CI: tests and contract suite; image signing | 025 H-10, boxes 1 and 2 | offline | M | 024 CH-3 (in flight) |
| 1f | The untrusted remote chart on kind | 059 H-15 box 2 | live kind | M | 1a, 1b; 057 H-19 at issue level, this box does not need it |

### Wave 2: the rest of the critical path, and phase A on kind

| # | Piece | Issue and box | Needs | Size `suggested:` | Depends on |
| - | ----- | ------------- | ----- | ----------------- | ---------- |
| 2a | Stateless check per framework, on by default in the template CI | 057 H-19, 2 boxes | offline | M | 025 H-10 |
| 2b | Kind part 3: credentials per service, egress denied, revoke one, chart defaults pass admission, `seed.sh rekey` | 026 CH-4, 5 boxes | live kind | L | 024 CH-3; 002 G-1 (wave 4, the user's keys); the rule 8 box needs the digest from 2e |
| 2c | The rollout rings on kind; the signature box is parked | 056 CH-7, 6 boxes, then the signature box | live kind | L | 056 builds (in flight), 055 CH-6, 014 H-7; the signature box waits on the ADR-005 decision |
| 2d | `make platform-up` on kind, the demo smoke test, one trace | 139 CH-19, boxes 1 to 3 and 5 | live kind | L | 139 offline (in flight), 026 CH-4 |
| 2e | Publish the chassis image to `ghcr.io/odere-pro/agent-chassis`, pin by digest | 139 CH-19 box 4 | the user (push) | S | 139 recipe (in flight) |
| 2f | Pin a published platform release in the template | 025 H-10 box 3 | offline | S | 2e |
| 2g | Correct the OWASP map rows ASI04 and ASI05 | 132 CH-13 box 1 | offline docs | S | the user's sign-off, 026 CH-4, 022 H-6 |
| 2h | The workload gets config through `ctx`, no config-store credential | 010 H-12, 1 box | offline; kind if a render test is not enough | S | nothing |

### Wave 3: offline boxes, by phase

| # | Piece | Issue and box | Needs | Size `suggested:` | Depends on |
| - | ----- | ------------- | ----- | ----------------- | ---------- |
| 3a | `latency_ms` in the `metrics` event | 012 H-3, 1 box | offline | S | 003 G-1b, 004 G-2 (issue level; this box does not need its open boxes) |
| 3b | A changed prompt changes the gate input | 037 M-2, 1 box | offline | S | 028 S-3 |
| 3c | The judge route is checked against the service's key | 042 A-2 box 3 | offline | S | 028 S-3 (issue level; this box does not need its open boxes) |
| 3d | 30 `fail` cases with reasons; a profile test that holds off `sync` | 137 CH-17, boxes 1 and 4 | offline, `eval-expert` | M | 028 S-3, 042 A-2 (issue level; this box does not need its open boxes) |
| 3e | The LangGraph default passes the evals | 058 CH-8, 1 box | offline | S | 059 evals box (in flight) |
| 3f | The LangSmith exporter behind a profile switch | 129 CH-10 box 1 | offline | S | 014 H-7 (issue level; this box does not need its open boxes) |
| 3g | Feedback adapter picked by `spec.adapters`; the score goes where DEC-1 says | 130 CH-11, 2 boxes | offline | S | 129 CH-10 (issue level; this box does not need its open boxes) |
| 3h | Spend filtered by agent, user, and route | 006 G-6, 1 box | offline | S | 004 G-2 (issue level; this box does not need its open boxes) |
| 3i | The north star row on the dashboard | 046 A-5 box 2 | offline | S | 001 DEC-1, 014 H-7, 042 A-2 (issue level; this box does not need its open boxes) |
| 3j | Tool port: write mode, one side effect on retry, malformed output, red-team case, tool budget, `/mcp` contract | 054 H-16, 6 boxes | offline | L, split in two | 022 H-6 |
| 3k | SLO rules from an `slo` block, fallback alert, `chassis_build_info`, SLO panels, `gate_failed_open` alert, store-down alert | 117 X-3, 6 boxes | offline | M | 014 H-7 (issue level; this box does not need its open boxes) |
| 3l | Cost per 1,000 requests; a `task` label on model cost; the cost panel | 120 X-7, boxes 1, 3, 4 | offline | S | 004 G-2 (issue level; this box does not need its open boxes) |

### Wave 4: live Compose

| # | Piece | Issue and box | Needs | Size `suggested:` | Depends on |
| - | ----- | ------------- | ----- | ----------------- | ---------- |
| 4a | A real LiteLLM router: both formats, three providers, no key in config, a keyless call refused | 002 G-1, 4 boxes | live Compose, the user's provider keys | M | nothing |
| 4b | `big-default` and `local-small` by name | 003 G-1b, 1 box | live Compose | S | 002 G-1 |
| 4c | Token counts: proxy against router, router against provider; cost totals | 013 CH-2, 004 G-2, 120 X-7 box 2 | live Compose | M | 002 G-1; 012 H-3 for 013 CH-2 |
| 4d | A model call in Langfuse; the OTel SDK cost; the chassis memory with redaction on | 014 H-7, 2 boxes; 022 H-6 memory box | live Compose (PoC-7 and PoC-8 stacks) | M | nothing |
| 4e | Rate limit across two chassis on one Valkey | 022 H-6 box 1 | live Compose | S | nothing |
| 4f | Fault and latency alerts on the PoC-8 stack | 117 X-3, 2 boxes | live Compose | M | 3k |
| 4g | LangSmith stack tests; the Langfuse dataset stack test | 129 CH-10, 2 boxes; 131 CH-12 box 2 | live Compose | M | 3f |
| 4h | Every dashboard panel has data | 046 A-5 box 1 | live Compose, then kind | M | 4d |
| 4i | `demo.sh --live` calls `/v1/messages` on the LangGraph default | 059 H-15 box 1 | live Compose | M | 057 H-19 at issue level, this box does not need it |
| 4j | Recheck the broker on NATS JetStream; live publish and result-event checks | 020 X-8, 7 boxes; 019 H-17, 2 boxes | live Compose and kind | L | outside phases A to G; 139 lists 020 as a later dependency |

### Wave 5: the Mac run and the user

| # | Piece | Issue and box | Needs | Size `suggested:` | Depends on |
| - | ----- | ------------- | ----- | ----------------- | ---------- |
| 5a | `make poc06-mac`: the judge's agreement, TPR, TNR, cost, and latency | 028 S-3; 042 A-2, 3 boxes; 137 CH-17, boxes 3 and 5 | the Mac run | M | 3d |
| 5b | Turn on the gate in `cloud` (async, 0.1) | 042 A-2 box 4 | offline | S | 5a |
| 5c | Prompt Guard 2 rerun | 022 H-6, the injection box | the Mac run | S | nothing |
| 5d | Verdicts match on two engines | 131 CH-12 box 1 | the Mac run | S | 5a |
| 5e | Items in "Only the user can do these" | 001 DEC-1, 055 CH-6, 122 X-5, 137 CH-17, 059 H-15, 117 X-3, 022 H-6, 046 A-5 | the user | — | — |

## Only the user can do these

- **Sign off** the 132 CH-13 review note, `docs/reviews/2026-11-15-owasp-agentic-map-review.md`.
- **Decide the ADR-005 amendment.** `chassis-architect` says Kyverno bends decision 4 ("no Kyverno"). The proposal: Kyverno for `verifyImages` only; the trust rules stay ValidatingAdmissionPolicies. Compare sigstore `policy-controller`. It covers the 056 CH-7 signature box and 080 R-7. The 056 PR lands the rings and parks the signature rule.
  - When it is rebuilt, `platform-security` asks for: `imageReferences` from `registryPrefix` + "chassis" (the first draft failed open on a cloud); `failurePolicy: Fail`; `cloud` refusing `required: false` once the chassis is signed; a NetworkPolicy for Kyverno's egress.
- **Push the chassis image** to GHCR (139 CH-19). Use a classic PAT with only `write:packages`, not `gh auth token`. Run `docker login ghcr.io -u odere-pro --password-stdin`, then `scripts/publish_chassis.sh publish` (it lands with the 139 PR, not on `main` yet), then `docker logout ghcr.io`. The script builds with SBOM and provenance, pushes, signs keyless by digest (a browser OIDC login), and pins the digest in the chart. It refuses a dirty tree, a HEAD not on `origin/main`, or an existing tag. GHCR packages start private.
- **Provider keys** for the live router run (002 G-1, 004 G-2), in `~/Git/.env`.
- **Owners and reasons** for the epic's live dependencies: cloud account, golden-set reviewer, legal and compliance (001 DEC-1, 2 boxes).
- **The Mac run:** `make poc06-mac`. It gives the judge's numbers for 028 S-3, 042 A-2, and 137 CH-17.
- **The T10 probe workload** (PoC-5 criterion 8). It is the last box of 055 CH-6.
- **A Linux host with pinned CPUs** for throughput at 4 pairs (122 X-5, 4 boxes).
- **A second labeler** for 20% of the gate set (137 CH-17).
- **A person new to the code** for the `agentctl new` trial (059 H-15).
- **Decisions:** named-entity masking in the Collector (Q6, 022 H-6); the config-version label on metrics, a contract change (046 A-5).
- **The first real production week.** It sets the north-star baseline and the weekly SLO note (117 X-3).
- **Repository settings:** the `SNYK_TOKEN` secret; `poc08-evals` as a required check.
- **Start Docker, unlock 1Password,** and **merge every PR.** Auto-merge is refused.

## How to work here

How a wave runs:

1. Start from a clean checkout at `origin/main`. Run `env -u VIRTUAL_ENV uv sync --all-packages`, then `make progress`.
2. Run `gh pr list --state all`. Skip anything in flight.
3. Build each piece in a fresh worktree. One piece per worktree, one PR per piece.
4. Review, then fix. The user merges every PR.

Lessons:

- Run `make check` (mypy included) before a PR, not only `make quick`.
- The `developer` agent refuses to commit. The orchestrator commits.
- Subagents may hit a write hook that limits writes to the orchestrator's worktree. Use Agent isolation worktrees.
- 1Password must be unlocked for signing and for the SSH fetch. If the signer fails, ask the user. Never commit unsigned, never skip the hook.
- Run one plain command at a time. Compound commands get denied.
- The machine: the Docker VM has about 7.75 GiB. Run one `make check` at a time beside kind. Five parallel gates gave load failures (`/ready` not 200 within 120 s).
- Each agent logs to its own `mktemp` file. Two agents once reported the same tail: one read the other's log.
- Compose, kind, and the load stack share one machine. Run infra work in one worktree at a time.
- Another writer may push to PR branches. Check the remote branch before each push.
- The secret scan flags long run ids. Keep a run id to 8 characters.
- Contract v9: section J, item 29, is taken by 023 C-5. A new rule takes the next item.

Commit failures and their fixes:

- A ticked line needs `· Delivered by:` and its evidence.
- Ticking an issue's last box means retiring it in `backlog.py` (wave `WD`, a `Delivered by:` line).
- A new workspace package must be copied by every Dockerfile. Run `make setup` after a merge that adds one.
- A key-like literal needs `# pragma: allowlist secret`.
- If an old worktree holds the branch name, use a new name.

Other rules:

- `main` is protected: the CI `check` job is required. Never push to `main`, never force-push. The remote is GitHub (`odere-pro/agent-playground`).
- Never kill another session's processes; stop your own by PID.
- Docs: plain words, short sentences, US spelling. A value the epic does not give is marked `suggested:`.

## Kickoff prompt for the next session

```text
Read HANDOVER.md at the repository root, then the product plan
(docs/plans/2026-11-06-agent-container-product.md).

Goal: continue the agent container from the checkpoint of 2026-11-15,
main 5689002 (246 of 344 boxes, 11 of 44 issues).

1. Check what changed since 5689002: git log, gh pr list --state all,
   make progress. Report the numbers in two lines.
2. Do not start the in-flight pieces: 024 CH-3 kind part 2
   (feat/024-ch3-kind-1115c), 056 CH-7 builds, 139 CH-19 offline,
   the 059 H-15 evals box, 023 C-5. Check gh for their PRs first.
3. Wave 1: fix the rule 9 regression in test_poc05_kind_admission.py;
   the agent-lib seccomp value (platform-security); close bundle.auth
   in close_ports (022); drop the north star from the open decisions
   at plan-template.md line 145; 025 H-10 CI boxes.
   Then 059 H-15 box 2 on a fresh poc05 cluster.
4. Wave 2: 057 H-19; 026 CH-4 on kind; 056 CH-7 rings on kind;
   139 CH-19 on kind; after the user's push, 025's published pin.
5. Wave 3: the offline boxes by phase, as listed in HANDOVER.md.
6. Wave 4: live Compose, one stack at a time.
7. Ask the user for the items in "Only the user can do these", first
   the ADR-005 amendment and the GHCR push.
8. Update HANDOVER.md and the plan's "Progress" and "In progress"
   at the end.

End each live run in a dated note, and tick only what the note proves.
Run make check before each PR. One make check at a time beside kind.
One piece per worktree, one PR per piece. The user merges.
```
