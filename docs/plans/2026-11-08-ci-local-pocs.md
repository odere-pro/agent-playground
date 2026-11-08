# CI cut: finished PoCs are checked locally

Status: done
Date: 2026-11-08

## Context

The core review (PRs #85 to #100) showed the cost of the test volume. Each commit's `make quick` took 10 to 50 minutes under load. Four PRs at once queued about 300 GitHub jobs, and some waited over an hour for a runner. One PR commit fired both `push` and `pull_request` on 13 workflows, about 57 to 74 jobs. The `check` job ran every test: 7,438 passed in 720 s on `main` at f49d542, a 13 m 04 s step.

The user's rule: a finished PoC is checked with local tests only, not on every push and pull request.

## Decisions (the user, 2026-11-08)

- PoC-6a, 6b, 6c, and 7 count as finished for CI, though their `Status:` lines still say `in progress`. The Makefile names them in `CI_FINISHED_POCS`; no Status line changes.
- No PoC file stays in the CI gate. ADR-001 hard requirement 2 stays in the gate through a package binding of the lane contract over the `remote` lane. Done criteria whose text promises CI on every commit (PoC-3 c1, PoC-5 c1 and c2, PoC-8 c1 and c2, issues 026 and 055) keep their text; their evidence lines say where the tests run since 2026-11-08.
- The kind drills and the agent scan run by hand only (`workflow_dispatch`), not on a schedule.

## Changes

- `Makefile`: `test-gate` (packages, `scripts/tests`, open PoCs), `test-pocs-done`, `gate-paths`; `make check` runs `test-gate`. `make test` still runs everything.
- `packages/chassis/tests/test_lane_contract_remote.py`: `LaneContract` over `inprocess`, `sidecar`, and `remote`.
- `scripts/tests/test_ci_gate_paths.py`: every PoC folder is in the gate or the local list, exactly once.
- Workflows: dispatch only for `remote-lane`, `poc06-kind`, `poc06-load`, `poc06-slm`, `poc07-agent-scan`, `poc07-evals`, `poc07-stack`, `poc08-litellm`, `poc08-stack`. Paths filters on pull requests, and push on `main` only, for `poc08-evals`, `poc09-helm`, `poc09-scaffold`, and the new `images.yml` (moved out of `ci.yml`). `lint-extra` and `typescript` run on pull requests and `main`. `check` is unchanged: every push and every pull request.
- The static tests that pinned the old triggers now pin the new ones. Docs: `docs/guides/testing.md` ("What CI runs"), `docs/guides/local-dev.md`, the root `CLAUDE.md`, PoC READMEs, and the PoC-5 guide.

## Amended 2026-11-08: no duplicate runs

The first PR run still started 34 checks; 26 were the `poc08-evals` and `images` matrices, each job under about a minute. The user: "no duplications, priority is main product, universal agent container".

- `images.yml` is one job that loops over the 11 images; `poc08-evals.yml` is one job (the gate, the regression, the red-team set) that loops over `ENGINES`. A failure names its image or engine.
- `ci.yml` runs on pull requests and pushes to `main` only, not on branch pushes. `typescript` moved to `typescript.yml`, filtered to its folder.
- The gate's package tests are already the product's: chassis, agentctl, and agent-testkit are 274 s of 344 s; the test-only packages take about 20 s and stay.

## Branch protection

The required check stays `check` alone; nothing has to change. A path-filtered workflow must not become a required check. PoC-8's flag (`poc08-evals` as a required check) still waits: it needs an always-run summary job first.
