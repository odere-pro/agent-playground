# gVisor overhead for the remote-lane engines (2026-10-09)

Exit criterion 9: "gVisor works for every engine that runs in the `remote` lane (or the exceptions are listed), and its latency and memory overhead are measured." Task T23. The record test is `tests/test_poc05_records.py`.

The [spike](2026-10-02-kind-sandbox-spike.md) (section 7, `spike/q7-overhead.sh`) measured only an idle busybox. This note measures the real images on the PoC-5 cluster `kind-poc05`.

## Method

Command, run from the repo root on the Mac:

```
pocs/poc-05-sandboxed/notes/spike/q9-overhead-engines.sh
```

- **Pods.** Each image runs as a plain Pod in the scratch namespace `poc05-q9-overhead`. The script makes that namespace (PSA `restricted`, no admission label) and deletes it at the end. Each image runs once with `runtimeClassName: gvisor` and once on the default runc. The pods copy the manifests' hardening and resources:
  - echo-python as in `remote/remote-echo.yaml`: 256Mi limit, bearer required.
  - code-runner as in `tools/code-runner.yaml`: 256Mi limit and request.
- **gVisor.** runsc `release-20260928.0`, platform `systrap`, `oci-seccomp = "true"` (the node's `/etc/containerd/runsc.toml`).
- **Ready.** The wall time from before `kubectl apply` to the return of `kubectl wait --for=condition=Ready`, N=5. Each sample pod is deleted before the next.
  - The figure includes two kubectl starts and a `python3` timer call.
  - The readiness probe runs every 1 s, so a figure moves in steps of about 1 s.
- **Request.** N=50 sequential GETs, one new connection each, after 5 warmup requests. They come from a runc client pod in the same namespace, using echo-python's own Python.
  - echo-python: `/.well-known/agent-card.json` with the bearer.
  - code-runner: `/health`.
- **run_python.** Code-runner only: N=10 MCP `tools/call` of `run_python` with `print(1)`, each with a fresh idempotency key. Each call forks a Python child in the sandbox.
- **Memory.** The pod cgroup on the node after the requests and a 5 s settle. This is the bring-up item 10 method ([bring-up note](2026-10-02-bring-up.md), section 10).
  - It records `memory.current`, the working set (`current - inactive_file`), and `memory.peak`, in MiB.
  - For a gVisor pod the cgroup holds the whole sandbox: the sentry and the gofer.
- **Kernel.** The pod's `platform.release()`. gVisor reports its own kernel; runc reports the node's.
- **Echo token.** A random throwaway token, piped into a Secret in the scratch namespace and never printed. It is deleted with the namespace.

## Result

Run 2026-10-09 14:53:15Z to 14:55:13Z, 117 s, exit 0, `grep -c sk-` on the log: 0. Load, node 1/5/15 min: 2.37 1.59 5.35 at the start, 1.99 1.83 5.00 at the end. Mac: 5.94 at the start, 3.35 at the end.

```
echo-python runc startup ms: 2746 2934 2932 2939 2929
echo-python gvisor startup ms: 5140 4928 4927 4943 4940
code-runner runc startup ms: 3120 2941 2936 2899 2960
code-runner gvisor startup ms: 4187 3919 3924 3954 3929
```

| Engine | Runtime | Kernel | Ready ms, median (min-max), N=5 | Request ms p50 / p95, N=50 | run_python ms p50 / p95, N=10 | current MiB | working set MiB | peak MiB |
| --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| echo-python | runc | 7.0.14-linuxkit | 2932 (2746-2939) | 0.6 / 0.8 (err 0) | - | 64 | 64 | 75 |
| echo-python | gvisor | 4.19.0-gvisor | 4940 (4927-5140) | 0.9 / 1.2 (err 0) | - | 97 | 97 | 107 |
| code-runner | runc | 7.0.14-linuxkit | 2941 (2899-3120) | 0.4 / 0.5 (err 0) | 11.0 / 64.6 (err 0) | 67 | 67 | 73 |
| code-runner | gvisor | 4.19.0-gvisor | 3929 (3919-4187) | 0.9 / 1.1 (err 0) | 85.3 / 89.9 (err 0) | 105 | 105 | 110 |

## Readings

- **Both remote-lane images work under gVisor.** Every gVisor pod reached Ready and answered every request with no error, and its kernel is gVisor's. The kind suites also run both on gVisor: the remote suite and the code runner, `test_poc05_kind_remote_controls.py::test_h28_remote_runs_on_gvisor_next_to_a_runc_pod` ([close runs](2026-10-09-close-runs.md)).
- **Start time.**
  - echo-python takes about 2.0 s longer under gVisor: 4.9 s against 2.9 s, +68 %.
  - code-runner takes about 1.0 s longer: 3.9 s against 2.9 s, +34 %.
  - A per-call sandbox comes from the warm pool, so a call pays this cost only on a cold start ([bring-up note](2026-10-02-bring-up.md), claim latency).
- **Request latency.** An HTTP request adds about 0.3 to 0.5 ms at p50 under gVisor. That is noise next to a model call.
- **Process start.** `run_python` forks a Python child per call. Its p50 rises from 11 ms on runc to 85 ms under gVisor, about +74 ms per call. This is the largest overhead in the table and the one a code-execution call pays every time. The runc p95 (64.6 ms) is one slow call out of 10.
- **Memory.** A gVisor pod's working set is 33 MiB (echo-python) to 38 MiB (code-runner) above runc for the same image: the sentry and the gofer. Both stay well under their 256Mi limit.

## Exceptions

- **echo-typescript is an exception, not measured.** Its A2A server checks no inbound token (`packages/workloads/echo-typescript/README.md`). So it cannot be a remote yet; see the [blind-spots note](2026-10-02-blind-spots.md), section 3, and plan section 2.4. Owner: PoC-6.
- **echo-pydanticai and echo-langgraph are not measured.** Both serve through `workload-a2a`, which has `--require-token-env`, and both have a Dockerfile. But neither is built or loaded for kind in PoC-5 (`IMAGES` in `deploy/kind/poc05/run.sh`), and neither runs in the remote lane here. The plan's T23 row lists them. Measuring them means adding them to `IMAGES` and to the script. suggested: PoC-6, with the first remote that uses them.

## What the numbers do not cover

- Only the `systrap` platform, on one kind node in Docker Desktop on an Apple-silicon Mac. KVM is not measured.
- No model call: the fake model and real models are out of scope. A run's end-to-end time under gVisor is not measured.
- N=5 starts and N=50 requests are small samples. The figures are for orders of magnitude, not a benchmark.

## Exception: the script's own token

Owner: `platform-security`. Accepted in the security review of the close ([review](2026-10-09-review-security-cluster.md), "The overhead script's token").

`q9-overhead-engines.sh` makes its own random token in a scratch namespace, an exception to "Secrets come from the seed script only". It is piped through stdin, never in argv, authenticates nothing real, and is deleted with the namespace. Using the seed's token would copy a live credential.

The scratch namespace gets default-deny plus a same-namespace allow (added after the measured run).
