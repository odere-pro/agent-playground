# Parallel PoC work: structure, worktrees, harness

Status: in progress

Approved 2026-10-09. First slice on branch `chassis/harness-skills`: the parts that do not wait for PoC-5 (worktree-safe `make setup`, `git-flow` worktree section and no AI trailer, `record-measurement` and `demo-record` skills, the `packages/chassis/CLAUDE.md` split). PoC-5 close (WS0) is owned by its own session. WS1, WS2 and the rest of WS3 and WS4 follow after PoC-5 merges.


## Context

PoC-1 to PoC-4 are closed. PoC-5 has passing kind evidence in `notes/` but 0/25 boxes ticked and no demo.
The plan in `docs/planning/poc/000-plan.md` allows 6a to start now, 6b after 5, then 7 → 8 → 9 strictly in sequence.
So the next weeks have the first real parallelism: closing PoC-5, starting 6a, and preparing 6b.

The repo is not built for that. Exploration found:

- **One-value state.** `pocs/CURRENT` is read by `.claude/hooks/session_start.sh`, `scripts/harness_lint.py:90-92`, and the `poc-iteration` skill.
- **Shared files every PoC edits.** These are `Makefile` (`.PHONY`, `kind-pocNN`, `CASSETTE_TESTS`) and `pyproject.toml` (members, mypy excludes per PoC conftest, markers naming `POC04_*`/`POC05_*`). Others are the root `CLAUDE.md` map, the `ci.yml` images matrix, and `.secrets.baseline`. Two PoC branches will conflict here.
- **Test harness chain through `sys.path`.** PoC-3 imports PoC-2 helpers. PoC-4 and PoC-5 conftests push earlier PoC test folders onto `sys.path`. That is about 2,000 lines of reusable harness in PoC folders (`poc02_harness.py` is 751 lines). This breaks the "code that outlives the iteration goes to `packages/`" rule.
- **Basename collisions.** Test folders have no `__init__.py`. PoC-4 uses bare names (`test_kind.py`).
- **Inconsistent deploy layout.** PoC-4 kind sits at the `deploy/kind/` root (`run.sh`, `cluster.yaml`, cluster `poc04`). PoC-5 has `deploy/kind/poc05/`.
- **Worktree blockers.**
  - `make setup` writes `.git/hooks/pre-commit`, which fails in a worktree.
  - Kind cluster names (`poc04`, `poc05`) and image tags (`:poc04`, `:poc05`) are fixed.
  - Host ports 4000/8080/8081/18080/18081 are fixed, and so are the compose project names.
  - Docker VM memory is about 7.75 GiB. PoC-5 alone uses about 3.3 GiB.
- **Harness gaps.**
  - The cluster skill is PoC-5-specific (`poc-05-operate`).
  - No skill covers worktrees, closing a PoC, recording measurements, or demos.
  - No agent owns infrastructure.
  - No lock enforces "one agent on Docker/kind".
  - `git-flow` has a stale Co-Authored-By trailer, which conflicts with the user's no-attribution rule.
  - `packages/chassis/CLAUDE.md` is 19.5 KB.
  - The root CLAUDE.md skill list misses `poc-05-operate`.

Goal: each PoC becomes a self-describing folder that shared files discover by glob. Worktrees run side by side with no collisions. The harness has skills for the workflows we repeat.

## Recommended approach (5 workstreams, ordered)

### WS0 — Close PoC-5 first (no restructure on this branch)
- Commit the in-flight work: the notes, the kind test, `contract-v4.md`, `.claude/skills/poc-05-operate/`, `docs/guides/poc-05-runbooks.md`.
- Tick the README boxes, linking each one to the `notes/2026-10-08-*-suite.md` evidence.
- Record the demo, and run teardown once.
- Use the existing `poc-iteration` skill and the `poc-05-operate` skill.
- Reason: restructuring under an open PoC mixes two kinds of change in one diff.

### WS1 — PoC folder as the unit (structure)

Target layout, which only extends the current layout:
```
pocs/poc-NN-slug/
  README.md            # adds a line "Status: open|closed|planned" (replaces pocs/CURRENT)
  CLAUDE.md
  poc.mk               # make targets for this PoC only: kind-up/down, demo, load
  tests/               # files prefixed test_pocNN_*; conftest uses the shared plugin
  deploy/ -> moved?    # NO: keep deploy/ central, but one subfolder per PoC
  demo/  notes/
deploy/kind/pocNN/     # PoC-4 moves from deploy/kind/{run.sh,cluster.yaml} into deploy/kind/poc04/
deploy/kind/lib.sh     # shared: cluster name, image tag, ports derived from env (see WS2)
packages/poc-harness/  # NEW: the PoC-2/3/4 harness code promoted out of pocs/*/tests
```
Steps:
1. **Retire `pocs/CURRENT`.** Add `Status:` to every PoC README. Update `harness_lint.py` (allow several `open`), `session_start.sh` (list the open PoCs and the current branch), and the `poc-iteration` skill.
2. **Add a Makefile fragment per PoC.** The root `Makefile` gets `-include pocs/*/poc.mk`. Move `kind-poc04`, `kind-poc05`, `load-test`, and the `CASSETTE_TESTS` list into the matching `poc.mk`. Keep the old target names as aliases, so `make kind-poc05` still works.
3. **Promote the harness.**
   - Create `packages/poc-harness` as a workspace member.
   - Move in `poc02_harness.py`, `poc03_harness.py`, and the reusable parts of `poc04_conftest.py`/`poc05_conftest.py`.
   - Expose a pytest plugin. It holds env gating as one `@requires_env("POC05_KIND")` style marker, which replaces the markers named after each PoC.
   - Remove every `sys.path` insert.
   - Drop the per-PoC mypy excludes.
4. **Rename tests.** Prefix PoC-1 to PoC-4 test files `test_pocNN_*`. Add a `harness-lint` check that every test basename is unique.
5. **Make deploy consistent.** Move PoC-4 kind into `deploy/kind/poc04/`. Keep a thin `deploy/kind/run.sh` that dispatches by PoC. `deploy/helm/` stays for PoC-9.
6. **Discover CI by glob.** Build the `ci.yml` images matrix from a manifest file (`deploy/images.yaml`), not from inline entries. Add path filters, so a docs-only or one-PoC change skips unrelated jobs. `make check` stays the gate.

### WS2 — Parallel execution (worktrees + shared Docker)
1. **Fix `make setup` for worktrees.** Install the hook into `$(git rev-parse --git-common-dir)/hooks`.
2. **Add a namespace per worktree.** `deploy/kind/lib.sh` and the compose files read `AO_NS`, which defaults to empty, so today's names stay the same.
   - Cluster `poc05${AO_NS:+-$AO_NS}`.
   - Image tag `:poc05${AO_NS}`.
   - Compose project name.
   - Host-port offset `AO_PORT_BASE`.
   - Chassis test ports already use 0 or Unix sockets. Fix the one fixed port in `packages/chassis/tests/test_server_cli.py` (8090).
3. **Add a Docker/kind slot lock.**
   - `scripts/infra_lock.sh` takes a lock in `$(git rev-parse --git-common-dir)/ao-infra.lock`.
   - `poc.mk` cluster targets wrap their work in it.
   - Default: one slot, because the 7.75 GiB VM limit makes this a hard rule, not a convention.
4. **Write the parallel model down.** Add it to `docs/guides/local-dev.md`.
   - Offline work (`make quick`, `make test`, `test-poc`) runs in any number of worktrees.
   - Infra work (kind, compose stacks, load, integration) runs one worktree at a time through the lock.
   - Branches stay `poc-NN/<topic>`. Each worktree lives at `../agent-orchestration.wt/<branch>`.
   - Shared-file edits (pyproject members, uv.lock) land on a small `chassis/<topic>` PR first, and PoC branches rebase onto it.

### WS3 — Harness: skills, agents, hooks
New skills, each a `.claude/skills/<name>/SKILL.md` that cites existing make targets:
- `worktree-parallel`: add, setup, and namespace a worktree, take the lock, clean up. It wraps the `EnterWorktree` tool and `git worktree`.
- `kind-operate`: generalizes `poc-05-operate` to any PoC through `deploy/kind/pocNN/` and `lib.sh`. Covers up, smoke, test, teardown, the memory preflight, and links to the runbooks. `poc-05-operate` becomes a thin pointer, or is deleted after PoC-5 closes.
- `poc-close`: evidence sweep (each ticked box links to a test or note), demo, `backlog-changes.md`, ADR update, status flip. Split out of `poc-iteration`.
- `record-measurement`: a dated `notes/` file with the command, environment (commit, machine, cluster), and the raw output tail.
- `demo-record`: one demo shape per PoC (`demo/run.sh` + `demo/README.md`). Replaces the four styles in use today.
- `cross-poc-change`: a chassis change that touches several PoC suites. Covers which `test-poc` runs to execute and how to update the contract version.

Agents:
- Add `infra-operator`. Tools: Bash, Read, Grep. It owns kind, docker, and kubectl, and must use the `kind-operate` skill and the lock. `developer` and `tester` hand cluster steps to it.
- Add a `poc-lead` (orchestrator). It reads a PoC README, splits the open boxes into independent tasks, and assigns each to `developer`/`tester` in its own worktree (`isolation: worktree`). It gathers results and runs `reviewer`.

Hooks and settings:
- A PreToolUse hook on Bash matching `kind |kubectl |docker (build|run)`. It blocks when the infra lock is held by another worktree. It also blocks `kubectl` without `--context`.
- SessionStart prints the worktree path, branch, open PoCs, running kind clusters, and the lock holder.
- Permissions: allow `git worktree *`, `kind get *`, `kubectl --context kind-poc* get *`.

Fixes:
- Remove the stale Co-Authored-By trailer from the `git-flow` skill. Add a worktree section.
- Root CLAUDE.md: list all skills and agents, and say "pocs with Status: open".
- Shrink `packages/chassis/CLAUDE.md` to about 60 lines. Move the API detail into `docs/guides/chassis-reference.md`.
- Trim the duplicated "current design direction" in `docs/planning/CLAUDE.md`.
- Update stale lines in `pocs/poc-05-sandboxed/CLAUDE.md`.

### WS4 — harness-lint guards (keep it true)
Extend `scripts/harness_lint.py` so drift fails `make check`:
- Every skill and agent on disk is listed in the root CLAUDE.md, and the reverse.
- Every repo path cited in a CLAUDE.md exists.
- Size cap per CLAUDE.md (suggested: 8 KB).
- Test basenames are unique across `packages/` and `pocs/`.
- A PoC with `Status: closed` has no unchecked boxes, and each checked exit criterion links to a `notes/` or `tests/` path.
- Each `poc.mk` defines only targets prefixed with its PoC.

### Planning link (optional, small)
Add an optional `poc` field to the `ROWS` in `docs/planning/tools/backlog.py`, so the PoC-to-issue mapping is data, not prose. Run `make planning-sync planning-check`. Use the `planning-sync` skill.

## Sequencing and parallel lanes

| Order | Branch | Depends on | Parallel with |
|---|---|---|---|
| 1 | `poc-05/close` (WS0) | — | WS3 skills drafting |
| 2 | `chassis/poc-unit` (WS1 steps 1–2, 4–5) | WS0 merged | `chassis/harness-skills` |
| 3 | `chassis/poc-harness-pkg` (WS1 step 3) | 2 | WS2 |
| 4 | `chassis/parallel-infra` (WS2) | 2 | 3 |
| 5 | `chassis/harness-skills` (WS3 + WS4) | 2 for paths | 3, 4 |
| 6 | `poc-06a/...` opens | 2 | 3–5, 6b prep |

Every step is a separate PR into `main`, so each diff stays reviewable. Each PR goes through `developer` → `make quick` → `reviewer`. Doc changes go through `docs-editor`.

## Critical files
- `Makefile`, `pyproject.toml`, `scripts/harness_lint.py`, `.claude/hooks/session_start.sh`, `.claude/settings.json`
- `pocs/CURRENT` (removed), `pocs/*/README.md`, `pocs/poc-0{2,3,4,5}-*/tests/*harness*.py`, `*conftest*.py`
- `deploy/kind/run.sh`, `deploy/kind/cluster.yaml`, `deploy/kind/poc05/run.sh`, `deploy/kind/poc05/cluster.yaml`, `deploy/compose/*.yaml`
- `.github/workflows/ci.yml`
- `.claude/skills/{poc-iteration,git-flow,poc-05-operate}/SKILL.md`, `.claude/agents/*.md`
- Root `CLAUDE.md`, `packages/chassis/CLAUDE.md`, `docs/guides/local-dev.md`

## Verification
- After each PR: `make quick` and `make check` pass, and the output tail goes in the PR.
- WS1: `make test` collects the same test count before and after the harness move, and `pytest --collect-only -q | tail -1` matches. `grep -r "sys.path" pocs/` is empty. `make test-poc POC=02..05` passes.
- WS1/WS2 alias check: `make kind-poc05 ARGS="up"`, then the PoC-5 kind suite with `POC05_KIND=1`, then teardown. Done once, through the lock.
- WS2 parallelism:
  - Create two worktrees. Run `make setup && make quick` in both at the same time, and both pass.
  - Start `make kind-poc05` in worktree A. The same command in worktree B blocks with a clear lock message.
  - With `AO_NS=b` and the lock released, B's cluster is named `poc05-b` and does not touch A's images.
- WS4: write a fake drift (an unlisted skill, a duplicate test basename). `make harness-lint` fails with a clear message. Revert the drift.
