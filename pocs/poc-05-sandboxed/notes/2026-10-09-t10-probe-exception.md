# T10: the probe workload exception for PoC-5 (2026-10-09)

T10 is the in-pod probe workload, `packages/workloads/hostile`. It serves exit criterion 8, "The hostile suites pass in both lanes". PoC-5 records it as an **exception**, not a pass. Criterion 8 closes flagged, as "partly shown".

- **Owner:** the user.
- **Status:** work in progress. Not built.
- **Decided:** by the user, 2026-10-09. This replaces two earlier decisions: the drop on 2026-10-02, and the 2026-10-09 plan to build it in the PoC-5 close-out.
- **How it is tested once built:** [the runbook](../../../docs/guides/poc-05-runbooks.md#testing-the-probe-workload-t10-after-it-is-built), "Testing the probe workload (T10) after it is built".

## What T10 is

A small workload image that runs one check per call from inside a pod and prints the outcome. It runs in both lanes:

- as the sidecar workload, next to a chassis in `poc05-agents`;
- as a remote pod on gVisor, through agent-sandbox, in `poc05-remote`.

Each check observes one control from where a hostile workload would sit: a connect, a file, the env names, a write to the root, a burst of children. It reports what it saw. It does not attack anything. The test on the host decides pass or fail, and pairs each refusal with its allowed control.

The interface is in the runbook. This note does not define the checks' code; the user writes it.

## Why it is an exception

It is not built. No image, no manifest, and no kind test for it exist on `poc-05/cluster` at `1c9685a`. The empty skeleton was removed on 2026-10-08. `demo/demo.sh` prints a `T10 | - | exception: in-pod probe not built (WIP)` line in place of the probe suite.

## What covers criterion 8 meanwhile

The kind suites check each control from outside the probe's place. They `kubectl exec` the image's own Python in our own containers (the `agent-echo` workload, `remote-echo`, the code runner, the dispatcher), and read the live pod spec and the node's cgroup. Every refusal is paired with an allowed control in the same test. Each test was broken once in a temporary copy and failed ([sidecar suite](2026-10-08-sidecar-suite.md), [remote suite](2026-10-08-remote-suite.md)).

| File | What it shows |
| ---- | ------------- |
| `tests/test_poc05_kind_sidecar_controls.py` | From the sidecar workload: H01 (a listener at the metadata address on the node), H02, H03, H04, H13, H25, H26 |
| `tests/test_poc05_kind_remote_controls.py` | From the remote pod and the node: H17, H18, H19, H20, H21, H23 (the cap as set), H28, H30, the one-secret check |
| `tests/test_poc05_kind_hardreq1.py` | Hard requirement 1 from the sidecar workload: LiteLLM (H05), the MCP gateway (H07), Valkey (H10), MinIO (H12), the broker (H11); each next to the same call through the chassis |
| `tests/test_poc05_kind_code_runner.py` | The code runner per call: gVisor, no egress, ingress only from the dispatcher, the child burst, no files across calls, the claim gone after |
| `tests/test_poc05_kind_remote_shm.py` | `/dev/shm` in `remote-echo`, bounded by the pod's memory limit |

The full list per H id is in [the blind-spots note](2026-10-02-blind-spots.md), section 3.

## What the exception allows, and what it does not

- PoC-5 may close with criterion 8 ticked as flagged ("partly shown"), with the files above as its evidence.
- No note, guide, or backlog change may say a workload was probed from inside its pod. The checks show the controls are there. They do not show what a determined workload can do inside them.
- Until T10 lands, controls are checked from outside the pod only: `kubectl exec` of tools already in our images, pod specs, cgroup files.
- No attack tool is written, now or for T10. The probe observes and reports.
- The probe image is never added to `trustedRepositories` (`admission/params.yaml`). A trusted sidecar may not run it outside its test pod.
- 055 CH-6's criterion "a hostile workload in the sandboxed pod cannot ..." stays unticked until the probe runs.

## How it closes

1. The user builds the probe to the interface in the runbook.
2. The runbook passes, step by step: the offline gate, the image, the manifests, the kind suite `tests/test_poc05_kind_probe.py` in both lanes, and the demo re-recorded.
3. Criterion 8 moves from flagged to a plain `[x]` in the README, with the kind suite's run as evidence.
4. This note gets a "Closed" line with the date and the run. The blind-spots note, the threat model (section 7), and `backlog-changes.md` are updated to match.
