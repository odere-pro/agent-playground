# Handover

Checkpoint of 2026-11-13, at `main` `4d6cf92`. Read this first in the next session, then replace it at the next checkpoint.

## Where things stand

The PoC track is over. The product is the agent container: one chassis image in front of every agent.

- **What it is:** [docs/specs/README.md](docs/specs/README.md). One fact in one place. The wire contract in force is [docs/specs/contract.md](docs/specs/contract.md).
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

The six done issues in the plan's phases: 007 H-1 (PR 132), 128 CH-9, 134 CH-14, 135 CH-15, 136 CH-16, and 138 CH-18 (PRs 160 and 179).

What PRs 128 to 187 delivered, by area. The PR titles in `git log --oneline fe32e82..4d6cf92` name each box.

- **Phase A:** 007 H-1 retired (132). 010 H-12: credential-shaped values refused (144); bootstrap ceilings on reload and the `/openapi.json` start note (187). 024 CH-3 render tests for the tag bump and pins (141, 159). 023 C-5: the disclosure client guide (133) and marking on every route (164, 180). 018 H-18: the key reaches the sidecar (173). 009 CH-1: strict xfails for the cancel and JSON-RPC gaps (139).
- **Phase B:** 003 G-1b route declarations (155). 012 H-3: temperature 0 (140), run IDs in model metadata (184). 013 CH-2 stock SDK clients on the proxy (142). 017 H-4 one budget across gate attempts (138). 028 S-3 scoring command (130). 037 M-2 baseline gate (147, 174). 042 A-2 judge tokens and cost (158). 137 CH-17 kappa, TPR, TNR (154). 138 CH-18 retired (160, 179).
- **Phase C:** the trace join and the `create_agent` variant (128, 129); event-mapping contract tests per framework (176).
- **Phase D:** `fake-langsmith` traces (167); the LangSmith feedback adapter (162, 181); golden-set datasets (166, 178).
- **Phase E:** counters at 0 and `chassis_build_info` (131); the OWASP agentic map (137); the guard span (156); gate panels and the route filter (153, 168); 054 H-16 refused, bare, and changed tool counts (151, 163, 186); 022 H-6 idempotency key cap (136); 004 G-2 no user label (146).
- **Phase F:** the testing kit (134); the stateless check (135, 165); the template chart and the input-as-data case (157, 183); 059 H-15 governance block and the contract proof (152, 182); 016 H-9 compose-up box (143).
- **Phase G and no phase:** 056 CH-7 image tag on `/manifest` (145, 169). 008 H-14 class, kind, modules (148, 177). 019 H-17 result events (149, 175). 140 CH-20 `spec.memory` and the session scope (161, 185).

Review notes to carry:

- 010 H-12: the bootstrap ceilings are `suggested:`. A start document above them now fails startup.
- 130 CH-11 and 131 CH-12: the LangSmith paths and field names were never checked against a real LangSmith (`suggested:`).
- Flaky under load: `packages/chassis/tests/test_contracts.py::TestLiteLLMModel::test_a_call_ends_at_its_timeout`. It passes alone and on an idle machine.

## In flight when this was written

- Nothing. No PR is open, and every PR of wave 7 (174 to 187) is merged.
- Docker was not running on the Mac on 2026-11-13. Neither live track below has been tried.

## Next steps, in order

Offline work is close to used up. Wave 6 sent twelve agents and proved two boxes. Wave 7 added a triage step first and proved 22.

What is left, by what each box needs (the wave 7 triage):

| Needs | Boxes |
| ----- | ----- |
| A live Compose stack run | 28 |
| The user's decision | 22 |
| A real service or key | 8 |
| Another issue first | 3 |
| A person | 2 |
| A live kind run | 1 |

Not triaged, still with open boxes: 001 DEC-1, 009 CH-1, 015 H-8, 022 H-6, 024 CH-3, 026 CH-4, 055 CH-6, 056 CH-7, 057 H-19, 137 CH-17, 139 CH-19, 005 G-3. Triage them first in the next wave.

1. **The user's decisions** (next section). Each answer is one small build.
2. **The live Compose track.** The router boxes of 002, 003, 004, 012, and 013 with a real LiteLLM. 014 H-7 on the PoC-8 stack. One recorded green `make test-integration` for 015 H-8. A live template run for 016 H-9 and 059 H-15. Each run ends in a dated note.
3. **The kind track:** 026 CH-4, 055 CH-6, 024 CH-3, 139 CH-19.

Read an issue's acceptance criteria before you start; they are the done-when.

## Only the user can do these

- **Decisions:**
  - 009 CH-1: P-7, the scope of the cancel box; P-9, the JSON-RPC error code (suggested: `a2a.rpc_error`).
  - 055 CH-6: the two-key workload token Secret, a default proxy key or opt-in.
  - 012 H-3: refuse a route the key does not list, or force the allowed one.
  - 017 H-4: retry a scripted model error. This contradicts ADR-013.
  - 019 H-17: leave `input` out of result events by default; the model-source field name.
  - 059 H-15: the `--trust` flag (P-15).
  - 139 CH-19: the registry and tag of the published default image.
  - 046 A-5: a run-level first-attempt counter.
  - 014 H-7: trace sampling as a Collector policy.
  - 018 H-18: retire it in `backlog.py`. PR 173 proved its last box.
  - 132 CH-13: the `platform-security` review box.
  - Still open from before: the north star, and the primary trace view per environment ([001 DEC-1](docs/planning/issues/001-DEC-1-resolve-open-decisions.md)).
- **Start Docker** for the live Compose and kind tracks.
- **The Mac run:** `make poc06-mac`. It gives the judge's numbers for 028 S-3 and 137 CH-17.
- **Repository settings:** the `SNYK_TOKEN` secret; `poc08-evals` as a required check next to `check`.
- **The T10 probe workload** (PoC-5 criterion 8).
- **Merge every PR.** Auto-merge is refused.

## How to work here

How a wave runs:

1. Start from a clean checkout at `origin/main`. Run `uv sync --all-packages`, then `make progress`.
2. Triage: sort each open box by what it needs. Build only what can be proved offline.
3. Build each piece in a fresh worktree. One piece per worktree, one PR per piece.
4. Review, then fix. The user merges every PR.

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
2026-11-13, main 4d6cf92.

1. Check what changed since 4d6cf92: git log, gh pr list, make progress.
   Report the numbers in two lines.
2. Ask the user for the decisions listed in HANDOVER.md. Build one small
   piece per answer.
3. If Docker runs, take the live Compose track in order. End each run in
   a dated note, and tick only what the note proves.
4. Triage the issues HANDOVER.md lists as not triaged.
5. Update HANDOVER.md and the plan's "In progress" at the end.

One piece per worktree, one PR per piece. The user merges.
```
