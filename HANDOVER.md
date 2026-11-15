# Handover

Checkpoint of 2026-11-15, at `main` `fe55768`. Read this first in the next session, then replace it at the next checkpoint.

## Where things stand

The PoC track is over. The product is the agent container: one chassis image in front of every agent.

- **What it is:** [docs/specs/README.md](docs/specs/README.md). One fact in one place. The wire contract in force is [docs/specs/contract.md](docs/specs/contract.md), with the v9 rules in [docs/contracts/contract-v9.md](docs/contracts/contract-v9.md).
- **What is built next, and in what order:** [the product plan](docs/plans/2026-11-06-agent-container-product.md), phases A to G, with "In progress" and "Left to do".
- **The backlog follows the plan:** [docs/planning/issues/000-plan.md](docs/planning/issues/000-plan.md).
- **Open PoC items, by who can do them:** [docs/planning/poc/011-what-is-left.md](docs/planning/poc/011-what-is-left.md).

Progress, measured with `make progress` on `main`:

| When | `main` | Boxes ticked | Issues done |
| ---- | ------ | ------------ | ----------- |
| 2026-11-10 | `fe32e82` | 144 of 344 (41%) | 4 of 44 |
| After PRs 131 to 154 | | 183 of 344 (53%) | |
| After PRs 155 to 173 | `b319d8c` | 197 of 344 (57%) | |
| 2026-11-13, after wave 7 (PRs 174 to 187) | `4d6cf92` | 213 of 344 (61%) | 6 of 44 |
| 2026-11-15, after PRs 188 to 214 | `c19947b` | 236 of 344 (68%) | 10 of 44 |
| 2026-11-15, after PRs 215 to 218 | `fe55768` | 243 of 344 (70%) | 10 of 44 |

Per phase on `fe55768`:

| Phase | Issues done | Boxes ticked | Open complexity |
| ----- | ----------- | ------------ | --------------- |
| A Close the must-haves | 4 of 10 | 73 of 90 (81%) | 2 L, 2 M, 2 S |
| B Models and the judge | 2 of 10 | 57 of 76 (75%) | 1 L, 5 M, 2 S |
| C LangChain first | 1 of 2 | 13 of 14 (92%) | 1 L |
| D LangSmith | 0 of 3 | 8 of 15 (53%) | 1 M, 2 S |
| E Measure and show | 0 of 10 | 50 of 85 (58%) | 1 L, 5 M, 4 S |
| F Template GA | 1 of 6 | 31 of 46 (67%) | 2 L, 2 M, 1 S |
| G Under load and on rollout | 2 of 3 | 11 of 18 (61%) | 1 L |

The ten done issues: 007 H-1, 009 CH-1, 015 H-8, 017 H-4, 018 H-18, 128 CH-9, 134 CH-14, 135 CH-15, 136 CH-16, and 138 CH-18. None retired since PR 215.

The critical path to the whole plan, from `make progress`: 016 H-9 → 024 CH-3 → 025 H-10 → 057 H-19 → 059 H-15.

What PRs 188 to 214 delivered is in git history: `git log --oneline 4d6cf92..c19947b`.

## Merged since PR 215 was written

- **216, 055 CH-6:** two keys for the kagent-adk remote, one key per direction. Offline only; no box ticked. `platform-security` gave ACCEPT. The live kind run on a freshly seeded cluster is still to do.
- **217, the router-drill rerun on `main`:** 11 passed, no regression from the 012 H-3 route rule or the 004 G-2 scrape. No new box ticked; the 004 box "counted under the agent of its key" stays open. Note: `pocs/poc-08-cross-cutting-build/notes/2026-11-15-router-drill-rerun.md`.
- **218, the user's decisions of 2026-11-15.** Seven boxes ticked: six in 001 DEC-1, one in 129 CH-10.

The decisions in PR 218:

- **Broker:** NATS JetStream (019 H-17, 020 X-8, 060 H-23).
- **First cloud:** AWS (038 X-1a). On EKS, gVisor runs on a self-managed node group.
- **Public A2A (061 H-5):** not in version 1, so P2.
- **North star:** agents in production on the chassis. No targets until the first real week, which becomes the baseline.
- **Trace view:** LangSmith in `cloud`, Langfuse elsewhere. The score goes to the primary view only (ADR-007 and ADR-009 amendments).
- **Reuse picks:** Langfuse is the golden-set store. lakeFS or DVC waits for 069 D-7; the registry base for 076 R-1.
- **139 CH-19:** the default image is `ghcr.io/odere-pro/agent-chassis`, tagged `vX.Y.Z` and pinned by digest in the chart.
- **022 H-6:** OIDC only for version 1. Caller API keys are parked (ADR-008 amendment).
- **132 CH-13:** the `platform-security` agent reviews the OWASP agentic map and writes a dated note. The user signs off in that note.

Review notes to carry:

- 010 H-12: the bootstrap ceilings are `suggested:`. A start document above them now fails startup.
- 130 CH-11 and 131 CH-12: the LangSmith paths and field names were never checked against a real LangSmith (`suggested:`).
- PR 218: the user gave no reasons for AWS, the public A2A priority, or the north star. Each reason in 001 is `suggested:`.
- 020 X-8 box 1 was proven with Kafka. NATS is now the broker, so it needs a recheck.
- Flaky under load: `packages/chassis/tests/test_contracts.py::TestLiteLLMModel::test_a_call_ends_at_its_timeout`. It passes alone and on an idle machine.

## In flight in the old session

Do not start this in the new session.

- **016 H-9, the dev hot reload:** branch `feat/016-dev-reload-1115`. Built and reviewed (ACCEPT), `make check` green, being committed. The old session also does the live hot-reload Compose run and opens the PR.
- Before you touch 016, check `gh pr list --state all` for that PR. At this checkpoint no 016 PR is open.

## Next steps, in order

Read an issue's acceptance criteria before you start; they are the done-when. The epic gives no piece sizes, so each size below is `suggested:`.

| # | Piece | Issue and box | Needs | Size | Depends on |
| - | ----- | ------------- | ----- | ---- | ---------- |
| a | Kind track, part 1, on a fresh `poc05` cluster (skill `poc-05-operate`) | 055 CH-6 two-key live tests, kagent-adk included; 059 H-15 box 2 (a `--trust untrusted` chart runs as a `remote`); the 004 G-2 kind box (a spoofed agent tag counts under the key's agent, H31) | live kind | M | nothing. Seed fresh. A cluster seeded by old code needs `seed.sh rotate remote-kagent-adk-token` |
| b | Kind track, part 2, then part 3 | 024 CH-3, four boxes (the tag rollout, the workload port unreachable, Secrets only in the chassis, the liveness restart); then 026 CH-4, five boxes (credentials per service, egress denied, revoke one service, chart defaults pass admission, `seed.sh rekey`) | live kind | L each | 024 waits on 016 H-9 (old session); 026 waits on 024, and 002 G-1 is still open |
| c | 139 CH-19 | build `make platform-up`; publish the default image to `ghcr.io/odere-pro/agent-chassis` | offline build, then a GHCR push (user's credentials) | L | 024 CH-3, 026 CH-4 |
| d | 056 CH-7 | the rollout rings: the builds first | offline build, then live kind | L | 024 CH-3, 055 CH-6 |
| e | 022 H-6 | an `OidcAuth` route test for the reworded box. The current route test runs `StaticTokens` | offline | S | nothing |
| f | 132 CH-13 | the `platform-security` agent reviews the OWASP agentic map and writes a dated note | offline, then user sign-off in the note | S | the backlog lists 026 CH-4; check the review needs it |
| g | The north star | write "agents in production on the chassis" into the product plan and `docs/specs/metrics.md` | offline docs | S | nothing |
| h | 020 X-8 | box 1 was proven with Kafka; recheck it against NATS JetStream | live Compose | M | check 020's deps first |
| i | Offline boxes to triage | 023 C-5, two boxes (the chassis marking wins over a workload's own; an older consumer reads `aigenerated`), due 2026-12-02; 059 H-15 box "`agentctl new` renders `evals/cases.yaml` and `make eval` runs the judge" | offline | S each | 023 lists 010 H-12 and 019 H-17 as open deps; check the box needs them |

`make progress` does not sort boxes by what they need. Item i comes from reading the open boxes; triage it before you build.

## Only the user can do these

- **Owners** for the epic's live dependencies: the cloud account, the golden-set reviewer, legal and compliance. Still open in 001 DEC-1.
- **The training-data rules.** Parked with the SLM track.
- **Sign off** the 132 CH-13 review note.
- **Start Docker** for the live Compose and kind tracks.
- **The Mac run:** `make poc06-mac`. It gives the judge's numbers for 028 S-3 and 137 CH-17.
- **Repository settings,** from before: the `SNYK_TOKEN` secret; `poc08-evals` as a required check next to `check`.
- **The T10 probe workload** (PoC-5 criterion 8). It is the last box of 055 CH-6.
- **Merge every PR.** Auto-merge is refused.

## How to work here

How a wave runs:

1. Start from a clean checkout at `origin/main`. Run `env -u VIRTUAL_ENV uv sync --all-packages`, then `make progress`.
2. Triage: sort each open box by what it needs. Build only what can be proved offline.
3. Build each piece in a fresh worktree. One piece per worktree, one PR per piece.
4. Review, then fix. The user merges every PR.

Lessons from the last sessions:

- Run `make check` (mypy included) before a PR, not only `make quick`. PR 210 failed CI on mypy.
- The `developer` agent's role refuses to commit. The orchestrator commits: `git -C <worktree> -c user.name=... commit`.
- Another writer, signing as "Oleksandr Derechei", pushed fixes to PR branches in parallel (209, 210). Check the remote branch before each push.
- The secret scan flags long run ids in notes. Keep a run id to 8 characters.
- Contract v9 sections: F is items 17 to 23, G 24 to 26, H 27, I 28. A new rule takes section J and item 29.

Commit failures and their fixes:

- A ticked line needs `· Delivered by:` and its evidence.
- Ticking an issue's last box means retiring it in `backlog.py` (wave `WD`, a `Delivered by:` line).
- A new workspace package must be copied by every Dockerfile.
- A key-like literal needs `# pragma: allowlist secret`.
- If an old worktree holds the branch name, use a new name.

Other rules:

- `make setup` after a merge that adds a workspace package.
- `make quick` before every commit; the pre-commit hook runs it too. A docs-only change runs no test.
- Commits are SSH-signed through 1Password, so 1Password must be unlocked. If the signer fails, ask the user. Never commit unsigned, never skip the hook.
- `main` is protected: the CI `check` job is required. Never push to `main`, never force-push. The remote is GitHub (`odere-pro/agent-playground`).
- Never kill another session's processes; stop your own by PID.
- Compose, kind, and the load stack share one machine. Run infra work in one worktree at a time.
- Docs: plain words, short sentences, US spelling. A value the epic does not give is marked `suggested:`.

## Kickoff prompt for the next session

```text
Read HANDOVER.md at the repository root, then the product plan it links
(docs/plans/2026-11-06-agent-container-product.md).

Goal: continue the agent container product from the checkpoint of
2026-11-15, main fe55768 (243 of 344 boxes, 10 of 44 issues).

1. Check what changed since fe55768: git log, gh pr list, make progress.
   Report the numbers in two lines.
2. Do not start 016 H-9. The old session owns it
   (feat/016-dev-reload-1115, build and live hot-reload run). Check gh
   for its PR before you touch 016.
3. Start a fresh poc05 kind cluster (skill poc-05-operate). In order:
   055 CH-6 two-key live tests with kagent-adk, 059 H-15 box 2, the
   004 G-2 kind box. Seed fresh; an old seed needs
   seed.sh rotate remote-kagent-adk-token.
4. Then 024 CH-3, then 026 CH-4, on kind.
5. 139 CH-19: build make platform-up; publish the default image to
   ghcr.io/odere-pro/agent-chassis, pinned by digest.
6. 056 CH-7 rollout rings: the builds first.
7. 022 H-6: an OidcAuth route test for the reworded box.
8. 132 CH-13: platform-security reviews the OWASP agentic map in a dated
   note; the user signs off in it.
9. Write the north star ("agents in production on the chassis") into the
   product plan and docs/specs/metrics.md.
10. 020 X-8: recheck box 1 against NATS JetStream.
11. Triage the offline boxes in HANDOVER.md "Next steps" item i.
12. Update HANDOVER.md and the plan's "Progress" and "In progress" at the end.

End each live run in a dated note, and tick only what the note proves.
Run make check before each PR. Check the remote branch before each push.
One piece per worktree, one PR per piece. The user merges.
```
