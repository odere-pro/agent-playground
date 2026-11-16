# Does a scratch `/tmp` meet the 057 H-19 disk check? 2026-11-16

Writer: `chassis-architect`, revised after the `platform-security` review. Read against `main` at `71db2e8`. A note for the user's decision, not a decision.

## Question

057 H-19, box 3 (`docs/planning/issues/057-H-19-stateless-check-ci.md:49-52`): "The template's workload for each supported framework passes." Its `sidecar` sub-bullet says: "`smolagents` passes only with a scratch `/tmp` (the pod's emptyDir) that must be empty after each call, because `filelock` makes and removes a temp folder when it is imported. That is not "no writable volumes"." It ends: "Also open: a decision on whether a scratch `/tmp` that must be empty after each call meets the disk check."

## Recommendation

The two engines that need it, `smolagents` and `claude-agent`, are untrusted and run only in `remote` ([ADR-006](../planning/adr/006-agent-engines-default-supported-lanes.md) lines 80, 81, 84; `packages/agentctl/src/agentctl/rules.py:32`; `spec.trust` refuses them in any other lane). The `sidecar` lane check uses a scratch folder only as a CI stand-in. In `remote`, a writable `/tmp` meets the disk check only with four things. First, one call in flight per pod (`spec.limits.max_in_flight: 1`, suggested) now; the interim fix is in progress on branch `fix/untrusted-one-in-flight-1116`. Second, a pod per call later (an ADR, the user's decision). Third, the cap shown on kind. Fourth, the check fails on leftovers at start and after each call.

The CI check allows writes there only if the template declares it. In a pod every container's `/tmp` is writable.

## Why

**Where "no writable volumes" comes from.** Only the 057 issue says it (`057:19`): "the contract tests run with a read-only root file system and no writable volumes". The epic and ADR-001 say less:

- Epic H-19 (`slm-agent-platform-epic-v3.md:349`): "the build fails if an agent writes to local disk or keeps data between calls."
- Epic B.2 (`:632`): "nothing is kept in memory or on disk between calls. Any pod can serve any request".
- ADR-001 forces (`001-chassis-delivery-model.md:46,48`): untrusted code "must not reach ... the disk"; "Stateless. It scales by adding replicas."

The epic's rule is about what outlives a call. A memory-backed `/tmp` that is empty after each call keeps nothing across calls, and nothing outlives the pod. But for untrusted code, "across calls" includes two calls in flight at once (see Security).

**Every pod already has the volume.** The chart mounts a memory-backed, capped `/tmp` on every container (`deploy/helm/agent-lib/templates/_deployment.tpl:73-86`, remote `:126-131`; caps 16Mi and 32Mi, `deploy/helm/README.md:98`, suggested). The remote pod's `/tmp` is writable, and a test shows it: `pocs/poc-05-sandboxed/tests/test_poc05_kind_remote_controls.py::test_h21_root_is_read_only_and_tmp_is_writable` (`:464`). Under gVisor, runsc mounts its own tmpfs on `/tmp` even with a read-only root (`pocs/poc-05-sandboxed/notes/2026-10-02-kind-sandbox-spike.md:179,205`). So "no writable volumes" can never hold in `remote`. The cap on `/tmp` under gVisor is shown only in the manifest (`2026-10-02-blind-spots.md:62`). On `/dev/shm`, runsc ignored the pod's cap (`:42-43`).

**The CI check exists.** `agent_testkit.stateless_lane` has `scratch_var` (`stateless_lane.py:57-59`). It gives each pod a fresh folder, points `TMPDIR` at it, and allows writes only there (`:138-143`). After each call it polls for up to 2 s (`SCRATCH_SETTLE_S`, suggested, `:42-43`). If a file is left, it raises `ScratchLeftover` (`:107-129`). The template turns it on only for `smolagents` and `claude-agent` (`test_stateless_lane.py.jinja:26-42`). The other five keep "No writable volume: any write fails" (`:39`).

**What the template run shows for `smolagents`.** In the template run, with the fake model, it writes only at import. Model-written code can write to `/tmp`. `filelock` 4.1.0 `_strict.py:77` makes a `TemporaryDirectory` to probe `os.link` and removes it. On `OSError` it returns `False`.

**Gaps in the check:**

1. The scratch is not checked after start (`stateless_lane.py:154` runs `check_disk` only). Add `check_scratch` there.
2. An emptyDir outlives a container restart (`_containers.tpl:99`, "the kept emptyDir"). The workload should empty its scratch at start (suggested).
3. The audit hook misses writes by a non-Python child outside the scratch (`readonly_root.py:17-18`), such as the Claude CLI. Only kind proves those.
4. The live proof for "nothing crosses calls" should copy `test_poc05_kind_code_runner.py::test_files_do_not_cross_calls` (`:486`). The in-pod hostile probe (T10) is still an open exception (`pocs/poc-05-sandboxed/notes/2026-10-09-t10-probe-exception.md`).

## Lanes and contracts

No contract changes: not the envelope, the event schema (no `schema_version` bump), `handle`, the ports, the A2A mapping. `spec.limits.max_in_flight` exists already, and only its value changes. `inprocess` and `sidecar` are unaffected: no untrusted engine runs there. `remote` carries the cost: one call per pod caps the throughput of each remote at one, and a pod per call adds a pod start to each call.

## Security: leaks between calls

"Empty after each call" stops leaks from one call to the next. It does not stop leaks between two calls in flight in one pod. Every call runs as uid 10002, and `mkdtemp` mode 0700 (`echo_claude_agent/handle.py:307-310`) does not keep out the same uid. The `claude-agent` transcript "holds the prompt and every tool result" (`echo-claude-agent/README.md:59`). Its `Bash` tool and the `smolagents` executor run model-written code. A process left running by one call can read later calls too. PoC-5 found this in the code runner (`2026-10-02-review-code.md:34`) and fixed it with a fresh pod per call.

`chassis.server` caps runs per replica: `spec.limits.max_in_flight`, default 32, min 1 (`packages/chassis/src/chassis/server/config.py:281-298`, `server/pipeline.py:339-350`). The remote lane is one replica (`_validate.tpl:244-249`, `_deployment.tpl:105`). So `max_in_flight: 1` gives one call per remote pod at a time. On `main`, nothing sets it.

**HIGH.** `claude-agent` passes the per-run secret and the bearer token to the CLI in its environment (`handle.py:126-136`, `ANTHROPIC_CUSTOM_HEADERS` and `ANTHROPIC_AUTH_TOKEN`). A second run's `Bash` tool, same uid 10002, can read them from `/proc/<pid>/environ` and call the proxies as the first run. That undoes the fix [ADR-011](../planning/adr/011-per-run-secret-and-v0-wire-bounds.md) decision 1 makes for R2-02 (`:18`, `:58-61`).

Recommendation:

- **Now:** `max_in_flight: 1` for untrusted agents (suggested; owner 055 CH-6; no ADR). The interim fix is in progress on branch `fix/untrusted-one-in-flight-1116`, not merged, with no PR yet. It is planned to make 1 the default for untrusted agents and refuse a higher value, in the chassis and in agent-lib's remote lane. No commit is on the branch yet. A `platform-security` review comes before its PR. It closes the HIGH while one call is in flight. A process left running by a call stays open until the next step.
- **Then, the user's decision:** a pod per call for untrusted engines, as an ADR amending [ADR-005](../planning/adr/005-remote-lane-auth-and-trust-admission.md). The ADR also weighs sending the `smolagents` executor to the code runner behind `ToolPort`.
- **Reject** a uid per call. It needs `CAP_SETUID`, which conflicts with dropping all capabilities and `allowPrivilegeEscalation: false` (`_helpers.tpl:42-50`). It also does not cover `smolagents`, whose code runs in the workload process.

## What to write down

The issue is not edited here. Suggested text, for whoever edits it after the review:

- `057:19`: "Disk check: the contract tests run with a read-only root file system for both the chassis and the workload container. In the workload container, the only writable path is its own scratch `/tmp`: an emptyDir, memory-backed, size-capped, used only when the template declares it. It must be empty after start and after every call. Any other write fails the build. The chassis's `/tmp` holds only its ready marker (`/tmp/chassis-ready`, ADR-012)." The rule binds the workload's `/tmp` only. The chassis writes its marker there (`_containers.tpl:39-41`, `chassis/ready_marker.py`), so without this the check fails every chassis.
- Box 3, `057:51`, the last two sentences become: "`smolagents` passes with a scratch `/tmp` that is empty after each call (`filelock` makes and removes a temp folder at import). That stands in, in CI, for the remote pod's `/tmp` ([note](../../reviews/2026-11-16-scratch-tmp-stateless.md))."
- `057:52`: remove "Also open: a decision on whether a scratch `/tmp` ... meets the disk check." Add: "Open: the scratch check after start; `max_in_flight: 1` for untrusted agents (055 CH-6; branch `fix/untrusted-one-in-flight-1116`, in progress); the pod-per-call ADR (the user's decision); the remote lane on kind (`/tmp` empty after each call, the cap holds, nothing crosses calls)."
