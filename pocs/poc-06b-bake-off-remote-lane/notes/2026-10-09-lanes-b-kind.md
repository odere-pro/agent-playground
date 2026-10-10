# PoC-6 engines on kind: design, exceptions, and what is not yet shown (2026-10-09)

Task T-LANES-B. Command: `deploy/kind/poc06/run.sh up` then `run.sh test` (CI: `.github/workflows/poc06-kind.yml`). It was written in a container with no kind, no kubectl, and no working Docker; the kind half ran in CI, and the results are in "Kind runs" at the end. The offline half is `pocs/poc-06b-bake-off-remote-lane/tests/test_poc06b_kind_static.py`. The kind half is `test_poc06b_kind_{engines,remote_controls,sidecar_controls}.py`.

## What runs where

| Engine | Lane | Pod | Chassis | Image |
| ------ | ---- | --- | ------- | ----- |
| echo-openai-agents | sidecar | `agent-openai-agents` (runc) | native sidecar in the same pod | `echo-openai-agents:poc06` |
| echo-typescript | sidecar | `agent-typescript` (runc) | native sidecar | `echo-typescript:poc05` (PoC-5's build) |
| echo-smolagents | remote | Sandbox `remote-smolagents` (gVisor) | `chassis-smolagents-remote` | `echo-smolagents:poc06` |
| echo-claude-agent | remote | Sandbox `remote-claude-agent` (gVisor) | `chassis-claude-agent-remote` | `echo-claude-agent:poc06` |
| echo-typescript | remote | Sandbox `remote-typescript` (gVisor) | `chassis-typescript-remote` | `echo-typescript:poc05` |
| kagent-adk | remote, plain A2A | Sandbox `remote-kagent-adk` (gVisor) | `chassis-kagent-adk-remote` | `kagent-adk:poc06`, built from kagent's source at a pinned commit |

All in PoC-5's namespaces and cluster: chassis pods in `poc05-agents`, remotes in `poc05-remote`. PoC-5's LiteLLM and Valkey policies admit only `poc05-agents` pods with `agents.platform/role: chassis`, so a new namespace would need PoC-5 edits.

## Decisions

1. **Trusted repositories.** Rule 4 admits a trusted sidecar pod only if its workload repository is on `trustedRepositories`. `echo-openai-agents` was not. `deploy/kind/poc06/admission/params.yaml` is PoC-5's ConfigMap plus that one repository; `run.sh` applies it as the cluster admin after the PoC-5 bring-up. A test fails if it differs from PoC-5's in anything else. This extends a hand-kept allow-list; it removes no control.
2. **The fake model's script.** The in-cluster fake model server runs `example.yaml`. The bake-off tasks need other rules, and the example script's catch-all `after_tool` rule would swallow the lookup loop. `run.sh` mounts `platform/fake-model-script.yaml` through a patch on the live Deployment (no PoC-5 file changes). On a PoC-6 cluster the PoC-5 kind suite's scripted texts (`hello`, `glossary`, `fail`) get the default reply. PoC-5's own suite needs a PoC-5-only cluster. Open question for the orchestrator: if one cluster must serve both suites, the script needs an engine-aware split that the fake server cannot do today.
3. **Gateway tool names.** LiteLLM lists `fake_tools-glossary_lookup`. The OpenAI Agents SDK refuses a call to a name it was not offered, so the scripted tool call carries the gateway's name. The kind tests strip the prefix, and read a text-only result as its JSON object (`poc06b_kind.gateway_view`), then apply `poc06_harness`'s checks unchanged.
4. **Engine markers.** A smolagents `CodeAgent` needs Python in a `<code>` block, and kagent-adk is checked on text. The script cannot see the engine, so the kind test adds ` [code]` or ` [text]` to the end of the task text for those two engines. The harness checks do not read the input text.
5. **Claude scratch volume.** `/tmp` is a 128Mi memory-backed emptyDir; `CLAUDE_AGENT_HOME_BASE=/tmp`, and each run makes its HOME and work dir there. The memory limit is 1Gi (the CLI is 175 to 240 MB resident). The pod sets no other `ANTHROPIC_*` or `CLAUDE_*` variable; a static test checks it.
6. **Proxy addresses.** Each remote's chassis has a fixed-ClusterIP Service (`10.96.85.92` to `.95`; PoC-5 uses `.91`), because the remote lane has no DNS.
7. **kagent-adk.**
   - It is the probe note's "cheaper alternative": the Python ADK runtime alone, not the controller, Substrate, or PostgreSQL. It is not "kagent on Kubernetes".
   - **Image: built from source, pinned by commit.** No ghcr digest could be resolved offline. `run.sh build_kagent` clones `kagent-dev/kagent`, checks out the commit in `deploy/kind/poc06/kagent/source.commit` (`e324f6d8...`, the probed one), fails closed unless `git rev-parse HEAD` equals it, builds `python/Dockerfile`, and `kind load`s it as `kagent-adk:poc06`. The pod uses `imagePullPolicy: Never`.
   - **Supply-chain gap, accepted for a PoC remote.** kagent's `python/Dockerfile` does not pin its base images by digest: `debian:bookworm-slim` and `ghcr.io/astral-sh/uv:${UV_VERSION}` are by tag, and `apt-get install` is unpinned. Python dependencies come from the frozen `uv.lock` (`uv sync --frozen`), whose hashes uv checks. The pin is the git commit (SHA-1) only.
   - **Launcher.** The image's `kagent-adk static` builds the app against a kagent API server for its task store; none exists here. The ConfigMap carries `run_kagent_adk.py`, the probe's launcher (same app, `local=True`, in-memory task store), started with the image's venv Python `/.kagent/.venv/bin/python`. The image supplies libraries only.
   - OTEL exporters are set to `none` in the pod env (the probe saw 16.8 s with the defaults, 0.64 s with them off).
   - **Deviation from the task text:** the model `base_url` is the chassis's fixed ClusterIP, not a DNS name. The remote lane has no DNS (PoC-5 H20, tested), and giving kagent a kube-dns edge would weaken that. The DNS-name rule in the probe note comes from Substrate's credential proxy, which is not used here.
   - The runtime checks no inbound bearer (probe note, item 5). The control is the NetworkPolicy (9000 only from its chassis). It holds no Secret: the inbound bearer is its model key (`api_key_passthrough`).

## Recorded exceptions

- **No Python in the Node image.** `remote-typescript` and `agent-typescript` cannot run the PoC-5 `kubectl exec ... python` probe. `tests/node_probe.js` implements the same check kinds in Node (`tcp`, `http`, `path`, `env_names`, `read`, `write`, `resolve`, `dns`). It does not implement `procs`. The PoC-5 H26 `/proc` scan is a sidecar check that the new sidecar tests do not repeat (they check `shareProcessNamespace` on the spec). The Node probe was run on this container only for `path`, `tcp`, `write`, `read`, and `env_names`; `dns` and `http` were not run.
- **kagent-adk Python.** The image has its venv Python at `/.kagent/.venv/bin/python` (on the image's PATH as `python`; `probe_python` also tries the full path). If none is found the test is marked `xfail` with `NO_PYTHON`, and the pod-spec and node checks still stand.
- **Claude lookup.** `xfail(strict=False)`: the CLI offers MCP tools as `mcp__chassis__<name>`, so the scripted call names a tool it did not offer. A fix is coming separately.
- **kagent-adk lookup** asserts the answer text only. In plain-A2A mode the chassis cannot see tool calls; the test asserts no `tool_call` event appears, so the limit is on record.

## gVisor and clone3: the seccomp profile

**Symptom.** The first `poc06-kind` CI run failed at `run.sh up`. The `remote-typescript` Sandbox crash-looped at Node start:

```
#  node[1]: std::unique_ptr<long unsigned int> node::WorkerThreadsTaskRunner::DelayedTaskScheduler::Start() at ../src/node_platform.cc:109
#  Assertion failed: (0) == (uv_thread_create(t.get(), start_thread, this))
```

**Root cause.** The kind nodes run runsc with `oci-seccomp = "true"` (`deploy/kind/poc05/install-gvisor.sh`), so the pod's RuntimeDefault profile is applied inside the sandbox. runsc's OCI seccomp converter ignores `errnoRet` and returns EPERM for every `SCMP_ACT_ERRNO` rule (https://github.com/google/gvisor/issues/14688; the fix, https://github.com/google/gvisor/pull/14721, is approved but not released; our pin is runsc 20260928.0). containerd's RuntimeDefault blocks `clone3` with `errnoRet: 38` (ENOSYS), so glibc falls back to `clone`. Under runsc that is EPERM, and `pthread_create` fails in glibc 2.34 and later. Every multi-threaded program in a gVisor pod is hit: Node (at start), Python `threading`, the Claude CLI binary, kagent-adk. The Python remotes only looked healthy because they start single-threaded. Another project hit the same limit and allowed `clone3` for runsc only (eumemic/aios PR 2435).

**Fix.** A Localhost profile, `poc06-runsc-clone3.json`, for the four PoC-6 gVisor pods (`remote-typescript`, `remote-smolagents`, `remote-claude-agent`, `remote-kagent-adk`; pod and container level). It is the RuntimeDefault profile the node's containerd generated for a drop-ALL container, with one change: the `clone3` rule's action is `SCMP_ACT_ALLOW` and its `errnoRet` is gone.

- `deploy/kind/poc06/seccomp/derive_profile.py` makes it from `crictl inspect` of the running PoC-5 `remote-echo` container (gVisor, RuntimeDefault, drop ALL). It fails closed unless there is exactly one rule naming `clone3`, with names exactly `["clone3"]`, action `SCMP_ACT_ERRNO`, `errnoRet` 38, and it asserts the output differs from the input in that rule only. Offline tests: `tests/test_poc06b_seccomp_profile.py`.
- `run.sh seccomp` (run first by `up` and `apply`) writes the file to `/var/lib/kubelet/seccomp/profiles/` on every kind node and logs its sha256 and the one changed rule.
- The static test pins a gVisor pod to exactly this Localhost profile and a runc pod to RuntimeDefault. The kind test `test_remote_can_start_a_thread_and_its_seccomp_filter_is_on` starts a thread in each remote and reads `Seccomp:\t2` from `/proc/self/status`.

**Accepted risk.** `clone3` is unfiltered inside the guest, so a guest process can create namespaces through `clone3`. They live in the Sentry's emulated kernel, not on the host. Every other RuntimeDefault rule is unchanged, and gVisor stays the boundary.

- The control lost: RuntimeDefault's masked `clone` rule is what blocks the `CLONE_NEW*` flags. `clone3` takes its flags in a struct, so seccomp cannot mask them, and allowing `clone3` bypasses that mask. A guest can now create a user namespace through `clone3`. `unshare` and a raw `clone` with `CLONE_NEWUSER` stay refused; the kind test `test_remote_still_refuses_namespaces_and_mounts` checks that, with `mount`.
- A user namespace gives the guest namespace-scoped capabilities, and it reaches Sentry code that is gated on them. That is a larger Sentry attack surface than before. It is still inside gVisor, and the host kernel does not see it.
- PoC-5's checklist line "default seccomp profile" is relaxed for these four pods only. A static test scans every manifest under `deploy/` and fails if any other pod references the profile. PoC-5 admission has no seccomp rule, and PoC-6 does not edit PoC-5.
- `derive_profile.py` pins the baseline before it derives: `defaultAction` is `SCMP_ACT_ERRNO`; at least 200 syscalls are allowed (`suggested:` floor; containerd's profile has over 300); none of `mount`, `umount2`, `unshare`, `setns`, `bpf`, `keyctl`, `open_by_handle_at`, `perf_event_open`, `init_module`, `finit_module`, `kexec_load` is allowed (each needs a capability in containerd's profile, or is absent; `ptrace` is not on the list, because containerd allows it with no capability on kernel 4.8 and later, which kind run 2 showed); a `clone` rule with an `args` mask exists; the container has no bounding capabilities. `run.sh seccomp` also checks that the source pod is owned by the `remote-echo` Sandbox, runs on gVisor, and drops ALL with RuntimeDefault.
- Secret handling: `crictl inspect` output holds the `remote-echo` container env, including its token. It flows only through a pipe into `derive_profile.py`, which never prints its input, and no error path echoes it.
- Missing file: a pod that names a Localhost profile the node lacks must not run. The kind test `test_a_pod_with_a_missing_profile_never_runs` records what the cluster does (admission refusal, or `CreateContainerError` or `CreateContainerConfigError`). Either is fail closed.
- The node write is atomic: a temp file, a sha256 check, then `mv`.

**Removal trigger.** Bump runsc to a release that contains google/gvisor#14721, drop the Localhost profile and the `seccomp` step, and go back to RuntimeDefault in the four manifests (and in the static test).

**PoC-5 is left alone.** PoC-5's `remote-echo` has the same latent limit: its Python threads would fail. It starts single-threaded, so it works. PoC-6 edits no PoC-5 file, so it is not changed here.

**Checked on a cluster (kind runs 4 to 6).** Both assumptions held: `crictl inspect` of a runsc container includes `info.runtimeSpec`, and containerd's Localhost loader accepts the OCI-format profile as written. `profiles/...` is relative to the kubelet's seccomp directory (`/var/lib/kubelet/seccomp`, suggested: the kubelet default).

## What the chassis cannot see or control for kagent-adk (this slice)

- Tool calls and the model's turns inside the runtime (plain mode maps artifacts to deltas and reads token usage from `kagent.dev/a2a/usage`).
- Whether the runtime checks the inbound bearer (it does not).
- The runtime's own telemetry and its state (OTEL is set off in the manifest; in-memory task store).
- Kagent's controller, Substrate, and PostgreSQL are not part of this slice, so nothing here says what they would do to the admission rules or the memory budget.

## What CI was expected to show (written before the first run)

The expectation, kept for the record; "Kind runs" at the end has what happened. Likely first failures, in order: (1) a gVisor start problem on the runner (as for `remote-lane.yml`); (2) the Claude pod's memory or startup time under gVisor; (3) smolagents or OpenAI Agents SDK tool names against the gateway; (4) the fake-model script's reach for the Claude smoke and simplifier (the CLI's trailing system turns); (5) the kagent source build (network clone, uv sync) and its launcher under gVisor. The kagent tests are real, not xfail.

## Update 2026-10-10: first kind run, kagent-adk 403 on the model call

Run: `poc06-kind` 38007359399 (job 114079121220, kind run 4). Result: 70 passed, 1 xfailed, 3 failed. The three failures are the `kagent-adk` tasks, and the `Up` step was green. Every other engine passed. Each kagent-adk task ended `a2a.failed`: its model call to the chassis remote proxy (`POST http://10.96.85.95:8091/v1/chat/completions`) got 403. The chassis log said `RequireRun`: "the traceparent names no run in flight". The bearer passed (no 401), so the failure was the missing trace context, not the token.

Cause (reproduced locally, details in the probe note, Item 4): the pod runs with the OTel exporters `none`, so kagent's `instrument_app` does not instrument the app and the inbound `traceparent` is dropped; and the model client uses `httpx2`, which the httpx instrumentor does not patch. The probe's "same trace id reached the model" was measured on 0.4.0 from PyPI with exporters on. It does not hold for the source build at the pin.

Fix: `run_kagent_adk.py` in `deploy/kind/poc06/kagent/remote-kagent-adk.yaml` now registers `TraceparentPassthroughPlugin` after `LLMPassthroughPlugin`. It copies the inbound `traceparent` onto every model request through a request hook on the model's client. The bearer passthrough is unchanged. The exporters stay off. Offline tests: `tests/test_poc06b_kagent_traceparent.py`. Not yet shown on kind: that waits for the next `poc06-kind` run.

## Kind runs (CI, `poc06-kind.yml`)

| Run | Head | Result |
| --- | ---- | ------ |
| 38001700054 (run 1) | `fa7fe99` | `Up` failed: `remote-typescript` crash-looped, Node's `uv_thread_create` assertion. Cause: runsc turns RuntimeDefault's `clone3 -> ENOSYS` into EPERM ("gVisor and clone3" above) |
| 38005452147 (run 2; run 3 on `0e1655a` the same) | `e025021` | `Up` failed in the `seccomp` step: `derive_profile: baseline: the input allows syscalls it must not: ['ptrace']`. containerd allows `ptrace` with no capability on kernel 4.8 and later; it left the deny list. The source-pod checks and `crictl inspect` worked |
| 38007359399 (run 4) | `d8ea82d` | `Up` green, every PoC-6 gVisor Sandbox Ready. 70 passed, 1 xfailed (Claude lookup), 3 failed (kagent-adk, 403 `run_required`; the update above) |
| 38010118212 (run 5) | `e28a584` | 71 passed, 3 failed (kagent-adk only). The Claude lookup passed once the fake model answered the prefixed tool name; the token scrub broke nothing |
| 38010290978 (PR run) and 38010210641 (push run) | `0248655` | **74 passed**, no skip, no xfail. Every pod Running with 0 restarts. The profile's sha256 on the node: `916fe642...`, the one changed rule `clone3` |

So on kind, under gVisor with the Localhost profile, every remote engine (TypeScript, smolagents, Claude Agent SDK, kagent-adk) and every sidecar engine of this slice (OpenAI Agents SDK, TypeScript) passes smoke, simplifier, and lookup through its chassis, and the remote and sidecar controls (`test_poc06b_kind_remote_controls.py`, `test_poc06b_kind_sidecar_controls.py`) pass, including the thread start, `Seccomp: 2`, and the refused namespace and mount checks.
