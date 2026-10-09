# PoC-5 runbooks

Status: first draft, 2026-10-09 (T27). Per-call code-runner sections added 2026-10-09 (per-call plan, task 7). One section per problem: symptom, check, cause, fix. Every command was run on the Mac and is pasted in a note, unless it says "not yet run". Notes: [bring-up](../../pocs/poc-05-sandboxed/notes/2026-10-02-bring-up.md), [sidecar suite](../../pocs/poc-05-sandboxed/notes/2026-10-08-sidecar-suite.md), [remote suite](../../pocs/poc-05-sandboxed/notes/2026-10-08-remote-suite.md). The day-to-day steps are in the skill `poc-05-operate`.

Every `kubectl` call pins `--context kind-poc05`. No secret value goes in a command line, a file, or this guide.

## The cluster will not start

**Symptom.** `deploy/kind/poc05/run.sh up` stops before `up done`, or a platform pod never gets Ready.

**Check.**

```
$ deploy/kind/poc05/run.sh up
[kind-poc05] Docker memory: 7934 MiB total, 7934 MiB not used by running containers
$ kubectl --context kind-poc05 get pods -A
$ kubectl --context kind-poc05 apply -k deploy/kind/poc05/platform --dry-run=server
```

Read the pod's `lastState.terminated.reason` for an OOM kill.

**Cause, one of:**

- Not enough Docker memory. The preflight stops under 3 GiB free and warns under 6 GiB. It does not count the `poc05` node itself, so a second `up` does not refuse itself. The stack needs about 3.3 GiB working set on the node (bring-up, item 10).
- The PoC-4 cluster `poc04` still runs. `run.sh` refuses to create while it does.
- `minio-init` OOM-killed: exit 137, `OOMKilled`, about one second after start, no output. It happened at 64Mi and once at 128Mi. The limit is now 192Mi (`platform/minio.yaml`).
- `field is immutable` on a changed Job. A Job's template cannot change. If `minio-init` is still within its 600 s TTL, `apply` of an edited manifest fails with `The Job "minio-init" is invalid: spec.template: ... field is immutable`.

**Fix.**

- Memory: stop a container you own, then rerun `up`. Never stop another stack.
- `poc04`: the refusal says `the PoC-4 cluster poc04 is running; delete it first: deploy/kind/run.sh delete`. Do that, then rerun `up`. Not yet run on this branch.
- `minio-init`: rerun `up`. A one-shot Job passes on its restart. If it is killed every time, raise the limit in `platform/minio.yaml`.
- Immutable Job: `run.sh` now handles it. `replace_changed_job` deletes a finished `minio-init` whose manifest changed, before the platform is applied. The line is `job minio-init: finished and its manifest changed; deleting it so apply makes it again`. A running or unchanged Job is left alone.

## A sandbox pod is not Ready

**Symptom.** `remote-echo` (in `poc05-remote`) is missing, Pending, or not Ready. `up` waits on it and stops. For the code-runner pool, see "The code-runner pool is not settled".

**Check.**

```
$ deploy/kind/poc05/run.sh pods
$ deploy/kind/poc05/run.sh smoke
$ kubectl --context kind-poc05 -n poc05-remote get pod remote-echo -o jsonpath='{.spec.runtimeClassName} {.status.podIP}'
gvisor 10.244.0.20
$ deploy/kind/poc05/platform/seed.sh status
```

`smoke` must show `PASS  runsc pod shows the gVisor marker`, `PASS  agent-sandbox controller is Ready`, and `PASS  a Sandbox with runtimeClassName gvisor runs`. `seed.sh status` lists names only; `remote-echo-token` must be in both `poc05-agents` and `poc05-remote`.

**Cause, one of:**

- The `runsc` handler is missing in the node (the gVisor smoke line fails).
- The agent-sandbox controller is not running (its smoke line fails).
- Admission or Pod Security refused the pod. The Sandbox exists but no pod does. See "Admission rejects a deploy".
- The image is not in the node. Every pod outside the remote lane sets `imagePullPolicy: Never`, so a missing `load` means no start.
- The token Secret `remote-echo-token` is missing.
- A restart under load. Under gVisor a sandbox that hits its PID cap or its memory limit is killed whole and the pod restarts (`SandboxChanged`), instead of one call failing ([spike](../../pocs/poc-05-sandboxed/notes/2026-10-02-kind-sandbox-spike.md), question 6).

**Fix.**

- Handler or controller: `deploy/kind/poc05/run.sh create smoke`, then `up`.
- Image: `deploy/kind/poc05/run.sh build load`, then delete the pod as the deployer. The Sandbox controller makes it again (`sandbox.agents.x-k8s.io/remote-echo condition met`).
- Token: `deploy/kind/poc05/run.sh seed`. It keeps every Secret that exists and makes the missing ones.

## A refusal test passes when it should fail

**Symptom.** A hostile test refuses as expected, but you suspect the control is not there (a mutation, a manifest change, or a review says so).

**Check.** First the paired allowed control in the same test. Every PoC-5 kind test asserts one. If the control failed, the refusal proves nothing. Then run the test on its own:

```
$ POC05_KIND=1 uv run pytest -m network pocs/poc-05-sandboxed/tests/test_poc05_kind_sidecar_controls.py -q -rs
6 passed in 12.80s
```

To prove the test can fail, break it once in a temporary copy and run it. Each kind test was broken this way and failed every time (sidecar and remote suite notes, "Mutation check"). Delete the copy after.

**Cause, one of:**

- The control is missing and something else refused. Example: a connection times out because nothing listens, not because a policy drops it. That is H01 on kind: nothing answers at 169.254.169.254, so the live refusal proves nothing.
- DNS, not policy. A failed name lookup can pass with egress still open ([threat model](../../pocs/poc-05-sandboxed/notes/2026-10-02-threat-model.md), section 6). H19 uses a literal address (1.1.1.1) for this reason. Its control: a pod in `default` reaches the same address.
- The wrong runtime. Under gVisor a write to `/` says `Permission denied` (EACCES), not `Read-only file system`. H21 writes to `/var/tmp` (mode 1777), where only the read-only mount can refuse (EROFS).
- A timeout counted as a refusal. A slow node can time out an allowed call too. The paired control catches it: it fails in the same test.

**Fix.** Restore the control (the manifest, the policy, or the runtime). Rerun the test. Record what happened in a dated note. Never edit the test to pass, and never loosen an assertion.

## Kind tests time out under host CPU load

**Symptom.** Kind tests that passed before now time out. On 2026-10-09 the code runner's fork test (`test_poc05_kind_code_runner.py::test_process_limit_refuses_forks_past_the_allowance`) did so while another program on the Mac used about 300 % CPU.

**Check.** Whether something else on the host is busy. Then the pod's restarts:

```
$ kubectl --context kind-poc05 get pods -A
$ docker stats --no-stream --format '{{.Name}} {{.MemUsage}}' poc05-control-plane
```

A host CPU check (`top` on the Mac) is not yet run in any note.

**Cause.** The kind node shares the Mac's CPUs. Under gVisor each forked Python child costs 5 to 8 MiB, and 31 of them reach the code runner's 256Mi limit. Near the limit each fork takes seconds. With the CPU busy, the test runs past its timeout. Since the per-call change a burst ends only its own sandbox, not the dispatcher. At load 4.7 the call after the burst got `sandbox_lost` once in 3 runs; the cause is not found (remote suite, 2026-10-09).

**Fix.** Stop the other load, or wait for it. Rerun the one file. Do not raise a test timeout to hide the load. The fork test now execs `cat` in each child, so a child costs less (the change in that test file).

## The code-runner pool is not settled

**Symptom.** `up` waits on the pool and stops. Or a code-runner kind test fails at its allowed control: the dispatcher's connect to a warm pod times out.

**Check.**

```
$ kubectl --context kind-poc05 -n poc05-tools get sandboxtemplate,sandboxwarmpool,sandboxclaim
NAME                                                     READY   DESIRED   AGE
sandboxwarmpool.extensions.agents.x-k8s.io/code-runner   2       2         45s
$ kubectl --context kind-poc05 -n poc05-tools get pods -L agents.x-k8s.io/claim-uid
```

Settled means: no claim, and every Running pod is Ready and has no `agents.x-k8s.io/claim-uid` label. That is what `warm_pool_ready()` in `poc05_kind.py` waits for.

**Cause, one of:**

- A claimed pod from the previous call is still being torn down. It shows phase `Failed` (`Error`) for a moment: the server exits non-zero on SIGTERM. The pool starts its replacement at the same time.
- A cold start under host load. A cold claim took 4.45 s at load 3 (bring-up, 2026-10-09).
- The controller runs without its extensions, or admission refused the template (`template rule T1` to `T5`). The pool then makes no pod.
- The quota is full: 4 claims or 6 pods in `poc05-tools`.

**Fix.** Wait for the pool to settle, then rerun the one file. A deleted warm pod comes back under the same `Sandbox` name in about 5 s (bring-up, 2026-10-09). For admission, see "Admission rejects a deploy". For the quota, see "A claim is stuck". Not yet run: a warm pod whose container crashes.

## A claim is stuck

**Symptom.** A `SandboxClaim` stays in `poc05-tools` after its call ended, or stays `Ready False`. Calls get `sandbox_unavailable`.

**Check.**

```
$ kubectl --context kind-poc05 -n poc05-tools get sandboxclaim
No resources found in poc05-tools namespace.
$ kubectl --context kind-poc05 -n poc05-tools get sandboxclaim <name> -o jsonpath='{.status.conditions}'
$ kubectl --context kind-poc05 -n poc05-tools get resourcequota code-runner -o jsonpath='{.status.used}'
{"count/sandboxclaims.extensions.agents.x-k8s.io":"0","limits.memory":"512Mi","pods":"2"}
```

**Cause, one of:**

- The dispatcher's delete failed or the dispatcher restarted mid-call. It logs a failed delete as a count. The claim's `shutdownTime` (60 s after create, `suggested:`) removes it.
- Admission refused the controller's own update. On 2026-10-09 rule C2 did: the claim showed `ReconcilerError` with `claim rule C2`. Fixed: C2 admits the empty `additionalPodMetadata: {}` the controller writes (bring-up, 2026-10-09).
- The quota is full. A fifth claim gets `exceeded quota: code-runner`.

**Fix.** Wait for `shutdownTime`. If the claim has none, or you cannot wait, delete it as the cluster admin (the plain `kind-poc05` context). The deployer has no rights on claims.

```
$ kubectl --context kind-poc05 -n poc05-tools delete sandboxclaim <name> --wait=false
```

The claim and its `Sandbox` go in about 0.1 s, the pod in about 2 s (bring-up, 2026-10-09). For a `ReconcilerError`, read the message and fix the rule or the claim, never the controller.

## A code-runner call ends with `sandbox_lost` or `sandbox_unavailable`

**Symptom.** A `run_python` call returns a tool error whose text starts `sandbox_unavailable` or `sandbox_lost`. The chassis passes it on as `ToolResult(is_error=True)`.

**Check.**

```
$ kubectl --context kind-poc05 -n poc05-platform get pods -l app.kubernetes.io/name=code-runner-dispatch \
    -o jsonpath='{.items[0].status.containerStatuses[0].restartCount}'
0
$ kubectl --context kind-poc05 -n poc05-tools get events --sort-by=.lastTimestamp
```

Not yet run as a recipe: a watch on claims and pods while the failing call runs. The bring-up note, 2026-10-09, has the watch output of a good call.

**Cause.** From `packages/code-runner/README.md`:

- `sandbox_unavailable`: no free slot, the claim was not created, it was not `Ready` within 20 s, or its pod IP is not an IP. Nothing ran. A retry with the same key is safe.
- `sandbox_lost`: the pod ended mid-call, did not answer within `timeout_s + 5` s, or answered over 256 KiB. The key is freed, so a retry runs in a fresh sandbox.

**Fix.** Retry the call; both codes are safe to retry. If `sandbox_unavailable` repeats, see "The code-runner pool is not settled" and "A claim is stuck". If `sandbox_lost` repeats, read the claimed pod's `lastState.terminated.reason` before it is gone. One `sandbox_lost` after a 31-child burst, at load 4.7, is open (remote suite, 2026-10-09). Do not loosen the test.

## Rotating the remote token

**Symptom.** The remote token must change: it leaked, or it is due.

**Check.** Which Secrets exist, names only:

```
$ deploy/kind/poc05/platform/seed.sh status
```

**Cause.** The chassis and the remote share one token, `remote-echo-token`, one copy in `poc05-agents` and one in `poc05-remote`. Each side reads it at start.

**Fix.** The three steps from `packages/workload-a2a/README.md`, "Remote lane: bearer token and rotation". Old token `O`, new token `N`. Each side accepts `{current, previous}`.

1. Accept `N` everywhere, still send `O`. Secret: `token` = `O`, `previous-token` = `N`. Restart the chassis pods and the remote pods. Wait until all are Ready. <!-- pragma: allowlist secret (placeholders, no value) -->
2. Swap what is sent. Secret: `token` = `N`, `previous-token` = `O`. Restart both, one pod at a time. Wait until all are Ready. <!-- pragma: allowlist secret (placeholders, no value) -->
3. Drop `O`. Delete `previous-token`. Restart both.

No side starts a step before every pod of both sides finished the one before. Then no call gets a 401.

Verify with H17, which reads the token only inside the remote's container (`run.sh test-remote`, `test_h17_listener_answers_only_the_remote_token_inside_a_run`).

Not yet run on kind. Two gaps first:

- The PoC-5 manifests map no `previous-token` key and set no `previous_token_env` or `--previous-token-env`. Step 1 needs them.
- `seed.sh rotate remote-echo-token` replaces both copies in one go. Use it only when a 401 window is acceptable; then restart both sides.

## Adding an allow-listed tool

**Symptom.** A workload needs a tool the gateway does not list for its chassis key.

**Check.** What the chassis's key sees, through the wrapper (the key stays in a subshell):

```
$ with_gateway.sh uv run python -I gateway_call.py
tools: ['code_runner-run_python', 'fake_tools-glossary_lookup', 'fake_tools-note_write']
```

The wrapper is now the verb `deploy/kind/poc05/run.sh with-gateway CMD...`.

**Cause.** Three places decide what a key may call:

- The MCP server is registered in LiteLLM under `mcp_servers` (`deploy/kind/poc05/platform/litellm/config.yaml`).
- The key's permission: `seed.sh` writes `object_permission.mcp_servers` and `object_permission.mcp_tool_permissions` (`{server: [tool, ...]}`) per service, from `SERVICES` in `deploy/kind/poc05/platform/seed.sh`. A key with no permission lists no tools.
- A NetworkPolicy edge from LiteLLM to the tool server's port.

The workload sees `<server>-<tool>`, for example `fake_tools-glossary_lookup`. Call the tool by that name. Under a bare name the chassis adapter cannot add the `idempotency_key` argument, and a write tool refuses with `idempotency_key_required`.

**Fix.** Not yet run for a new tool. The steps:

1. Register the server in `litellm/config.yaml`, if it is new.
2. Add the tool to the service's entry in `SERVICES` in `seed.sh`.
3. Add the edge from LiteLLM in the tool's namespace policy.
4. Re-mint the keys: `seed.sh rekey`. It was run on 2026-10-02 (bring-up, item 2) to change `allowed_routes`.
5. Restart the chassis pods so they read the new key.
6. Verify: the tool is listed under its `<server>-<tool>` name, and an unlisted tool is still refused (`test_poc05_kind_tool_gateway.py`).

A write tool needs an `idempotency_key` argument in its input schema. The gateway does not forward `_meta`.

## Admission rejects a deploy

**Symptom.** `kubectl apply` fails, or a Sandbox makes no pod, with a message from `agent-trust-rule`.

**Check.** Read the rule number in the message. Every rule has its own text, starting `trust rule N:`. For example:

```
trust rule 2: an untrusted workload may not run in the sidecar lane; use the remote lane
trust rule 3: outside the remote lane every image must come from the platform registry prefix: <image>
```

The rules are listed at the top of `deploy/kind/poc05/admission/policy.yaml`. Apply as the deployer, as `up` does (`--as=system:serviceaccount:agent-platform-system:deployer`).

**Cause, by rule:**

- 0: the params `agent-platform-system/agent-trust-params` are incomplete. The policy fails closed.
- 1: the pod has no `agents.platform/trust` label.
- 2: an `untrusted` pod in the sidecar lane.
- 3: an image outside the registry prefix, outside the remote lane.
- 4: a trusted sidecar image whose repository is not in `trustedRepositories`.
- 5: a remote pod without `gvisor`, with a service account token, or with a Secret other than its own token.
- 6a to 6c, 7a to 7c, 8: chassis shape, namespace shape, and pull policy. Read the message.

**Fix.** Change the pod, not the policy. A pod that is untrusted goes to the remote lane. Only `agents.platform:platform-admins` may change `agent-trust-params` (`admission/rbac.yaml`); the submitter and the deployer cannot. The admission kind suite shows every rule refusing its fixture with its own message, next to an admitted twin (`test_poc05_kind_admission.py`, 51 passed).
