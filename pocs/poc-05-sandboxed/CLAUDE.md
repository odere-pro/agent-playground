# pocs/poc-05-sandboxed

Context for this iteration. The root `CLAUDE.md` and `pocs/CLAUDE.md` still apply. Status: planned; the design is written and nothing is built yet.

## Read first

`docs/planning/poc/005-PoC-5-sandboxed.md` (question, exit criteria), then `docs/plans/2026-10-02-poc-05-sandboxed.md` (every decision and the task list; the code wins where they differ), then `notes/2026-10-02-threat-model.md` (the attack ids H01 to H32), then `README.md` here.

## What the iteration touches

- `packages/chassis`: the `remote` connector, `ToolPort` write mode, the remote proxy listener, `spec.trust`.
- `packages/workload-a2a` (the bearer check), `packages/workloads/hostile`, and the new `packages/fake-mcp-server` and `packages/code-runner`.
- `deploy/kind/poc05/` (cluster, gVisor, NetworkPolicy, admission, seed script), `docs/contracts/contract-v4.md`, and ADR-005.
- Here: `tests/`, `demo/`, and `notes/` (`spike/` holds the spike files). Code that outlives the iteration goes in `packages/` or `deploy/`.

## Rules

- Only one agent at a time uses Docker or kind. The VM has 7.75 GiB. Stop other stacks first; never prune or stop another project's container.
- The kind context is `kind-poc05`. Pass it on every `kubectl` call. Never act on another context.
- Tests are `test_poc05_*`, and the docstring names the exit criterion. Kind tests are marked `network` and run only with `POC05_KIND=1`. Everything else runs offline in `make test`.
- A probe check always has a paired allowed control: the same call works where it should.
- No credential in a file, a command line, or a log. Secrets come from the seed script only.

## How to run

Gate: `make test-poc POC=05`. The cluster: `make kind-poc05 ARGS="up"` once the target exists.

## Done means and do not touch

- Done: every exit criterion in `README.md` has evidence (a test name or a command and its output), `make test-poc POC=05` is green, `demo/` holds the run, `notes/backlog-changes.md` exists, and ADR-005 is updated with what the cluster showed.
- Do not touch: another iteration's scope; `README.md` boxes without evidence; the epic; `docs/planning/` outside the close.
- Ask: `chassis-architect` before a contract change, `platform-security` before keys, egress, sandboxing, or images, `eval-expert` for the evaluator gate, `observability-expert` for spans and correlation.
