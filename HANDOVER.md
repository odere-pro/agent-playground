# Handover

Checkpoint of 2026-11-15, at `main` `c19947b`. Read this first in the next session, then replace it at the next checkpoint.

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

Per phase on `c19947b`:

| Phase | Issues done | Boxes ticked | Open complexity |
| ----- | ----------- | ------------ | --------------- |
| A Close the must-haves | 4 of 10 | 73 of 90 (81%) | 2 L, 2 M, 2 S |
| B Models and the judge | 2 of 10 | 57 of 76 (75%) | 1 L, 5 M, 2 S |
| C LangChain first | 1 of 2 | 13 of 14 (92%) | 1 L |
| D LangSmith | 0 of 3 | 7 of 15 (46%) | 1 M, 2 S |
| E Measure and show | 0 of 10 | 44 of 85 (51%) | 1 L, 5 M, 4 S |
| F Template GA | 1 of 6 | 31 of 46 (67%) | 2 L, 2 M, 1 S |
| G Under load and on rollout | 2 of 3 | 11 of 18 (61%) | 1 L |

The ten done issues: 007 H-1, 009 CH-1, 015 H-8, 017 H-4, 018 H-18, 128 CH-9, 134 CH-14, 135 CH-15, 136 CH-16, and 138 CH-18. Four retired since the last checkpoint: 018 H-18 (PR 189), 015 H-8 (193), 009 CH-1 (211), and 017 H-4 (210).

The critical path to the whole plan, from `make progress`: 016 H-9 → 024 CH-3 → 025 H-10 → 057 H-19 → 059 H-15.

What PRs 188 to 214 delivered, by area. The PR titles in `git log --oneline 4d6cf92..c19947b` name each box.

- **Phase A:** 009 CH-1 retired. P-9: every JSON-RPC error code is `a2a.rpc_error` (190, 194). P-7: cancel reaches `handle` in every lane, and `inprocess` streams (211, contract v9 items 24 to 26). 018 H-18 retired (189). 055 CH-6: the two-key workload token, one key per direction (202); the kind tests, the seed key check, and rotate (207).
- **Phase B:** 012 H-3: an unlisted route is refused with 403 (191); a run's call is admitted only on its own route (198); bounded route labels, a cap on `model`, one route in plain A2A mode (208, contract v9 items 20 to 23). 017 H-4 retired: the gate reruns a model error, never an `a2a.*` lane error (210, ADR-017, contract v9 item 28). The live router drill for G-1b, G-2, and H-3 (195). 013 CH-2 stock SDK clients, live (201).
- **Phase E:** 014 H-7: sampling in the Collector (200), the tail window (204), a live run at rate 0 that keeps error traces (213). 046 A-5: the first-attempt counter (196). 006 G-6: the dashboard, live (201). 004 G-2: Prometheus scrapes LiteLLM live, with no `user` label (214).
- **Phase F:** 015 H-8 retired by a recorded `make test-integration` run (193). 059 H-15: `--trust`, and routes the key lacks are refused (192); the trust-aware lane default (199, 205); the key's routes read from a deploy manifest, box 7 (212).
- **No phase:** 019 H-17: `input` left out of result events by default, and `original_model` (197, 203, ADR-016). The code-runner answers a platform fault before the stream with a retryable 503 (209, P-35, contract v9 item 27). The finished-PoC tests are green again (206).

Decisions the user made this session:

- 017 H-4: the gate retries a model error ([ADR-017](docs/planning/adr/017-gate-retries-a-model-error-in-one-budget.md)).
- 137 CH-17: keep the `evaluator_mode` default.
- kagent-adk: read the pinned upstream source. Done; no upstream patch is needed.
- P-35: the code-runner 503 covers the faults before the stream (contract v9 item 27).
- 004 G-2: Prometheus may hold one LiteLLM scrape key. It is an exception to ADR-001 hard requirement 1, approved by the user on 2026-11-15. The scope is in [deploy/CLAUDE.md](deploy/CLAUDE.md), next to the rule on generated secrets.
- 016 H-9: build a dev reload.

Review notes to carry:

- 010 H-12: the bootstrap ceilings are `suggested:`. A start document above them now fails startup.
- 130 CH-11 and 131 CH-12: the LangSmith paths and field names were never checked against a real LangSmith (`suggested:`).
- The router drill of PR 195 ran before the stricter 012 H-3 route rule (PR 208). Its boxes need a rerun on `main`.
- Flaky under load: `packages/chassis/tests/test_contracts.py::TestLiteLLMModel::test_a_call_ends_at_its_timeout`. It passes alone and on an idle machine.

## In flight when this was written

- 055 CH-6, two keys for the kagent-adk remote: branch `feat/055-kagent-two-key-1115`, one commit, not pushed. It needs a `platform-security` review before its PR.
- 016 H-9, the dev reload build: branch `feat/016-dev-reload-1115`. No commit on it yet.
- 016 H-9, the live hot-reload run: branch `test/016-hot-reload-live-1115`. No commit on it yet. It runs after the build.
- The router-drill rerun on `main`: branch `test/router-drill-rerun-1115`, running now on the PoC-8 router-drill Compose stack.
- No PR is open. PR 214 (004 G-2 scrape) merged at `c19947b`.

## Next steps, in order

Read an issue's acceptance criteria before you start; they are the done-when. The epic gives no piece sizes, so each size below is `suggested:`. The 004 G-2 scrape branch landed as PR 214.

| # | Piece | Issue and box | Needs | Size | Depends on |
| - | ----- | ------------- | ----- | ---- | ---------- |
| 1 | Land the open branches | 055 CH-6 kagent-adk two keys; 016 H-9 dev reload | offline build; a `platform-security` review for 055 | S | nothing |
| 2 | The hot-reload run | 016 H-9 box "a code change in the workload is live without a rebuild or a restart". It is the last box; retire 016 after it | live Compose | S | 1 (016 build) |
| 3 | Finish the router-drill rerun | the 002 G-1, 003 G-1b, 004 G-2, and 012 H-3 boxes of PR 195, under the PR 208 route rule | live Compose (PoC-8 router-drill stack) | M | in flight now |
| 4 | Kind track, part 1, on a fresh `poc05` cluster (skill `poc-05-operate`) | 055 CH-6 two-key live tests, kagent-adk included; 059 H-15 box 2 (a `--trust untrusted` chart runs as a `remote`); the 004 G-2 kind box (a spoofed agent tag counts under the key's agent, H31) | live kind | M | 1 (055 branch) |
| 5 | Kind track, part 2 | 024 CH-3, four boxes: the tag rollout, the workload port unreachable, Secrets only in the chassis, the liveness restart | live kind | L | 016 H-9 (2) |
| 6 | Kind track, part 3 | 026 CH-4, five boxes: credentials per service, egress denied, revoke one service, chart defaults pass admission, `seed.sh rekey` | live kind | L | 024 CH-3 (5); 002 G-1 is still open |
| 7 | Builds for 056 CH-7 | the rollout rings, before any kind run | offline build | L | 024 CH-3, 055 CH-6 |
| 8 | Builds for 139 CH-19 | `make platform-up`, before any run | offline build, then the user's registry and tag | L | 024 CH-3, 026 CH-4 |
| 9 | Offline boxes to triage first | 023 C-5, two boxes (the chassis marking wins over a workload's own; an older consumer reads `aigenerated`), due before 2026-12-02; 059 H-15 box "`agentctl new` renders `evals/cases.yaml` and `make eval` runs the judge" | offline build | S each | 023 lists 010 H-12 and 019 H-17 as open deps; check the box needs them |

`make progress` does not sort boxes by what they need. Item 9 comes from reading the open boxes; triage it before you build.

## Only the user can do these

- **Decisions still owed:**
  - 001 DEC-1: the broker, the first cloud, the north star, the primary trace view per environment, the owners, and the reuse picks ([001 DEC-1](docs/planning/issues/001-DEC-1-resolve-open-decisions.md)).
  - 139 CH-19: the registry and tag of the published default image.
  - 022 H-6: caller API keys (ADR-008 open question).
  - 132 CH-13: who signs off the `platform-security` review box.
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

Lessons from this session:

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
2026-11-15, main c19947b (236 of 344 boxes, 10 of 44 issues).

1. Check what changed since c19947b: git log, gh pr list, make progress.
   Report the numbers in two lines. Check whether the router-drill
   rerun (test/router-drill-rerun-1115) landed.
2. Land the open branches: 055 CH-6 kagent-adk two keys (platform-security
   review first), then 016 H-9 dev reload.
3. Run the 016 H-9 hot-reload live run on Compose. Retire 016 if it proves
   the last box.
4. If the router-drill rerun is not done, finish it on main.
5. Start a fresh poc05 kind cluster (skill poc-05-operate). In order:
   055 CH-6 two-key live tests with kagent-adk, 059 H-15 box 2, the
   004 G-2 kind box, then 024 CH-3, then 026 CH-4.
6. Build what 056 CH-7 (rings) and 139 CH-19 (make platform-up) need first.
7. Triage the offline boxes in HANDOVER.md "Next steps" item 9.
8. Update HANDOVER.md and the plan's "Progress" and "In progress" at the end.

End each live run in a dated note, and tick only what the note proves.
Run make check before each PR. Check the remote branch before each push.
One piece per worktree, one PR per piece. The user merges.
```
