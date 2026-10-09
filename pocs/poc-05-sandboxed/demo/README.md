# PoC-5 demo

`demo.sh` shows both lanes on the kind cluster `poc05`: a normal request through each, every probe refused with its allowed control next to it, admission, the code runner, the broker, and where each refusal was logged. It is task T26 (plan `docs/plans/2026-10-02-poc-05-sandboxed.md`, section 7). It writes its record to `demo/<date>-demo-sandboxed.md`.

## Prerequisites

- The cluster is up: `make kind-poc05 ARGS=up`. The demo brings nothing up or down.
- `kind`, `kubectl`, `uv`, `python3`, and `jq` on the path (the same as `run.sh up`).
- No other agent or stack on the Docker VM uses kind (`pocs/poc-05-sandboxed/CLAUDE.md`).

## How to run

```console
pocs/poc-05-sandboxed/demo/demo.sh            # writes demo/<date>-demo-sandboxed.md
pocs/poc-05-sandboxed/demo/demo.sh /tmp/x.md  # or another path
```

It stops non-zero on the first failed step. The last line of a good run is `result: every step ok`. While the in-pod probe suite is not built (T10), it is `result: every step ok; T10 recorded exception`.

## What it does

| Step | What | How |
| ---- | ---- | --- |
| 0 | The cluster `poc05` and both agent deployments exist | `kind get clusters`, `kubectl --context kind-poc05 get` |
| 1, 3 | A normal request through `agent-echo` (sidecar) and `chassis-echo-remote` (remote), before and after the probe | `run.sh request` |
| 2 | The remote probe: H25, H19, H21, H23, H08, H17 | the named tests in `test_poc05_kind_remote_controls.py` and `test_poc05_kind_tool_gateway.py` |
| 4 | The sidecar probe: H05, H07 from the workload; then the in-pod probe suite | `test_poc05_kind_hardreq1.py`, `test_poc05_kind_probe.py` |
| 5 | Admission: every fixture through a server dry run | `test_poc05_kind_admission.py::test_fixture_outcome_matches_its_header` |
| 6 | One `run_python` call on gVisor, with no egress | `test_poc05_kind_code_runner.py::test_code_runs_on_gvisor_with_no_egress` |
| 7 | H11, Kafka with SASL | `test_poc05_kind_hardreq1.py -k kafka` |
| 8 | Where it was logged: `probe.check`, `remote_unauthenticated`, LiteLLM's 401 lines | `run.sh logs`, counted |

Every pytest step runs through `run.sh with-gateway` with `POC05_KIND=1` and prints one line per check: id, attempt, outcome, control. A failed, skipped, or deselected check fails the step. A test file that does not exist yet fails the step by name.

Every check is an existing kind test or a `run.sh` verb; the demo has no probe logic of its own. All captured output goes through `run.sh redact`. The gateway key reaches pytest only through `run.sh with-gateway`: in its environment, never in argv or the record. Every `kubectl` call passes `--context kind-poc05`.

## Recorded

Recorded: 2026-10-09T15:04:28Z on the Mac, cluster `poc05` with the Kafka pass applied, by `POC05_KAFKA=1 UV_NO_SYNC=1 pocs/poc-05-sandboxed/demo/demo.sh`. Exit code 0 in 40 s. The last line is `result: every step ok; T10 recorded exception`. The record is [2026-10-09-demo-sandboxed.md](2026-10-09-demo-sandboxed.md), and `grep -c sk-` on it is 0.

- Load: node 0.28 0.66 2.99 at the start, 0.59 0.69 2.90 at the end (1, 5, 15 min). Mac: 5.02 at the start.
- Steps that passed:
  - Remote probe: 6 passed.
  - Sidecar H05 and H07: 2 passed.
  - Admission: 63 passed.
  - Code runner: 1 passed.
  - H11: 1 passed. With `POC05_KAFKA=1`, a missing broker fails this step instead of skipping it.
- Step 4 prints the T10 exception line in place of the in-pod probe suite: `T10 | - | exception: in-pod probe not built (WIP) | notes/2026-10-09-t10-probe-exception.md`.
- In step 8 the LiteLLM line shows `key=5a84ad5`. That is the start of the sha256 of the test's fake key `NOT_A_KEY`, not a credential.
- Step 8 counts per pod. The remote chassis logs `remote_unauthenticated` to its `memory` telemetry, not stdout, so the demo counts the remote listener's 401 and 403 in its access log instead.

It does not show:
- The in-pod probe suite. T10 is a recorded work-in-progress exception ([notes/2026-10-09-t10-probe-exception.md](../notes/2026-10-09-t10-probe-exception.md)), so `probe.check` is 0 lines.
- H01 on a real cloud. On kind there is no cloud metadata service to refuse, so H01 holds only as a static check.

## What it does not show

- Checks it does not select: H01 to H04, H13, H18, H20, H26, H30, and the code runner's process and file checks. `run.sh test` runs the whole kind tier.
- The gVisor overhead numbers (T23) and the CI run of the remote lane (T24).
- Anything outside kind: a cloud metadata service, a real cloud API server, real models.
- NetworkPolicy drops as log lines: kindnet does not log them. Step 8 counts only what the chassis and LiteLLM log.
- The blind spots in `notes/2026-10-02-blind-spots.md`.
