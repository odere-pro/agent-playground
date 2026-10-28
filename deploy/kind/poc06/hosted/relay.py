"""A local TCP relay to a chassis's public port on kind, for `hosted.sh run`.

Why it exists. The chassis binds its public port (8080) to the pod IP only (deploy/CLAUDE.md), and
`kubectl port-forward` dials 127.0.0.1 inside the pod, so a port-forward cannot reach it. The
PoC-5 and PoC-6 kind tests send the request from inside the chassis container instead
(`kubectl exec`, the image's own Python). This relay does the same for a plain HTTP client: it
listens on 127.0.0.1 and, for each connection, runs `kubectl exec -i` with a tiny Python program
that connects to $POD_IP:8080 and copies bytes both ways.

Usage: relay.py CHASSIS_DEPLOYMENT. It prints `port <n>` on stdout when it listens, and runs until
it is killed. Standard library only. It holds no credential and sends none: whatever the client
sends goes to the chassis, and the chassis needs no key for its public port.
"""

from __future__ import annotations

import contextlib
import os
import socket
import subprocess
import sys
import threading

CONTEXT = "kind-poc05"
NAMESPACE = "poc05-agents"
CONTAINER = "chassis"

# Runs in the chassis container. stdin and stdout are the client's bytes.
POD_SIDE = r"""
import os, socket, sys, threading
s = socket.create_connection((os.environ["POD_IP"], 8080), timeout=30)
s.settimeout(None)
def up():
    try:
        while True:
            data = os.read(0, 65536)
            if not data:
                break
            s.sendall(data)
    except OSError:
        pass
    try:
        s.shutdown(socket.SHUT_WR)
    except OSError:
        pass
threading.Thread(target=up, daemon=True).start()
while True:
    data = s.recv(65536)
    if not data:
        break
    os.write(1, data)
"""


def exec_command(chassis: str) -> list[str]:
    """The kubectl command for one connection. The context is pinned; nothing secret is in it."""
    return [
        "kubectl",
        "--context",
        CONTEXT,
        "-n",
        NAMESPACE,
        "exec",
        "-i",
        f"deploy/{chassis}",
        "-c",
        CONTAINER,
        "--",
        "python",
        "-c",
        POD_SIDE,
    ]


def _pump_in(conn: socket.socket, proc: subprocess.Popen[bytes]) -> None:
    assert proc.stdin is not None
    try:
        while True:
            data = conn.recv(65536)
            if not data:
                break
            proc.stdin.write(data)
            proc.stdin.flush()
    except OSError:
        pass
    finally:
        with contextlib.suppress(OSError):
            proc.stdin.close()


def serve_one(conn: socket.socket, chassis: str) -> None:
    proc = subprocess.Popen(
        exec_command(chassis),
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
    )
    threading.Thread(target=_pump_in, args=(conn, proc), daemon=True).start()
    assert proc.stdout is not None
    try:
        while True:
            data = os.read(proc.stdout.fileno(), 65536)
            if not data:
                break
            conn.sendall(data)
    except OSError:
        pass
    finally:
        proc.kill()
        proc.wait()
        conn.close()


def main(argv: list[str]) -> int:
    if len(argv) != 2 or not argv[1].replace("-", "").isalnum():
        print("usage: relay.py CHASSIS_DEPLOYMENT", file=sys.stderr)
        return 2
    chassis = argv[1]
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind(("127.0.0.1", 0))
    server.listen(8)
    print(f"port {server.getsockname()[1]}", flush=True)
    while True:
        conn, _ = server.accept()
        threading.Thread(target=serve_one, args=(conn, chassis), daemon=True).start()


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
