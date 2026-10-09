# code-runner

The code-execution tool: an MCP server with one write tool, `run_python`. It runs in an agent-sandbox `Sandbox` pod on gVisor, behind LiteLLM's MCP gateway. An agent reaches it only as a tool, through the chassis's tool endpoint, so the gateway's per-key allow-list applies to it like any other tool. Design: `docs/plans/2026-10-02-poc-05-sandboxed.md`, section 2.8.

## The sandbox pod is the security boundary

This server is not a sandbox. It runs whatever Python it is given. The isolation comes from the pod: gVisor (`runsc`), no network egress at all (not even DNS), a read-only root, a non-root user (10003), all capabilities dropped, the pod PID limit, the memory limit, and a small memory-backed `/tmp`. The pod holds no credential.

Never run `code-runner` outside such a pod in a deployed lane. Running it on a host, or in a pod with egress or a mounted secret, gives the code that host or that secret.

## The tool

`run_python` (MCP, streamable HTTP at `/mcp`; `GET /health` for probes).

| Input | Type | Rule |
| ----- | ---- | ---- |
| `code` | string | Python source, run as `__main__`; at most 64 KiB (suggested) |
| `timeout_s` | int | wall-clock limit, 1 to 10, default 5 (suggested) |
| `idempotency_key` | string, optional | fallback only, when the client cannot send `_meta.idempotency_key` |

Output (structured content): `stdout`, `stderr`, `exit_code`, `timed_out`, `truncated`. Each stream is cut at 64 KiB (suggested); `truncated` is true when either was cut. `exit_code` is negative when a signal ended the child (`-9` after a timeout kill). An uncaught exception in the code is a normal result (`exit_code: 1`, the traceback in `stderr`), not a tool error.

It is a write tool (`readOnlyHint: false`): running code is not a pure read, and a retry must not run it twice. Every call needs an idempotency key, in `_meta.idempotency_key` (preferred) or in the argument. The server keeps the last 256 results by key (suggested), in memory. The same key with the same arguments returns the first result without running again, also while the first call is still running. The same key with other arguments is refused with `bad_arguments`, never answered with the first result (plan section 2.6 does not say otherwise; the review's L3 picks refusal as the safer rule for the fakes and the contract suite too).

A run is shared by every caller of its key. If the first caller is cancelled (a client disconnect), the run goes on for the others, and they get its result. When the last caller is gone, the run is stopped, its child killed, and the key forgotten, so a retry runs the code again.

## Errors

Tool errors (MCP `isError`) start with a stable code:

- `idempotency_key_required`: no key in `_meta` or the argument.
- `bad_arguments`: code over the size cap, `timeout_s` out of range, a key over 256 characters, `_meta` and argument keys that differ, or a key reused with other arguments.
- `run_failed`: the interpreter could not start, the call stopped before a result, a process from the call could not be stopped, or isolation was lost earlier (see below).

The dispatcher (below) adds two:

- `sandbox_unavailable`: no free slot, the claim could not be created, the sandbox was not `Ready` within 20 s (suggested), or its `podIPs[0]` is not a pod address. Nothing ran; a retry with the same key is safe.
- `sandbox_lost`: the sandbox ended, did not answer within `timeout_s + 5` s (suggested), answered over 256 KiB (suggested), or gave no usable answer. The key is forgotten, so a retry runs in a fresh sandbox.

The text after each code is a fixed string. No upstream body and no token goes into a message or a log line. A tool error from the sandbox's own server keeps only its code.

## Each call

1. A fresh directory `run-*` under `/tmp` (mode 0700); the code is written there as `main.py`.
2. A child `python -I -S` (isolated mode, no site packages: the standard library only). No shell, empty stdin, a new session.
3. The child's whole environment is `PATH`, `HOME` and `TMPDIR` (the fresh directory), and `LANG`. Nothing from the server's environment reaches it.
4. Before the code runs, the child sets `setrlimit`: CPU time (`timeout_s + 1` s), address space (256 MiB), open files (64), file size (8 MiB), and on Linux processes (`RLIMIT_NPROC`, below). Off Linux a limit the kernel refuses is skipped; on Linux that stops the run (exit 125).
5. At `timeout_s` the whole process group is killed. It is killed again after the child exits.
6. On Linux, every other process the call started is killed and reaped (the sweep, below), also on error or cancel.
7. The directory is removed, also on error or cancel. Only then may the next call start.

One run at a time, so memory stays predictable in a 256 MiB pod and the sweep knows what is the call's. Other calls wait.

## Isolation between calls

Calls are isolated per process, not per sandbox. One long-lived pod serves every call, and every child runs as the server's uid 10003 with no capabilities. So there is no per-call uid and no per-call PID namespace. What holds, on Linux only:

- **No process outlives its call.** The server is a child subreaper (`PR_SET_CHILD_SUBREAPER`), so a process that leaves the call's process group with `setsid()` and loses its parent is reparented to the server, not to init. After the child exits, the server reads `/proc`, kills and reaps every process started since the call began, and repeats until none is left. Then the directory is removed and the next call may start. So no code from one call is running while another call's directory exists.
- **If a process cannot be stopped** within 5 s (suggested), the call fails with `run_failed`, every later call is refused, and `GET /health` returns 503 `isolation_lost`, so the liveness probe restarts the pod.
- **The server is not dumpable** (`PR_SET_DUMPABLE=0`): a child with the same uid cannot ptrace it or read its memory through `/proc/<pid>/mem`, where the result cache holds other calls' output.
- **`RLIMIT_NPROC`** on the child, so runaway process creation fails inside the call instead of reaching the pod's PID limit (under gVisor, the whole sandbox). The kernel counts every task of the real uid, and the server shares that uid, so the limit is the uid's task count when the call starts (the server's own threads included) plus 32 (suggested). A fixed small number would refuse the child's first fork.

What follows from "per process, not per sandbox":

- Mode 0700 and a random directory name do not protect one call's directory from another call: both run as the same uid, which can always open its own directories. The protection is that no process from an earlier call is left alive.
- A file a call writes outside its own directory (for example straight into `/tmp` or `/dev/shm`) is not removed, and a later call can read it. Code that wants nothing kept must write only in its working directory (`TMPDIR` and `HOME` point there).
- While a call runs, its code can signal the server (same uid), which only ends the pod. It can read what the server can read in the pod; the pod holds no credential.
- The server must run one call at a time. `run_python` refuses a second call in the same process while one runs.

Off Linux (a developer's Mac) there is no subreaper and no `/proc`. Only the process-group kill applies, and the server logs a warning once. A process that calls `setsid()` can outlive the call there. **The guarantee holds on Linux only.**

## Dispatch mode: a sandbox per call

`code-runner dispatch --pool code-runner --namespace poc05-tools` runs the dispatcher (`code_runner/dispatch.py`). It serves the same `run_python` tool, the same arguments, and the same result. LiteLLM registers it as `code_runner`. It never runs code itself. Design: `docs/plans/2026-10-09-poc-05-per-call-sandbox.md`.

Per call:

1. Take a slot (2 concurrent claims, suggested); wait for one up to 20 s.
2. `POST` a `SandboxClaim`: `generateName: call-`, `warmPoolRef` the pool, `lifecycle` with `shutdownPolicy: Delete` and `shutdownTime` now + 60 s (suggested). No `env`, no labels, nothing from the caller.
3. `GET` it every 100 ms (suggested) until `Ready` is `True` and `status.sandbox.podIPs` is set, for up to 20 s.
4. Parse `podIPs[0]` as an IP address (loopback, link-local, multicast, and unspecified are refused), then send one stateless `tools/call` to `http://<podIP>:8000/mcp` with the code, `timeout_s`, and the key in `_meta`. A refused connect is retried for up to 2 s (suggested), since `Ready` may come before the server listens.
5. `DELETE` the claim (background propagation) on every path, also on cancel. A failed delete is counted and logged; `shutdownTime` removes the claim later.

A pod is never reused. The idempotency cache lives in the dispatcher: a repeated key returns the first result without a claim. A failed call frees its key.

Two `httpx` clients, never shared, and no Kubernetes SDK:

- **API client:** base URL fixed at start from `KUBERNETES_SERVICE_HOST` and `KUBERNETES_SERVICE_PORT` (an IP and a port, else it refuses to start); the cluster CA (`--ca-file`) verifies the server; no redirects; nothing taken from the environment (`trust_env=False`). It is the only client that sends the token, read from `--token-file` on each request, since the projected token rotates. It sends only `create`, `get`, and `delete` on `sandboxclaims` in one namespace.
- **Sandbox client:** no auth of any kind, no redirects, `trust_env=False`; the response body is cut off at 256 KiB (suggested), which ends the call with `sandbox_lost`.

`GET /health` never calls the API, so a slow API server cannot get the dispatcher restarted.

## Limits recorded

- A restart forgets every idempotency key.
- The result cache can hold up to 256 x 128 KiB of output in the worst case; it counts against the pod's memory limit.
- The rlimits are hygiene. The hard limits are the pod's.
- Isolation between calls is per process, on Linux only (above), when the server serves calls itself. Dispatch mode gives each call its own sandbox instead.
- A dispatcher restart forgets every key, and its in-flight claims wait for `shutdownTime`.
- The sweep stops every process started since the call began, so it relies on nothing else in the pod starting processes.
- Not yet verified under gVisor: `PR_SET_CHILD_SUBREAPER`, `PR_SET_DUMPABLE`, and `RLIMIT_NPROC` enforcement. The kind test covers the sweep.

## Run it

```bash
uv run code-runner --port 8000              # local only, for manual checks
uv run code-runner dispatch --pool code-runner --namespace poc05-tools   # in the dispatcher pod only
docker build -f packages/code-runner/Dockerfile -t agent-platform/code-runner:poc05 .
uv run pytest packages/code-runner -q        # offline, in-process MCP client
```

The image runs as uid 10003 with a read-only root. It needs `/tmp` writable (the pod's memory-backed `emptyDir`).
