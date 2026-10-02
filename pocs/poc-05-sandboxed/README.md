# PoC-5: Sandboxed: the remote lane and the trust rule

Status: in progress
Planning doc: [005-PoC-5-sandboxed.md](../../docs/planning/poc/005-PoC-5-sandboxed.md)
Time box: 3 weeks

## Question

Can untrusted code, including code that a framework writes and runs, run in the `remote` lane without reaching secrets, the internet, the disk, or tools it is not allowed to use? And does a workload in the `sidecar` lane reach internal services only through the chassis?

## Scope

- [ ] Container hardening for both containers and the remote pod: non-root user, read-only root file system, all capabilities dropped, default seccomp profile, CPU, memory, and process limits. No service account token in the workload: `automountServiceAccountToken: false`, or the token projected into the chassis container only.
- [ ] A local kind cluster with native sidecars, a CNI that enforces NetworkPolicy, and a gVisor RuntimeClass.
- [ ] The `remote` connector: the a2a-sdk client from PoC-2, with its URL and auth from config.
- [ ] The proxies for the `remote` lane: the chassis's model and tool proxies also listen on the pod IP, and each remote authenticates (suggested: mTLS through cert-manager, or a per-remote token from the chassis config). Only the remote pod's label may reach them. A managed runtime that cannot be pointed at the proxies holds its own scoped key, the one relaxation of hard requirement 1 (PoC-6b).
- [ ] A remote workload pod on gVisor (`runsc`), through `kubernetes-sigs/agent-sandbox`: no secrets mounted, default-deny egress, and only the chassis pod may call it. It reaches only the chassis's model and tool proxies, with its own credential. The chassis pod stays outside the sandbox.
- [ ] Hard requirement 1 on the kind cluster: LiteLLM, the MCP gateway, the broker, Valkey, and the config store refuse calls without the chassis's credential.
- [ ] One scoped LiteLLM virtual key per service, and an MCP gateway tool allow-list per key. The credentials are mounted in the chassis container only.
- [ ] A default-deny NetworkPolicy per service pod. It allows only what the chassis needs, and it names the cloud metadata service and the Kubernetes API in the deny list. The chassis's public port binds to the pod IP, so localhost carries only the proxies.
- [ ] `spec.trust: trusted | untrusted` in the agent config, and an admission check (Kyverno or ValidatingAdmissionPolicy). It blocks an `untrusted` workload, or an image not from our registry, in the `sidecar` lane.
- [ ] `ToolPort` is defined here, in the same shape as the PoC-1 ports, with its fake and contract suite. Then the real adapter (MCP through LiteLLM's MCP gateway), passing the same suite as the fake tools. `mode: write` tools need the `idempotency_key`.
- [ ] Generated code runs through the code-execution tool behind `ToolPort` (agent-sandbox, or E2B if needed), so it does not make an agent untrusted.
- [ ] A hostile suite for the `sidecar` lane: the workload cannot reach LiteLLM, the MCP gateway, the broker, Valkey, the config store, or the internet on its own. It holds no service account token, and cannot reach the cloud metadata service, the Kubernetes API, or the chassis's public port on localhost.
- [ ] A hostile suite for the `remote` lane: the workload cannot read secrets, reach the internet, write to disk, fork-bomb, call a tool that is not allow-listed, or use the chassis's proxies without its own credential.
- [ ] CI runs a fake workload through the `remote` lane on every commit.
- [ ] Optional: Envoy as an egress proxy for other allow-listed hosts. The chassis proxies already add the credentials for models and tools, so Envoy is not needed for them.

## Exit criteria

Each one has a scenario test in `tests/` or a recorded reason it cannot have one yet.

- [ ] The hostile suites run in CI; the parts that need no cluster run offline on every commit.
- [ ] CI runs a fake workload through the `remote` lane on every commit, and it passes the same contract suite as the `sidecar` lane.
- [ ] From the workload container, every internal service refuses a call without the chassis's credential. Through the chassis, the same calls succeed.
- [ ] No provider key or internal credential is visible in any workload container or remote pod, and neither holds a service account token.
- [ ] From the workload container, the cloud metadata service, the Kubernetes API, and the chassis's public port on localhost are unreachable.
- [ ] The remote pod reaches only the chassis's model and tool proxies, and only with its own credential.
- [ ] The admission check rejects an `untrusted` workload, and a third-party image, in the `sidecar` lane.
- [ ] The hostile suites pass in both lanes.
- [ ] gVisor works for every engine that runs in the `remote` lane (or the exceptions are listed), and its latency and memory overhead are measured.
- [ ] What the chassis cannot see or control in the `remote` lane is listed.

## How to run

```bash
make test-poc POC=05
```

## Demo

Not recorded yet. The script and its output go to `demo/`.

## Notes and decisions

Dated files in `notes/`. `notes/backlog-changes.md` lists what the backlog should change when this iteration closes.
