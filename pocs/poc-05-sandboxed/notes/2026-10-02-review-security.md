# PoC-5 security review after waves 0 to 2 (before cluster bring-up)

Author: platform-security, 2026-10-02. Read-only review of the files listed in the orchestrator's scratch list. Inputs: `docs/plans/2026-10-02-poc-05-sandboxed.md`, `notes/2026-10-02-threat-model.md` (B1-B12, H01-H32), `notes/2026-10-02-kind-sandbox-spike.md`. No secret values in this file.

Commands run (offline only):

```
kubectl kustomize deploy/kind/poc05/{base,platform,agents,remote,tools}   -> all five build
shellcheck deploy/kind/poc05/run.sh deploy/kind/poc05/install-gvisor.sh deploy/kind/poc05/platform/seed.sh   -> clean
scripts/check_offline.sh packages/chassis/tests/test_remote_auth.py packages/chassis/tests/test_server_cli.py \
  packages/workload-a2a/tests/test_workload_a2a_auth.py pocs/poc-05-sandboxed/tests -q
  -> 246 passed, 36 skipped, 1 warning in 5.59s
```

## Verdict

**GO for bring-up, FIX FIRST before any admission or hard-requirement-1 claim.** Bring-up as cluster admin can proceed. Nothing here lets a workload reach a credential during bring-up. But the three High findings mean the admission suite (T21) would pass while the trust rule can still be bypassed by shape. Fix F1 to F3 and add their rejected fixtures before T21 runs. Fix the bring-up blockers (section "Bring-up blockers") first, or bring-up fails for reasons that have nothing to do with security.

Count: 3 High, 5 Medium, 8 Low, plus 5 bring-up blockers that are not security findings.

## Findings, most severe first

### F1 (High): the registry prefix is a Docker Hub namespace, and admission does not pin the pull. The source rule can be bypassed.

- Where: `deploy/kind/poc05/admission/params.yaml:21` (`registryPrefix: agent-platform/`), `admission/policy.yaml:145-157` (rules 3 and 4 match by string prefix only). The params comment at lines 7-9 names the risk but leaves it to `run.sh` and the manifests.
- Consequence: `agent-platform/x` is short for `docker.io/agent-platform/x`. A submitter sets `imagePullPolicy: Always` (or uses a tag that was never `kind load`ed, with `IfNotPresent`). The node then pulls from whoever owns the Docker Hub namespace `agent-platform`. That image passes rule 3 (prefix) and rule 4 (`agent-platform/echo-python:<any tag>` is a trusted repository). Code from outside our registry runs in the trusted sidecar lane next to the chassis. This breaks the trust rule's source half. Tags are also accepted, so any tag of a trusted repository is admitted.
- Fix (PoC-5): (a) make the prefix a registry host only we control and that cannot resolve publicly. Suggested: `kind.local/agent-platform/`, with the images built and loaded under that name. (b) Add a rule: outside the remote lane, every container sets `imagePullPolicy: Never` on kind. In the cloud, require `@sha256:` digests for `trustedRepositories`. (c) Add rejected fixtures for `imagePullPolicy: Always`, for a `docker.io/agent-platform/...` spelling, and for a tag that was not loaded. Each needs an admitted twin.
- Proof: `kubectl --as=system:serviceaccount:poc05-tenant:submitter apply --dry-run=server` of each fixture. Each is rejected with the new rule's message, and its twin is admitted.

### F2 (High): any pod may run the chassis image with its own command and take any Secret in the namespace. This is the builder's Secret gap, and it is wider than reported.

- Where: `admission/policy.yaml:95-105` (chassis images are taken out of `workloadImages`) and `153-157` (rule 4 checks only `workloadImages`). No rule reads `command`, `args`, the number of chassis containers, or Secret references outside the remote lane. `admission/rbac.yaml:35-47` gives the submitter pod create in `poc05-agents`.
- Consequence: a pod whose only image is `agent-platform/chassis:poc05` is in the "other" lane. Rule 3 passes and rule 4 does not apply. With `command: [python, -c, ...]` it runs arbitrary code on runc from a "trusted" image. With `env.valueFrom.secretKeyRef` it can take any Secret in `poc05-agents`: every chassis's LiteLLM virtual key (four are seeded, two are unmounted), `valkey-auth`, `minio-chassis`, and both remote tokens. The same holds for a pod with two chassis containers, or for a chassis container whose `--config` points at a ConfigMap the submitter wrote (the submitter may create ConfigMaps). It then spends on another agent's key with no run budget. Hard requirement 1 holds today only because the submitter is the operator.
- Fix: do both of the following.
  1. Namespace split. Drop the submitter's RoleBinding in `poc05-agents` (`rbac.yaml:35-47`). Chassis pods and their Secrets are deployed by a platform deployer identity, which stands in for the CH-3 chart or pipeline. The kind admission tests for rules 2 and 4 run as that deployer. Otherwise RBAC, not the policy, refuses them, and the test passes for the wrong reason.
  2. Add a rule 6 as a backstop in every lane except remote:
     - at most one container uses the chassis repository;
     - that container sets no `command`;
     - only that container references a Secret (`env`, `envFrom`, or a `volumeMount` of a secret or projected volume);
     - the chassis's `--config` volume is a ConfigMap named `chassis-<app>-config`, which is suggested.
- Record in ADR-005 and the blind-spots note that any principal with pod create in a namespace can read every Secret in it. Separation between agents therefore needs a namespace per agent (production, CH-3).
- Proof: three rejected fixtures, each with an admitted twin: a chassis-image `command` override, two chassis containers, and a workload container with a `secretKeyRef`. Also an RBAC check that the submitter cannot create a pod in `poc05-agents`: `kubectl auth can-i create pods -n poc05-agents --as=...` returns `no`.

### F3 (High): NetworkPolicy and Service selectors read labels the submitter writes, and admission does not tie a pod's shape to its namespace.

- Where: `platform/network-policy.yaml:21-27,141-147,164-170` (ingress to LiteLLM, Valkey, and MinIO comes from `agents.platform/role: chassis` in `poc05-agents`), `agents/network-policy.yaml:16-18,64-81`, `remote/network-policy.yaml:10-35` (`app.kubernetes.io/name`), `admission/policy.yaml:106-110,168-173` (any remote-shaped pod passes in any enforced namespace, and rule 5 allows any `remote-*-token`).
- Consequence: the submitter can create these pods:
  - In `poc05-agents`, a remote-shaped pod (no chassis image, `lane: remote`, gVisor). Rule 3 is skipped, so any third-party image is allowed. With the labels `app.kubernetes.io/name: agent-echo` and `agents.platform/role: chassis`, it:
    - takes agent-echo's egress (LiteLLM, Valkey, DNS) and passes LiteLLM's and Valkey's ingress;
    - joins the `agent-echo` Service endpoints and receives public callers' requests;
    - can mount `remote-echo-token`, which rule 5 allows and which exists in `poc05-agents`.
  - With `app.kubernetes.io/name: chassis-echo-remote` it also gets egress to remote-echo:9000. With the token, it impersonates the chassis to the remote.
  - In `poc05-remote`, a pod labeled `app.kubernetes.io/name: remote-echo` with `remote-echo-token` passes 8091's ingress rule and BearerAuth as remote-echo (B8).
- Fix (PoC-5): add a rule 7 that uses `namespaceObject`. Give each enforced namespace a lane label: `poc05-agents` holds chassis-bearing pods only, `poc05-remote` holds the remote shape only, and `poc05-tools` holds gVisor tool pods only. The pod's computed shape must match its namespace's label. Allow `agents.platform/role: chassis` only on a pod that runs the chassis image. Bind rule 5's Secret name to the pod: `remote-` + `labels['agents.platform/remote']` + `-token`. With F2's namespace split, the submitter cannot reach `poc05-agents` at all. Record that per-remote tokens separate remotes from each other only once each remote has its own namespace. In PoC-5 every remote shares `poc05-remote`, so this is a recorded exception.
- Proof: rejected fixtures with admitted twins: a remote-shaped pod in `poc05-agents`, `role: chassis` on a pod with no chassis image, and a remote pod mounting another remote's token.

### F4 (Medium): the chassis's remote listener lets non-HTTP ASGI scopes through without the token.

- Where: `packages/chassis/src/chassis/server/remote_auth.py:123-125` (BearerAuth) and `161-163` (RequireRun) pass any scope type other than `http` straight to the app.
- Consequence: a websocket upgrade to `/mcp` (a mounted ASGI app) reaches the route with no token and no run. That works if uvicorn in the image has a websocket implementation. No websocket route exists today, so this is a fail-open shape on the chassis's one remote auth boundary, not a working bypass. The workload template already does this right: `packages/workload-a2a/src/workload_a2a/auth.py:59-73` closes a websocket with 1008.
- Fix: in BearerAuth, refuse `websocket` (close 1008) and pass only `lifespan`. Add a unit test that a websocket scope with no token never reaches the app.

### F5 (Medium): in the code runner, a snippet can outlive its call and see later callers' code. The runner's limits stop at the process group.

- Where: `packages/code-runner/src/code_runner/runner.py:117-119,186,216` (one `killpg` of the first group); `_BOOTSTRAP` lines 35-39 (no `RLIMIT_NPROC`); the snippet runs as the server's uid 10003.
- Consequence:
  - A grandchild that calls `setsid()` or double-forks escapes `killpg` and keeps running after the call returns.
  - Because it has the same uid, it can read the next callers' `/tmp/run-*/main.py` and their output, signal or ptrace the server, and read `/proc/<server>`. `MAX_CONCURRENT = 1` does not help against a process that stays behind.
  - A fork loop hits `podPidsLimit` and, under gVisor, kills the whole sandbox (spike, question 6). That is a denial of service on the tool for every key.
  - The pod boundary holds (no egress, gVisor, no Secret). Isolation between calls does not.
- Fix (PoC-5):
  - The server sets `PR_SET_CHILD_SUBREAPER` and `PR_SET_DUMPABLE=0`.
  - After every call, it kills every process in the container that is not the server (walk `/proc`; same uid, so it is allowed).
  - Add `RLIMIT_NPROC` to the bootstrap.
  - Record that isolation between calls is per process, not per sandbox. The production fix is one sandbox per call or per key, from an agent-sandbox warm pool.
- Proof (kind): call 1 double-forks a sleeper that writes a marker every 100 ms. Call 2 sees no marker growth and no foreign process. Control: within one call, the snippet's child process does run.

### F6 (Medium): sidecar lane, the chassis restart window. The native-sidecar order closes B7 at first start only.

- Where: `deploy/kind/poc05/agents/agent-echo.yaml:54-59,103-122`.
- Consequence: if the chassis container crashes or is OOM-killed, the kubelet restarts it while the main workload keeps running. The workload can drive the chassis to its 256Mi limit through the proxy. In that window, the workload can bind `POD_IP:8080` and `127.0.0.1:8090`. The new chassis exits 3 and crash-loops, while the workload answers the public port, the Service traffic, and the kubelet's `/health` and `/ready` probes. It sees callers' requests and any caller credentials. The workload gains no internal credential.
- Fix: record it as an accepted risk of the sidecar lane (trusted code only) in ADR-005 and the blind-spots note. Optionally add a check to the workload's liveness probe that fails when the chassis is absent, so the pod restarts as a whole. Add an H14 restart case to the kind suite: kill the chassis process and assert who owns 8080 and 8090 afterward.

### F7 (Medium): the admission CEL has never been executed. The offline test checks a Python model of it.

- Where: `pocs/poc-05-sandboxed/tests/test_poc05_admission_static.py:15` ("The model mirrors the CEL; it does not run it").
- Consequence: these are unproven until the server runs them: `request.kind.kind` for `pods/ephemeralcontainers`; the `Sandbox` `podTemplate` path; that agent-sandbox copies the podTemplate labels onto the pod (else rule 1 rejects every Sandbox pod and every NetworkPolicy misses it); and a CEL runtime error on a shape no fixture covers (`failurePolicy: Fail`, so this fails closed, but for the wrong reason). The params lookup is fail-closed as written: `parameterNotFoundAction: Deny`, plus rule 0 on empty values.
- Fix: run the fixtures with `--dry-run=server` first on the cluster (verification list, item 3). Add one fixture per workload kind and one ephemeral-container case.

### F8 (Medium): Postgres is pinned by tag, and no image is signed.

- Where: `deploy/kind/poc05/platform/postgres.yaml:40` (`postgres:17.6-alpine`, TODO). This database holds LiteLLM's virtual keys and their budgets.
- Consequence: a tag can move between bring-ups. Every other pod is pinned by digest: kind node, LiteLLM, Valkey, MinIO, mc, busybox, the agent-sandbox controller plus the manifest's sha256 check, and the Python, node, and uv bases in our Dockerfiles. Python dependencies come from `uv sync --locked`, which checks hashes. No image is signed (the ADR-001 rule; H-10 later), and MinIO and mc are community builds (a recorded exception).
- Fix: pin Postgres by digest before bring-up. List the unsigned images in the exceptions with H-10 as the owner.

### F9 (Low): the Valkey ACL is broad and shared by every agent.

- Where: `platform/seed.sh:98`, `user chassis on #<sha256> ~* &* +@all -@admin`.
- Consequence: anything holding the password can `FLUSHALL`, `KEYS *`, and `EVAL`, and can read every agent's keys. B12 stays open (PoC-8).
- Fix: `-@dangerous`, and a key pattern per agent when B12 is taken up.

### F10 (Low): MinIO credentials and an edge exist with no consumer.

- Where: `seed.sh:112-121` seeds `minio-chassis` into `poc05-agents`, and `platform/network-policy.yaml:164-173` admits chassis pods to MinIO. No chassis uses MinIO (`config: memory`, `agents/network-policy.yaml:6`).
- Consequence: an extra credential that F2 can steal, and an ingress edge no pod needs.
- Fix: seed `minio-chassis` into `poc05-platform` only, and drop the chassis ingress rule until a chassis reads its config from S3.

### F11 (Low): `mc admin user add` puts the chassis key pair in argv. Accept as a recorded exception.

- Where: `platform/minio.yaml:176-178`.
- Why accept: it is a one-shot Job, and the same container already holds the MinIO root pair. The pair it creates is read-only on one bucket of fake data. The argv is visible only to processes in this container and to root on the node, which can read Secrets anyway. The pod is deleted after 600 s.
- Record it in `deploy/compose/SECURITY.md` section 7 with the MinIO build-from-source item. Revisit by creating the user through the admin API from a small script that reads the pair from env.

### F12 (Low): the sidecar workload reaches the chassis's public port at the pod IP, not at loopback.

- Where: `agents/agent-echo.yaml:65-66`.
- Consequence: in a shared network namespace, `POD_IP:8080` is local. B6 holds literally (nothing on loopback but the proxies). The workload can still open runs on its own chassis, like any in-cluster caller, because 8080 is open to any source.
- Fix: record it. H13 must test both `127.0.0.1:8080` (refused) and `POD_IP:8080` (served, by design).

### F13 (Low): the gVisor install trusts the publisher's channel once, and installs zstd without a pin.

- Where: `deploy/kind/poc05/install-gvisor.sh:25,52-56`.
- What it does: checks the published `.sha512` and also a pinned sum (good). The pinned sum was itself read from the same channel, so trust was established once by the spike. It then runs `apt-get install zstd` as root in the node, with no version pin, after the check. That is acceptable because apt verifies signatures.
- x86_64 fails closed: an empty `GVISOR_SHA512_X86_64` fails the `^[0-9a-f]{128}$` test at line 43 and the script dies before any download.
- Fix: bake a node image later. For now, record the trust once it is established.

### F14 (Low): the remote token travels in plain HTTP both ways on the pod network.

- Where: `agents/chassis/echo-remote.yaml:21`, `remote/remote-echo.yaml:74-76`.
- It is the same token in both directions (ADR-005 decision 1). mTLS is the recorded next step. On the single-node kind cluster, exposure is limited to the node.

### F15 (Low): the code runner and the fake servers have no auth of their own. NetworkPolicy is their only control.

- Where: `tools/network-policy.yaml`, `platform/network-policy.yaml:94-129`.
- Consequence: if the CNI does not enforce policy, every pod can run code. Covered by verification item 1. Rule 7 (F3) should also require gVisor in `poc05-tools`, which admission does not check today (code-runner is in the "other" lane).

### F16 (Low): seed and key details.

- Where: `seed.sh:164-173` and `seed.sh:252-256`.
- `key_body` sets no `allowed_routes`. Set `allowed_routes` to the LLM and MCP routes, so a leaked key cannot reach management routes.
- Verify on the cluster that a key with no `object_permission` lists zero MCP servers. Some LiteLLM releases fall back to all servers.
- `rotate remote-*-token` is a hard cut with no previous-token step, unlike the plan's runbook. That is functional, not a leak.

## Bring-up blockers (not security, but bring-up will fail)

- `run.sh:25-30`: `IMAGES` lacks `fake-mcp-server` and `code-runner`. Their pods use `imagePullPolicy: Never`, so they stop at `ErrImageNeverPull`.
- `platform/litellm.yaml:63-66`: `HOME=/tmp` with no egress. Prisma may look for its engines under the new HOME and try to download them, and the start fails. Do not open egress. Point Prisma at the binaries baked into the image, or keep the image's cache path on a read-only mount.
- `smoke/smoke.yaml`: the busybox smoke pods carry no trust label and no `agent-platform/` image. Once the binding is applied, rules 1 and 3 reject them. Run smoke before admission, or give smoke its own namespace without the binding.
- The `run.sh` verbs for admission, platform, seed, and apply do not exist yet. The apply order in `policy.yaml:5-9` must be kept, and a check that the policy compiled must come before the binding.
- `pocs/poc-05-sandboxed/tests/test_poc05_netpol_static.py` (T20) does not exist. It is the named-deny fallback for B3 and B4 (no `ipBlock`, edges equal the table), so it must exist before any B3 or B4 claim.

## Answers by question

### Q2. NetworkPolicy walk

- Every PoC-5 namespace has default deny both ways (`base/default-deny.yaml`). No `ipBlock` anywhere (grep).
- Edges against plan section 2.10:
  - agent-echo: DNS, LiteLLM 4000, Valkey 6379. Narrower than the table (no MinIO, on purpose).
  - chassis-echo-remote: the same, plus remote-echo:9000. Ingress 8080 from any source; 8091 from `remote-echo` in `poc05-remote` only.
  - remote-echo: ingress 9000 from chassis-echo-remote only; egress 8091 to chassis-echo-remote only; no DNS.
  - code-runner: ingress 8000 from LiteLLM only; no egress at all.
  - LiteLLM: ingress 4000 from chassis pods; egress DNS, Postgres, fake model 8081, fake MCP 8082, code-runner 8000.
  - Postgres, fake model server, fake MCP server: ingress from LiteLLM only.
  - Valkey: ingress from chassis pods.
  - MinIO: ingress from chassis pods (unused, F10) and from `minio-init` (off the table, documented).
  - minio-init: egress DNS and MinIO.
  - Nothing is wider than the table except the unused MinIO edge. The real widening is label forgery (F3), not an edge.
- Missing edges that would fail bring-up: none found. This assumes the policy sees the pod after the Service rewrite, as the spike showed, for the remote's ClusterIP path to 8091 and the chassis's path to `remote-echo:9000`. The spike did not test a gVisor pod as an ingress target (spike line 105). remote-echo is one.
- DNS only where needed: the agent pods, LiteLLM, and minio-init. No DNS for remote-echo, code-runner, Postgres, Valkey, MinIO, or the fake servers. The sidecar workload inherits the chassis's DNS (H20, a recorded blind spot).
- The sidecar's shared network namespace: the workload reaches the proxies at 127.0.0.1:8090 (by design), the chassis's public port at `POD_IP:8080` (F12), LiteLLM and Valkey (by key or ACL, B1), and kube-dns. It does not reach the metadata service, the API, Postgres, the fake servers, MinIO, the code runner, or the remote. That is as designed.
- Kubelet probes: kube-network-policies on kindnet filters traffic forwarded between pods. The kubelet probes from the node's own network namespace, which PoC-4's default deny on the same CNI did not block. The proof is every pod reaching Ready: the TCP probe on gVisor remote-echo, httpGet on the code runner, exec on the sidecar workload.

### Q3. Admission

- Shapes covered:
  - Init and ephemeral containers are counted (`policy.yaml:78-82`).
  - Every workload kind is matched, the Pod is matched as the last hop, and UPDATE is matched, so a later image patch or label patch is checked again.
  - A lookalike such as `agent-platform/chassis-evil` is a workload image, so rule 4 applies. Digests and tags are both accepted.
- Gaps: F1 (pull source), F2 (the chassis image as a carrier, Secrets), and F3 (labels and namespace).
- Fail-closed:
  - `failurePolicy: Fail`;
  - `parameterNotFoundAction: Deny`;
  - rule 0 rejects empty params;
  - the submitter cannot edit the params, namespaces, or admission objects (`rbac.yaml`).
  - The binding selects on a namespace label the submitter cannot change.
- Recommendation for the Secret gap: use both. The namespace split is the fix: the submitter has no pod create where chassis Secrets live. Rule 6 is the backstop: only the single chassis container may reference a Secret, and it may set no `command`. Record the general exception (a pod creator reads every Secret in its namespace) in ADR-005. A recorded exception alone is not enough, because F2 also covers arbitrary code from a trusted image.

### Q4. Secrets

- No value is in any file (grep plus the seed static test).
- `seed.sh` follows its rules:
  - values come from `openssl rand` through command substitution;
  - they are written only with the `printf` builtin into pipes;
  - `curl -H @-` takes the master key from stdin;
  - `kubectl create secret --from-file=K=/dev/stdin --dry-run=client | apply --server-side` leaves no last-applied copy;
  - there is no `set -x`;
  - the only temp file is port-forward output;
  - a failed key mint prints only LiteLLM's error message.
- Secrets per container:

| Container | Secrets it reads |
| --- | --- |
| LiteLLM | `litellm-master`, `litellm-db` (env) |
| Postgres | `litellm-db` (file) |
| Valkey | `valkey-auth` (the ACL hash file only) |
| MinIO | `minio-root` |
| minio-init | `minio-root`, `minio-chassis` |
| agent-echo chassis | `chassis-echo-litellm`, `valkey-auth` |
| agent-echo workload | none |
| chassis-echo-remote | `chassis-echo-remote-litellm`, `valkey-auth`, `remote-echo-token` |
| remote-echo | `remote-echo-token` only (its own credential, not an internal service's) |
| code-runner, fake servers | none |

- Seeded but unmounted: the two probe keys, both `remote-probe-token` copies, and `minio-chassis` in agents. F2 can steal all of them.
- Hard requirement 1 holds by manifest. Only chassis containers hold internal credentials. It does not hold by admission yet (F2).
- `mc` argv: accept as a recorded exception (F11).

### Q5. Remote listener and remote token

- The chassis's check:
  - `hmac.compare_digest` against every token (current and previous), so the timing does not say which one matched;
  - more than one `Authorization` header is refused;
  - the header is stripped before the route;
  - the log carries only the method and path;
  - the counter reason is `missing` or `wrong`;
  - a refusal gets 401 with one fixed body and `WWW-Authenticate: Bearer`;
  - a missing run gets 403 `run_required` with one fixed body, and is counted.
  - Gap: non-HTTP scopes (F4).
- The workload's check: one fixed 401 body, websocket closed with 1008, the header stripped before a2a-sdk's DEBUG header logging, the token never in an error. The remote connector never follows a redirect and never follows the card's URL.
- What an authenticated remote can still do:
  - call `/v1/chat/completions` and `/mcp`, only while a run is in flight in its chassis;
  - spend up to the run's `remaining_tokens`, on the routes the key allows (`fake-chat`), within the key's 1.0/day and 600 rpm;
  - call the key's tools: `glossary_lookup`, `note_write`, and `run_python`. Two of those are write tools, and an idempotency key is required.
- What it cannot do: no client header reaches LiteLLM (the body is parsed into typed messages). It cannot open runs. Runs come from callers on 8080, which takes any caller with no auth, so spend is driven by callers.
- With one remote per chassis, "any run in flight" is that remote's run. If one chassis ever fronts two remotes, runs must be bound to the token.

### Q6. Hardening

- Every pod template meets section 2.11:
  - `automountServiceAccountToken: false` on pod and ServiceAccount;
  - `enableServiceLinks: false`;
  - non-root with numeric uids: chassis 10001, workload 10002, code-runner 10003, LiteLLM 10010;
  - read-only root, drop ALL, no privilege escalation, seccomp RuntimeDefault (pod or container);
  - CPU and memory limits everywhere;
  - per-container memory-backed `/tmp` with a `sizeLimit` on the agent, remote, and tool pods;
  - no `shareProcessNamespace` and no host namespaces;
  - restricted PSA on every namespace.
- Platform pods use disk-backed emptyDirs (LiteLLM tmp, Postgres data, MinIO data), with limits enforced by eviction. That is acceptable.
- gVisor: see F13. `oci-seccomp` is on, so RuntimeDefault applies inside the sandbox.
- Image pins: see F8.
- Code-runner limits: CPU, address space, files, and file size per process; a 10 s wall clock; a 64 KiB output cap; one run at a time. The pod is the boundary. Isolation between calls is not (F5).

### Q7. Chassis as the native sidecar

- Net positive: it closes the first-start squat on 8090 and 8080 (H14), because the main container starts only after the chassis has bound its ports and passed `/health`.
- Costs:
  - the restart window (F6);
  - the drain order is reversed (the workload gets SIGTERM first), which costs availability, not security.
  - The chassis's Secrets stay in the chassis container. Init containers get no extra privilege.

## B table

| B | Control now | Where it lives | Correct as written? |
| - | ----------- | -------------- | ------------------- |
| B1 | LiteLLM `master_key` plus virtual keys (seed); no `allow_all_keys` (kind and Compose); Valkey `default` user off with an ACL user; MinIO key pairs; fake servers and code-runner closed by policy only | shared service plus network | Yes for services. The network half rides forgeable labels (F3). The per-key MCP fields are UNVERIFIED. Kafka (H11) is deferred. |
| B2 | default deny egress; no internet edge anywhere | network | Yes. Needs CNI proof (item 1). |
| B3 | no `ipBlock` anywhere; ANP absent; named in a comment | network | Implicit only. The T20 static test is missing. Cannot be shown on Docker Desktop (spike). |
| B4 | `automountServiceAccountToken: false` everywhere; no edge to the node on 6443; submitter has no Secrets or API RBAC | runtime plus network plus RBAC | Yes. Admission does not forbid a projected SA token outside the remote lane (low; the SAs have no bindings). |
| B5 | separate containers, separate uids, no shared volume, Secrets in the chassis container only | runtime | Yes for the manifests. Not enforced at admission (F2). |
| B6 | public port on `$(POD_IP)`, proxies on loopback | chassis config | Yes, literally. Reachable through the pod IP (F12). |
| B7 | chassis as the native sidecar; bind before `/health`; exit 3 if a port is taken | runtime ordering plus chassis | First start: yes. Restart window open (F6). |
| B8 | BearerAuth plus RequireRun on 8091; 8091 ingress from the remote's label only | chassis plus network | Code: yes, except non-HTTP scopes (F4). Network: label forgeable (F3). |
| B9 | RuntimeClass gvisor (rule 5 requires it for the remote shape); restricted PSA; per-container hardening; oci-seccomp | runtime | Yes. gVisor is not required at admission for `poc05-tools` (F15). |
| B10 | CPU and memory limits; `podPidsLimit` 256; `/tmp` `sizeLimit` | runtime (cgroups) | Yes. Under gVisor the pid cap kills the sandbox (a DoS of the pod, by design). The code runner adds no `RLIMIT_NPROC` (F5). |
| B11 | per-key models, budget, and rpm; per-key MCP allow-list; RequireRun plus the run budget; uncorrelated cap on loopback | shared service plus chassis | Yes, pending the LiteLLM field check. No `allowed_routes` on keys (F16). |
| B12 | none beyond B1 auth; one shared Valkey ACL user with `~*` | not closed | Open (PoC-8). Do not claim it. |

## Cluster verification, in priority order (each refusal with its paired allowed control)

1. **The CNI enforces, including for a gVisor pod as an ingress target.** Run the smoke `c_netpol_denied` and `c_netpol_allowed` checks. Then a pod not labeled as the chassis tries remote-echo:9000 and must time out. Control: chassis-echo-remote fetches the remote's card with the token and gets 200. If the control fails, the policy is too broad. If the refusal passes, the CNI is not enforcing.
2. **gVisor is really in use.** `/proc/version` shows the gVisor marker in remote-echo and the code runner, and `Seccomp: 2` there. Control: a runc pod shows no marker. Also check that `runtimeClassName` is set on the Sandbox-created pods and that they carry the podTemplate labels (F7).
3. **Admission on the server.** Run every fixture with `--dry-run=server`. Each rejected fixture must fail with its exact message, and each twin must be admitted. Add the F1, F2, and F3 cases, the ephemeral-container case (as admin, so that the policy and not RBAC refuses it), and one Sandbox, Deployment, and CronJob. Params removed: every request is denied. Params restored: the twin is admitted. Also `kubectl auth can-i` for the submitter in each namespace.
4. **B1 from the sidecar workload container, using the Python standard library.**
   - LiteLLM with no key: 401. Random key: 401.
   - MCP `tools/list` with no key: 401, or zero tools. A minted key with no `object_permission`: zero servers.
   - Control: the same calls through 127.0.0.1:8090 get 200, and the list is exactly the allow-list, without `unlisted_probe` (H05 to H08).
5. **B8, H17 and H18, from remote-echo.** 8091 with no token: 401. Wrong token: 401. Right token with no traceparent: 403 `run_required`. Control: the right token inside a run gets 200. From a smoke pod in `poc05-remote` without the remote-echo label: a timeout (H18). Control: remote-echo connects.
6. **H09 and H10.** Workload to `fake-model-server:8081`: a timeout. Control: a model call through the chassis gets 200. Valkey with no AUTH: `NOAUTH`. Wrong password: `WRONGPASS`. Control: the chassis's state round trip works.
7. **B3 and B4 by literal IP.** From both lanes, the node IP (read from the `kubernetes` EndpointSlice) on 6443 and 10.96.0.1:443 must time out. The SA token directory must be absent. Control: an admin pod in a scratch namespace with an allow rule connects to the same address. That proves the address is live, so the timeout is the policy.
8. **H19 and H20 from remote-echo.** A literal public IP on 443 and a DNS query to the kube-dns IP get no answer. Control: the node (`docker exec`) reaches the same IP, and the chassis resolves names.
9. **The code runner (F5).** No live process from an earlier call. `/tmp` fills only to its cap. No egress by literal IP. Control: a normal `run_python` returns its output.
10. **Every pod reaches Ready under default deny.** This is the proof that kubelet probes pass on kindnet.
11. **No value in any log.** Search the pod logs for LiteLLM, the chassis, the remote, and minio-init for the live Secret values. Do it in a script that prints only the count of matches, never a value. The expected count is 0. Control: the same script finds a planted canary string.
12. **The F6 restart window.** Kill the chassis process in agent-echo and record who owns 8080 and 8090 while it restarts. Record the result. Do not claim B7 for restarts.
