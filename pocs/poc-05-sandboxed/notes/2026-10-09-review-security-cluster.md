# PoC-5 security review of the cluster work, 2026-10-09

Read-only review by the `platform-security` agent of origin/main 485167e (T19 to T22) and the uncommitted change to `test_poc05_kind_code_runner.py`. Nothing was run on the cluster; the restart cause is inferred, not observed. Result: 1 HIGH, 2 MEDIUM, 4 LOW, no CRITICAL.

## HIGH

**One caller can restart the shared code runner.** `packages/code-runner/src/code_runner/runner.py:64` sets RLIMIT_AS to 256Mi per process, the same as the pod limit; `isolation.py:39` allows 32 processes; `deploy/kind/poc05/tools/code-runner.yaml:63-75` keeps the 1 s default liveness timeout. No per-call bound covers total memory. About 31 Python children reach the pod limit and the probe fails (bring-up note, 2026-10-09). The `/bin/cat` test change keeps the NPROC assertion honest but hides the hostile case.

Recommended now (values `suggested:`): first read `lastState.terminated.reason` (if OOMKilled, the probe change alone does nothing); address space per process 96Mi; process allowance 8; pod memory 384Mi with request equal to limit; liveness `timeoutSeconds: 5`, `failureThreshold: 6`. Add a test that runs about 31 Python children, then asserts the restart count is unchanged and a second call works; `xfail(strict=True)` against 054 H-16 if it does not hold. The full fix is a sandbox per call or per caller (an agent-sandbox warm pool), owned by 054 H-16.

The HTTP 400 is most likely LiteLLM reusing its MCP session after the runner restarted (FastMCP answers 400 for an unknown session). `gateway.py:198-199` maps a bare 400 to non-retryable `tool_unavailable`, the safe default for a write tool. Not a security finding; name the cause in 054.

## MEDIUM

- **Files from one call reach the next caller.** Every call runs as uid 10003, and only the call's own workdir is removed (`runner.py:137,141`). Files elsewhere in `/tmp` and in `/dev/shm` stay. This is the B12 risk inside the code runner. Fix: after each call, delete every entry in the tmp root and `/dev/shm` that did not exist at server start. Test: call A writes `/tmp/x` and `/dev/shm/x`, call B sees neither; control: A reads its own files inside its call.
- **H23 is stated wrong for the remote lane.** The node-side `pids.max` check shows the cap is set. Under gVisor, hitting the cap ends the sandbox rather than returning EAGAIN (blind spots, line 35), so the threat model's expected result for H23 (line 86) does not hold there. Fix: restate H23. Optional destructive test against `remote-echo` only (its own pod): the node stays Ready, agent-echo answers, remote-echo comes back. Owner 055 CH-6 if deferred.

## LOW

- **`/dev/shm` in the remote sandbox** is memory-backed and counts against that pod's own 256Mi; the worst case is the remote taking down its own pod. Fix: an emptyDir (medium Memory, sizeLimit 8Mi) at `/dev/shm` in `remote-echo.yaml` and the code runner, with an ENOSPC test like H22. Owner 055 CH-6.
- **H01 on kind** is acceptable for criterion 5: the static check over the live policies plus H19's refusal of a literal off-cluster IP next to its control. Cheap upgrade: a listener at 169.254.169.254 on the node's loopback so the live half tests something. Cloud rerun: 038 X-1a.
- **`with-gateway`**: no leak path in normal use. Gaps: under `bash -x`, `run.sh:411-414` prints the key (wrap in `{ set +x; } 2>/dev/null`); in CI the key is not a registered secret (emit `::add-mask::` when `GITHUB_ACTIONS` is set). Never add `-v` to `kctl`; never run pytest with `--showlocals`.
- **A refusal that could pass with the control missing**: `REFUSED_TCP` accepts `ConnectionRefusedError` and `OSError` (same three files as the code review). A kindnet drop is a timeout; refused means nothing listens. The H30 targets (Valkey, code runner) have no "target is up" control in the same call. Fix: expect `TimeoutError`, or prove each target answers from an allowed peer. No other false-pass path found.

## Disposition

Decided by the user on 2026-10-09:

- HIGH and the file-leak MEDIUM: build a sandbox per call (an agent-sandbox warm pool) in PoC-5, not only tighter limits. A design comes first, then the cluster work.
- H23: restate it for the remote lane and add the destructive test, run last, against `remote-echo` only.
- LOW: all four go into PoC-5: the `/dev/shm` size cap, `with-gateway` hardening, a live metadata listener for H01, and strict refusal sets (with the code review's matching MEDIUM).

The cluster work runs one agent at a time, after the step A1 verdict runs.

## Review of the per-call sandbox design, 2026-10-09

`platform-security` on `docs/plans/2026-10-09-poc-05-per-call-sandbox.md`: **approve with required changes**. Hard requirement 1 holds: untrusted code runs only in per-call pods with no token, no secret, and no egress; the one new credential, a Kubernetes token, sits in a platform pod like LiteLLM's keys.

Required before build:

1. **Admission must match `SandboxTemplate` and `SandboxClaim`** (design task 4 becomes required). A template without `networkPolicyManagement` gets `Managed`, and the controller writes a NetworkPolicy that allows internet egress (B2); its ClusterRole writes NetworkPolicies cluster-wide. Templates: only `Unmanaged`, `envVarsInjectionPolicy: Disallowed`, `volumeClaimTemplatesPolicy: Disallowed`, plus the `spec.podTemplate` rules. Claims: no `env`, no `additionalPodMetadata`, `warmPoolRef` only `code-runner`, `shutdownPolicy: Delete` with `shutdownTime` set. Kind check: no controller-owned NetworkPolicy in any `poc05-*` namespace.
2. **A compromised dispatcher** cannot pick or change a template or reach another namespace. It can delete other callers' claims (denial of service, recorded). Add `can-i` "no" checks: `update`/`patch` sandboxclaims, `create` sandboxtemplates and sandboxwarmpools, `get` secrets in `poc05-platform`.
3. **The token never reaches a sandbox.** Separate httpx clients for the API (fixed base URL, no redirects, cluster CA verified) and for the sandbox; a size cap on the sandbox response; an offline test where the fake sandbox sees no `Authorization` header.
4. **Tenants and the namespace.** The submitter can create and patch pods in `poc05-tools` (`admission/rbac.yaml:62-73`), so it can fill the shared quota or patch a warm pod. Drop that binding or move the pool to its own namespace; kind checks that the submitter cannot create claims, templates, or pools in any `poc05-*` namespace; grep the with-extensions manifest for `aggregate-to-` labels.

The ipBlock is acceptable. The static test asserts: exactly one `ipBlock` across all PoC-5 policies; it is in `code-runner-dispatch` in `poc05-platform`, whose podSelector selects only the dispatcher; the rule holds only that `/32`, no `except`, ports exactly TCP 6443; the block is outside 169.254.0.0/16 and the pod and service ranges; the file in git holds a sentinel and `run.sh` refuses to apply it unfilled. Kind checks: the live block equals the `kubernetes` EndpointSlice address; the dispatcher reaches node:6443 but not node:10250; H03 and H04 still refuse for the workload, the remote, and a sandbox; H01 still finds nothing covering 169.254.169.254.

Minor: add the dispatcher to `test_poc05_hardening_static.py`; record in the threat model that B4 has one platform exception and B12 is closed for the code runner.

## /dev/shm under gVisor, 2026-10-09

Task 6b found the 8Mi `/dev/shm` emptyDir in the code-runner template is ignored under gVisor: runsc mounts its own tmpfs there (about 4 GiB in `df`), and one call wrote 17 MiB with no error. `platform-security` verdict: **accept and record, owner 055 CH-6.** With a sandbox per call, nothing in `/dev/shm` reaches the next caller, and the pod's 256Mi limit bounds it to that one sandbox. No runsc option to honor the pod's mount is known (the `dev.gvisor.spec.mount.*` hints are a lead to try, not a known fix). Required: `test_dev_shm_is_capped` stays `xfail(strict=True)` with the evidence; a passing test for the bound that holds (the pod's memory limit, and call B not seeing call A's file); a comment on the 8Mi volume that gVisor ignores it; `remote-echo` recorded as untested in the blind-spots note.
