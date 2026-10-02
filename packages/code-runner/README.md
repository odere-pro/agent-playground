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

## Limits recorded

- A restart forgets every idempotency key.
- The result cache can hold up to 256 x 128 KiB of output in the worst case; it counts against the pod's memory limit.
- The rlimits are hygiene. The hard limits are the pod's.
- Isolation between calls is per process, on Linux only (above). A per-call sandbox is the stronger design and is noted for later (plan 2.8: one long-lived sandbox fits the memory budget).
- The sweep stops every process started since the call began, so it relies on nothing else in the pod starting processes.
- Not yet verified under gVisor: `PR_SET_CHILD_SUBREAPER`, `PR_SET_DUMPABLE`, and `RLIMIT_NPROC` enforcement. The kind test covers the sweep.

## Run it

```bash
uv run code-runner --port 8000              # local only, for manual checks
docker build -f packages/code-runner/Dockerfile -t agent-platform/code-runner:poc05 .
uv run pytest packages/code-runner -q        # offline, in-process MCP client
```

The image runs as uid 10003 with a read-only root. It needs `/tmp` writable (the pod's memory-backed `emptyDir`).
