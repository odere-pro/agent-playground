# pocs/poc-05-sandboxed

Context for this iteration. The root `CLAUDE.md` and `pocs/CLAUDE.md` still apply. Status: done (2026-10-09), criteria 1 (sidecar kind suites not yet shown in CI), 3 (MinIO) and 8 (T10) flagged (`README.md`). The decisions are in `notes/2026-10-02-mac-kickoff.md` and `notes/backlog-changes.md`.

## Read first

`docs/planning/poc/005-PoC-5-sandboxed.md` (question and what is left; the exit criteria are in `README.md`), then `docs/plans/2026-10-02-poc-05-sandboxed.md` (every decision and the task list; the code wins where they differ), then `notes/2026-10-02-threat-model.md` (the check ids H01 to H32), then `notes/2026-10-02-mac-kickoff.md` (decisions and what is left), then `README.md` here.

## What the iteration touches

- `packages/chassis`: the `remote` connector, `ToolPort` write mode, the remote proxy listener, `spec.trust`.
- `packages/workload-a2a` (the bearer check), and the new `packages/fake-mcp-server` and `packages/code-runner`. The probe workload `packages/workloads/hostile` (T10) is not in the tree yet (see Rules).
- `deploy/kind/poc05/` (cluster, gVisor, NetworkPolicy, admission, seed script), `docs/contracts/contract-v4.md`, and ADR-005.
- Here: `tests/`, `demo/`, and `notes/` (`spike/` holds the spike files). Code that outlives the iteration goes in `packages/` or `deploy/`.

## Rules

- Docker and kind: the rules on who may use them and which kind context to pass are in `deploy/CLAUDE.md`.
- Tests are `test_poc05_*`, and the docstring names the exit criterion. Kind tests are marked `network` and run only with `POC05_KIND=1`. Everything else runs offline in `make test`.
- A refusal check always has a paired allowed control: the same call works where it should.
- T10, the in-pod probe workload, is WIP by the user and a recorded exception (`notes/2026-10-09-t10-probe-exception.md`; how to test it: `docs/guides/poc-05-runbooks.md`, "Testing the probe workload (T10) after it is built"). Until it lands, check controls from outside the pod: `kubectl exec` of tools already in our images, pod specs, cgroup files.
- Never write an attack tool.
- No credential in a file, a command line, or a log. Secrets come from the seed script only.

## How to run

Gate: `make test-poc POC=05`. The cluster: `make kind-poc05 ARGS="up"`.

## Done means and do not touch

- Done: every exit criterion in `README.md` has evidence (a test name or a command and its output), `make test-poc POC=05` is green, `demo/` holds the run, `notes/backlog-changes.md` exists, and ADR-005 is updated with what the cluster showed.
- Do not touch: another iteration's scope; `README.md` boxes without evidence; the epic; `docs/planning/` outside the close.
- Ask: `chassis-architect` before a contract change, `platform-security` before keys, egress, sandboxing, or images, `eval-expert` for the evaluator gate, `observability-expert` for spans and correlation.
