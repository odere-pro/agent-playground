# Findings R5a: sandboxed, secure

Session: R5 (part a of 2; part b is [findings-R5b-sandboxed-quality.md](findings-R5b-sandboxed-quality.md)). Area: `deploy/kind/poc05/`, `packages/code-runner/`, `packages/fake-mcp-server/`, the chassis tool gateway (`server/tool_endpoint.py`, `adapters/mcp/gateway.py`), `adapters/oidc/`, the remote listener flags in `server/cli.py`, and the PoC-5 kind suites. PoC: `pocs/poc-05-sandboxed`.
Rubric: [rubric.md](rubric.md).

## Summary

The PoC-5 controls are real and mostly well tested: default-deny egress, gVisor, a token only for the remote pod, a tool allow-list per key, and a per-call code sandbox. Every refusal on kind sits next to an allowed control. The top risk is admission. The trust rule checks the service account token only for remote and tool pods, and the deployer identity may delete `default-deny`. So a manifest from the pipeline can undo hard requirement 1 with no refusal from the cluster. Several controls are also checked only statically, or only by `run.sh smoke`, or only by hand. CI runs the remote-lane kind suite but not the admission or sidecar suites.

R2 already covers `remote_auth.py`, `model_proxy.py`, and `adapters/a2a/remote.py`. R0-10 covers `.github/workflows/remote-lane.yml:40`. They are not filed again here.

## Findings

Most severe first. Severity: CRITICAL, HIGH, MEDIUM, LOW. Lens: secure.

### R5a-01 [HIGH] [secure] deploy/kind/poc05/admission/policy.yaml:271
Problem: No admission rule stops a chassis-shape (sidecar-lane) pod from mounting a service account token: automount and `projectedToken` are checked only for remote pods (rule 5, line 251) and tool pods (rule 7d, line 299), and rule 6c counts only Secret references.
Why it matters: ADR-001 hard requirement 1 and `deploy/CLAUDE.md` say the token is never in the workload; today only our own manifests (`hardening_static`) and the live `agent-echo` pod (`test_h02`) hold that, so a deployer manifest that leaves automount at its default is admitted.
Fix: Add rule 7e: a chassis-shape pod sets `automountServiceAccountToken: false`, and a projected token volume is mounted only by the chassis container; add a rejected fixture and an admitted twin. Keeps behavior: no.
Spec: "given a sidecar pod in `poc05-agents` with no `automountServiceAccountToken` field, when the deployer applies it with `--dry-run=server`, then `agent-trust-rule` refuses it with 'trust rule 7e', and its twin with automount false is admitted."
Status: proposal

### R5a-02 [HIGH] [secure] deploy/kind/poc05/admission/rbac.yaml:100
Problem: The `agent-deployer` ClusterRole has create, update, patch, and delete on `networkpolicies`, bound in `poc05-agents`, `poc05-remote`, and `poc05-tools` (lines 107 to 144); `test_poc05_admission_static.py:532` pins this on purpose.
Why it matters: The design says hard limits live in network policy and the pipeline is not the boundary, yet one pipeline manifest can delete `default-deny` or open egress to the metadata service, the API, or the internet.
Fix: Drop `networkpolicies` from `agent-deployer`, apply each `network-policy.yaml` as cluster admin like `base/default-deny.yaml`, and add a `can_i` row "deployer cannot delete networkpolicies" next to its allowed control. Keeps behavior: no.
Spec: "given the deployer identity, when it asks `can-i delete networkpolicies -n poc05-tools`, then the answer is no, while `create pods -n poc05-tools` is yes."
Status: proposal

### R5a-03 [MEDIUM] [secure] deploy/kind/poc05/run.sh:532
Problem: CI (`run.sh test-remote`) runs only `REMOTE_TESTS`; the admission suite, `test_poc05_kind_sidecar_controls.py` (H01, H03/H04, H13), `test_poc05_kind_hardreq1.py`, and `test_poc05_kind_tool_gateway.py` run only by hand, and only rule 1 runs as an `up` canary (lines 192 to 195).
Why it matters: The policy header (policy.yaml:55 to 67) says only the cluster proves the CEL compiles and refuses each fixture, so a broken rule 2 to 8 or a broken metadata deny (criteria 5 and 7) can merge green; this is the cost of the flagged criterion 1 and ADR-001 hard requirement 2.
Fix: Add `test_poc05_kind_admission.py` and `test_poc05_kind_sidecar_controls.py` to the CI verb; they need no Kafka and the cluster is already up in that job (the change is in `run.sh`; R0-10 touches the same workflow). Keeps behavior: yes.
Spec: "given the remote-lane CI job, when a fixture's CEL rule is broken, then the job fails at `test_fixture_outcome_matches_its_header`."
Status: open

### R5a-04 [MEDIUM] [secure] [shared] pocs/poc-05-sandboxed/tests/poc05_conftest.py:61
Problem: With `POC05_KIND=1` set but no `kind-poc05` context, every kind test is skipped, not failed; the gateway fixtures also skip on missing env (`test_poc05_kind_code_runner.py:208`, `test_poc05_kind_tool_gateway.py:51`), and pytest exits 0 on an all-skip run.
Why it matters: A kind run, or the CI job, can go green with zero refusals checked, which is a vacuous pass of the hostile suite.
Fix: When `POC05_KIND=1`, turn a missing context or missing gateway env into `pytest.fail` (or a collection error) instead of a skip. Keeps behavior: yes.
Spec: "given `POC05_KIND=1` and no `kind-poc05` context, when the PoC-5 kind tests run, then the run fails instead of reporting every test skipped."
Status: open

### R5a-05 [MEDIUM] [secure] deploy/kind/poc05/admission/policy.yaml:146
Problem: For sidecar pods, admission does not refuse `shareProcessNamespace: true` and does not require the workload's `runAsUser` to differ from the chassis's, and H26 (no read of the chassis's `/proc/<pid>/environ`) depends on both.
Why it matters: With a shared PID namespace and the same uid, the workload can read `LITELLM_API_KEY` from the chassis's environ, which breaks hard requirement 1; only the static test on our own manifests (`test_poc05_hardening_static.py:209`) catches it.
Fix: Add a sidecar-lane rule: no `shareProcessNamespace: true`, and every non-chassis container has a `runAsUser` different from the chassis's; add a fixture pair. Keeps behavior: no.
Spec: "given a trusted sidecar pod with `shareProcessNamespace: true`, when applied with `--dry-run=server` in `poc05-agents`, then it is refused, and the twin without the field is admitted."
Status: proposal

### R5a-06 [MEDIUM] [secure] deploy/kind/poc05/admission/policy.yaml:299
Problem: Rule 7d requires gVisor and automount false for tool pods, but unlike rule 5 (line 256) it does not check `!variables.projectedToken`, so a tool pod may mount a projected token volume.
Why it matters: A code-runner sandbox runs arbitrary Python; a token there waits for one bad egress edge, and `code-runner-dispatch.yaml:7` claims rule 7d "stays strict".
Fix: Add `&& !variables.projectedToken` to rule 7d and a rejected fixture `rule7d-tool-gvisor/rejected-projected-token.yaml`. Keeps behavior: no.
Spec: "given a tool pod in `poc05-tools` on gVisor with automount false and a projected token volume, when applied with `--dry-run=server`, then it is refused with 'trust rule 7d', and the twin without the volume is admitted."
Status: proposal

### R5a-07 [MEDIUM] [secure] deploy/kind/poc05/agents/network-policy.yaml:21
Problem: Port 8080 ingress on `agent-echo` (and `chassis-echo-remote`, line 69) has no `from`, so any pod whose own egress is not policed (`default`, `kube-system`, `agent-sandbox-system`) can call `/v1/run`, which takes no credential; `test_h18` asserts an unpoliced pod in `default` connects.
Why it matters: Any such pod can drive the agent, spend its scoped LiteLLM key, and reach `run_python` through it, a path to models and tools with no credential of its own.
Fix: Allow 8080 only from a named gateway namespace and label (port-forward needs no rule), or require auth on the public port; update `netpol_edges.yaml:16` and the H18 control. Keeps behavior: no.
Spec: "given a pod in `default` with no egress policy, when it connects to `agent-echo` on 8080, then the connect times out, while the named gateway peer connects."
Status: proposal

### R5a-08 [MEDIUM] [secure] deploy/kind/poc05/run.sh:658
Problem: The only kind proof that PSA `restricted` refuses a root pod, and that seccomp applies, is `run.sh smoke` in `poc05-smoke`; no pytest reads `CapEff`, `NoNewPrivs`, `Seccomp:`, or the uid inside a workload, remote, or code-runner container, and dropped capabilities are static only.
Why it matters: A wrong PSA label on a workload namespace, or a runsc config without `oci-seccomp`, leaves the real workloads unhardened while smoke still passes, and criterion 8 would not see it.
Fix: Add one kind test per lane that reads `/proc/self/status` (`CapEff` all zero, `NoNewPrivs: 1`, `Seccomp: 2`) and a non-zero uid, plus a `--dry-run=server` root pod per workload namespace refused by PSA. Keeps behavior: yes.
Spec: "given the remote pod on kind, when the test reads `/proc/self/status` in the workload, then `CapEff` is all zero, `NoNewPrivs` is 1, `Seccomp` is 2, and a root pod sent to `poc05-remote` is refused by PodSecurity."
Status: open

### R5a-09 [MEDIUM] [secure] deploy/kind/poc05/platform/seed.sh:190
Problem: The `/key/delete` call for the old alias runs `curl -sS` without `-f`, discards the output, and ends in `|| true`, so a 401, 404, or 500 is ignored and a new key is minted anyway.
Why it matters: `seed.sh rotate` after a leak can leave the leaked virtual key live next to the new one, against the header's "never leaves two live keys".
Fix: Use `curl -f`, treat only "no such key" as success, and confirm by `/key/info` or `/key/list` that no key with the old alias remains before minting; fail closed otherwise. Keeps behavior: yes.
Spec: "given LiteLLM answers 500 to `/key/delete`, when `seed.sh rotate chassis-echo-litellm` runs, then it exits non-zero and mints no key."
Status: open

### R5a-10 [MEDIUM] [secure] packages/code-runner/src/code_runner/dispatch.py:426
Problem: Both `FastMCP(...)` constructors (also `server.py:160`) leave `mask_error_details` unset, and fastmcp's default is False, so any exception that is not a `RunnerError` reaches the caller as its own text.
Why it matters: `_answer` (line 249) catches only `ValueError` and `UnicodeDecodeError`, so a `RecursionError` from a nested sandbox answer escapes, and `_make_workdir` (`runner.py:141`) can raise an `OSError` with a `/tmp` path; this breaks "errors do not leak internals" and the module's own rule at lines 8 and 9.
Fix: Pass `mask_error_details=True` to both constructors, and map `RecursionError` and `OSError` to `RunnerError`. Keeps behavior: yes.
Spec: "given a sandbox that answers with 100 KB of nested `[`, when the dispatcher parses it, then the caller gets `sandbox_lost` with the fixed text and no interpreter text."
Status: open

### R5a-11 [MEDIUM] [secure] packages/code-runner/src/code_runner/dispatch.py:445
Problem: `code` has no `max_length` (also `server.py:181`), and the 64 KiB check (line 463; `runner.py:151`) runs only after uvicorn has read and parsed the whole body, with no body cap.
Why it matters: The dispatcher pod has a 128Mi limit, so one oversized `tools/call` can OOM the only code-runner front door (rubric: each inbound adapter validates its input).
Fix: Add `Field(max_length=limits.max_code_bytes)` and an ASGI body cap that returns 413 above a few times `max_code_bytes`; keep the byte check. Keeps behavior: no (the oversize refusal changes shape).
Spec: "given a 50 MB `tools/call` body, when it reaches the dispatcher, then it is refused with 413 or a schema error before 5 MB is buffered."
Status: proposal

### R5a-12 [MEDIUM] [secure] packages/chassis/src/chassis/adapters/oidc/__init__.py:108
Problem: `OidcAuth.__init__` and `from_env` accept any `jwks_url`, including `http://`, with no scheme check.
Why it matters: JWKS fetched over cleartext lets an on-path attacker swap the signing keys and mint tokens that pass every alg, iss, aud, and exp check.
Fix: Refuse a non-`https` `jwks_url` unless the host is loopback, with a `ValueError` at construction. Keeps behavior: no (a startup refusal).
Spec: "given `OIDC_JWKS_URL=http://idp.example/jwks`, when `OidcAuth.from_env()` runs, then it raises and the chassis does not start."
Status: proposal

### R5a-13 [LOW] [secure] deploy/kind/poc05/remote/network-policy.yaml:28
Problem: The metadata and API-server deny are shown on kind from the sidecar workload only (H01, H03/H04); the remote lane's H19 checks only 1.1.1.1, and the code runner checks the API but not metadata.
Why it matters: The remote lane is where untrusted code runs, and its metadata deny rests only on the static no-`ipBlock` test.
Fix: Add the `metadata_listener` check to `test_h19` and to the code-runner egress test. Keeps behavior: yes.
Spec: "given the metadata stand-in on the node, when the remote pod connects to 169.254.169.254:80, then it times out, while the unpoliced caller gets the reply."
Status: open

### R5a-14 [LOW] [secure] deploy/kind/poc05/tools/code-runner.yaml:81
Problem: A read-only root is shown live only for the remote pod (`test_h21`); the code-runner sandbox and the sidecar workload are checked only statically.
Why it matters: If the SandboxTemplate copy drops `readOnlyRootFilesystem`, a call can write outside `/tmp` and no test notices.
Fix: In `test_poc05_kind_code_runner.py`, run code that writes `/var/tmp/x` (expect EROFS) next to `/tmp/x` (expect success). Keeps behavior: yes.
Spec: "given a code-runner call, when it writes `/var/tmp/poc05` and `/tmp/poc05`, then the first fails with EROFS and the second succeeds."
Status: open

### R5a-15 [LOW] [secure] deploy/kind/poc05/admission/params.yaml:26
Problem: `imageSource: digest`, the rule 8 branch meant for clouds (policy.yaml:210), runs only in the offline Python model and never on kind.
Why it matters: The production mode of "images pinned by digest" has never been shown to compile and refuse on a real API server.
Fix: In `test_poc05_kind_admission.py`, patch the params to `digest` the way `test_missing_params_deny_everything` does, check a tag-only fixture is refused and its digest twin admitted, then restore. Keeps behavior: yes.
Spec: "given `imageSource: digest`, when a sidecar pod with a tag-only image is applied with `--dry-run=server`, then it is refused with 'trust rule 8', and the `@sha256` twin is admitted."
Status: open

### R5a-16 [LOW] [secure] deploy/kind/poc05/admission/policy.yaml:67
Problem: The header names `pods/ephemeralcontainers` (matched at line 84) as "only the cluster can prove", but no fixture or kind test exercises it, and the CEL does not check `targetContainerName`.
Why it matters: Whoever holds `pods/ephemeralcontainers` can attach a debug container to the chassis's process namespace and read its environ.
Fix: Add a kind test with two `--dry-run=server` ephemeral containers (a bad image; `targetContainerName: chassis`), each refused next to an admitted twin, and a rule for the second. Keeps behavior: no.
Spec: "given `agent-echo`, when an ephemeral container with `targetContainerName: chassis` is added with `--dry-run=server`, then `agent-trust-rule` refuses it."
Status: proposal

### R5a-17 [LOW] [secure] pocs/poc-05-sandboxed/tests/test_poc05_kind_sidecar_controls.py:298
Problem: The metadata refusal on the HTTP probe is asserted as `"status" not in fetched`, so any error counts as a refusal, while the TCP check above it uses `POLICY_DROPPED`.
Why it matters: `poc05_kind.py:12` says only a timeout means a policy drop, and the probe's `except Exception` (`poc05_kind.py:99`) lets a connection refusal or DNS error pass as a refusal.
Fix: Assert `fetched == {"error": POLICY_DROPPED}`. Keeps behavior: yes.
Spec: "given a live listener at 169.254.169.254, when the workload fetches it over HTTP, then the connect times out."
Status: open

### R5a-18 [LOW] [secure] packages/code-runner/src/code_runner/dispatch.py:411
Problem: The 256 KiB answer cap counts decoded bytes from `aiter_bytes()`, which gunzips, and one decoded chunk is appended before the check at line 413.
Why it matters: The sandbox is the untrusted party, so a small gzip answer can expand to tens of MB in the 128Mi dispatcher (defense in depth; it needs a compromised in-pod server).
Fix: Read with `aiter_raw()` and refuse any `Content-Encoding` but identity. Keeps behavior: yes.
Spec: "given a sandbox answer that gunzips to 50 MB, when the dispatcher reads it, then the call ends `sandbox_lost` having held under 256 KiB."
Status: open

### R5a-19 [LOW] [secure] packages/code-runner/src/code_runner/dispatch.py:333
Problem: If the claim POST is applied but its answer is lost, `_create` returns no name and `finally` never deletes the claim; `_delete` (line 359) tries once and only counts a failure.
Why it matters: Orphaned claims hold pool capacity until the 60 s `shutdownTime` backstop, so under API errors `max_claims` no longer bounds live sandboxes.
Fix: Create with a client-chosen `metadata.name` (`call-<uuid>`) so cleanup deletes by name on any failure, and retry `_delete` once or twice. Keeps behavior: yes.
Spec: "given a create the API server applied but whose answer timed out, when the call fails, then a delete for that claim name is sent."
Status: open

### R5a-20 [LOW] [secure] packages/chassis/src/chassis/adapters/mcp/gateway.py:333
Problem: Every `call()` with a name not in the cached list first runs a full `refresh()` (a new MCP session and `tools/list`), names have no length bound, and line 352 logs the name.
Why it matters: An untrusted remote workload on `/mcp` can double the gateway load per call and flood the log with long names.
Fix: Refresh for an unknown name at most once per cooldown (as `OidcAuth` does for JWKS) and cap name length (128) at the endpoint. Keeps behavior: yes.
Spec: "given 100 calls a second to unlisted names, when the endpoint serves them, then the gateway sees at most one `tools/list` per cooldown."
Status: open

### R5a-21 [LOW] [secure] packages/chassis/src/chassis/adapters/oidc/__init__.py:168
Problem: `_key` holds the `asyncio.Lock` across `_fetch()` (5 s timeout), and a failed fetch leaves `_fetched_at` unchanged (lines 193 to 201), so every request retries.
Why it matters: While the IdP is down, all requests queue behind the lock and each waits up to 5 s, so any bearer-shaped token can stall the chassis.
Fix: Record a short negative-cache time on failure and answer `auth_unavailable` at once, or fetch with a single-flight future outside the lock. Keeps behavior: yes.
Spec: "given a JWKS endpoint that times out, when 20 requests arrive together, then all get `auth_unavailable` within about 5 s in total."
Status: open

### R5a-22 [LOW] [secure] packages/chassis/src/chassis/server/cli.py:155
Problem: `--allow-any-proxy-host` puts the model proxy and `/mcp` on a non-loopback address with no auth, and nothing refuses it when the profile is `cloud` or `spec.trust` is `untrusted`.
Why it matters: That listener spends the chassis key and calls tools with the chassis gateway key for any pod that can reach it (hard requirement 1); the help text says "tests only" without enforcing it.
Fix: After loading the config, refuse the flag in profile `cloud` and when `spec.trust` is `untrusted`. Keeps behavior: no (a startup refusal).
Spec: "given `--proxy-host 0.0.0.0 --allow-any-proxy-host` and profile `cloud`, when `chassis serve` starts, then it exits with status 2."
Status: proposal

### R5a-23 [LOW] [secure] packages/code-runner/src/code_runner/cli.py:79
Problem: The dispatcher serves `/mcp` on `0.0.0.0` (`code-runner-dispatch.yaml:62`) with no authentication while it holds a token that can create `SandboxClaim`s; only the NetworkPolicy at `platform/network-policy.yaml:94` limits callers.
Why it matters: A policy gap or a CNI without enforcement lets any pod run code and use up claims, and the dispatcher cannot tell callers apart.
Fix: Accept an optional bearer read from a file, compare it with `hmac.compare_digest`, and have LiteLLM send it. Keeps behavior: no.
Spec: "given a configured token, when a call to the dispatcher `/mcp` has no bearer, then it gets 401 and no claim is created."
Status: proposal

### R5a-24 [LOW] [secure] packages/chassis/src/chassis/adapters/mcp/gateway.py:356
Problem: `_content(result)` returns the gateway's tool output with no size cap, and `adapters/mcp/server.py:181` serializes it back to the workload whole.
Why it matters: One large upstream tool result is buffered in the chassis and relayed; the dispatcher has this bound (dispatch.py:413) and the gateway adapter lacks it.
Fix: Cap the serialized result (suggested: 1 MiB) and map an overrun to `tool_unavailable`. Keeps behavior: no (a new refusal).
Spec: "given a tool that returns 50 MB, when the chassis relays it, then the workload gets `tool_unavailable` and the chassis holds no more than the cap."
Status: proposal

### R5a-25 [LOW] [secure] packages/chassis/src/chassis/adapters/mcp/gateway.py:254
Problem: `from_env` accepts any `LITELLM_MCP_URL`, so an `http://` URL to a host outside the cluster sends `Bearer <LITELLM_API_KEY>` in cleartext.
Why it matters: The module stops redirects and proxies (`trust_env=False`) but not a plain-http hop in the `cloud` profile (hard requirement 1: the key stays protected).
Fix: Refuse a non-`https` URL unless the host is loopback or an in-cluster name, and require `https` in profile `cloud`. Keeps behavior: no (a startup refusal).
Spec: "given `LITELLM_MCP_URL=http://gw.example.com/mcp` in profile `cloud`, when the gateway tools are built, then construction fails and names the variable."
Status: proposal
