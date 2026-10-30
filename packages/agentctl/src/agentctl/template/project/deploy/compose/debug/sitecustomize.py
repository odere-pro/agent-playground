"""Start debugpy in a container's service process when `DEBUGPY_LISTEN` is set.

The `debugpy` service in `../docker-compose.yaml` copies debugpy and this file into a named volume.
The chassis and the workload mount it read-only at `/opt/debugpy` and set `PYTHONPATH` to it, so
every Python process in those containers imports this file at start. It acts only in PID 1, the
service process (each entrypoint `exec`s it). Healthchecks, the entrypoint's address lookup, and
debugpy's own adapter are other processes, so they skip it and never take the port.

No image changes: the chassis image stays the pinned one, the workload image stays the one the
root `Dockerfile` builds. Without the volume and the variables, nothing here runs.

- `DEBUGPY_LISTEN`: `<host>:<port>`, or `auto:<port>` for the container's own address. Not
  loopback: Docker forwards a published port to the container address. Empty: no debugger.
- `DEBUGPY_WAIT=1`: block until a client attaches. Off by default.

debugpy has no authentication. Whoever reaches the port runs code in the process. Compose
publishes it on the host's 127.0.0.1 only; README.md says who else can reach it.
"""

from __future__ import annotations

import importlib
import os
import socket
import sys
from collections.abc import Mapping

LISTEN_VAR = "DEBUGPY_LISTEN"
WAIT_VAR = "DEBUGPY_WAIT"
SERVICE_PID = 1


def listen_address(value: str) -> tuple[str, int]:
    """Parse `host:port`; `auto` or an empty host means the container's own address."""
    host, sep, port = value.rpartition(":")
    if not sep or not port.isdigit():
        raise ValueError(f"{LISTEN_VAR} must be <host>:<port> or auto:<port>, got {value!r}")
    if host in ("", "auto"):
        host = socket.gethostbyname(socket.gethostname())
    return host, int(port)


def start(
    environ: Mapping[str, str] | None = None, pid: int | None = None
) -> tuple[str, int] | None:
    """Listen when this is the service process and a listen address is set; return it."""
    env = os.environ if environ is None else environ
    value = env.get(LISTEN_VAR, "").strip()
    if not value or (os.getpid() if pid is None else pid) != SERVICE_PID:
        return None
    try:
        address = listen_address(value)
        debugpy = importlib.import_module("debugpy")
        debugpy.listen(address)
    except (ImportError, OSError, ValueError) as exc:
        # A broken debugger must not stop the service; say why, once, on stderr.
        print(f"debugpy: not started: {exc}", file=sys.stderr, flush=True)
        return None
    print(f"debugpy: listening on {address[0]}:{address[1]}", file=sys.stderr, flush=True)
    if env.get(WAIT_VAR, "0") == "1":
        print("debugpy: waiting for a client (DEBUGPY_WAIT=1)", file=sys.stderr, flush=True)
        debugpy.wait_for_client()
    return address


start()
